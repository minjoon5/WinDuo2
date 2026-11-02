// WinDuo2's native renderer: the whole effect, from capture to screen, on the GPU.
//
// The Python renderer moved every frame through system memory three times:
// Desktop Duplication copied the screen out of video memory, the frame was
// uploaded back into an OpenGL texture, and the translucent Qt window was read
// back again for DWM. On a 120 Hz panel that no longer fit in a frame. Here:
//
// - Desktop Duplication hands over a texture that stays on the GPU. It is
//   copied, scaled into the padded picture texture, and its blur pyramid built,
//   all by draw calls.
// - The overlay is a DirectComposition visual over a flip-model swap chain with
//   premultiplied alpha. DWM composes it directly; nothing is read back.
// - Fades are DirectComposition opacity animations, so DWM runs them and the
//   app does not have to keep drawing to fade.
//
// The shaders are ports of winduo2/render/shaders.py and must stay in step
// with them. Python drives everything through the plain C API at the bottom,
// with ctypes; see winduo2/render/native.py.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <d3d11.h>
#include <d3dcompiler.h>
#include <dcomp.h>
#include <dxgi1_2.h>
#include <dxgi1_3.h>
#include <wrl/client.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <vector>

using Microsoft::WRL::ComPtr;

#define WD_API extern "C" __declspec(dllexport)

// Bumped whenever the C API or WdParams changes, so a stale DLL is refused.
static const int WD_VERSION = 2;

// ---------------------------------------------------------------------------
// The C API's types. Mirrored by ctypes structures in winduo2/render/native.py.

#define WD_PROFILE_SAMPLES 128

struct WdParams {
    float profile[WD_PROFILE_SAMPLES * 2];  // (picture row, scale) per entry
    float profile_end;
    float profile_slope;
    float max_radius;  // blur radius at full strength, in texture pixels
    float blur_strength;
    float blur_floor;
    float max_dim;
    float dim_strength;
    float dim_floor;
    float dim_reach;
    float hinge_glow;
    float reflection_intensity;
    float turn;
    float picture_fade;
};

struct WdInfo {
    int screen_width;  // the display, in pixels
    int screen_height;
    int picture_width;  // the captured picture after the width cap
    int picture_height;
    int texture_width;  // the picture plus its black margin
    int texture_height;
    int inset;  // margin, in texture pixels
    int max_level;
    int rotation;  // DXGI_MODE_ROTATION of the output; 1 is upright
    int excluded;  // the overlay is kept out of screen capture
};

struct WdTimings {
    float capture_ms;  // acquiring, copying, scaling and the pyramid
    float render_ms;   // drawing the effect
    float present_ms;  // handing the frame to DWM
};

// The layouts ctypes mirrors; tests/test_native.py checks the same sizes.
static_assert(sizeof(WdParams) == 1076, "WdParams layout changed: bump WD_VERSION");
static_assert(sizeof(WdInfo) == 40, "WdInfo layout changed: bump WD_VERSION");
static_assert(sizeof(WdTimings) == 12, "WdTimings layout changed: bump WD_VERSION");

// wd_frame's result bits.
// wd_frame's ``flags``.
enum {
    WD_WANT_PICTURE = 1,  // pull a new desktop image first, if there is one
    WD_MUST_PRESENT = 2,  // present even if that means waiting
};

enum {
    WD_DREW = 1,
    WD_NEW_PICTURE = 2,
    WD_HAS_PICTURE = 4,
    WD_CAPTURE_LOST = 8,
};

// ---------------------------------------------------------------------------
// Errors. One message, the last; the API is driven from one thread.

static char g_error[512] = "";

static void set_error(const char* format, ...) {
    va_list args;
    va_start(args, format);
    vsnprintf(g_error, sizeof g_error, format, args);
    va_end(args);
}

#define CHECK(expr, what)                                              \
    do {                                                               \
        HRESULT hr_ = (expr);                                          \
        if (FAILED(hr_)) {                                             \
            set_error("%s failed (0x%08lx)", what, (unsigned long)hr_); \
            return false;                                              \
        }                                                              \
    } while (0)

static double now_ms() {
    static LARGE_INTEGER frequency = {};
    if (!frequency.QuadPart) QueryPerformanceFrequency(&frequency);
    LARGE_INTEGER counter;
    QueryPerformanceCounter(&counter);
    return (double)counter.QuadPart * 1000.0 / (double)frequency.QuadPart;
}

// ---------------------------------------------------------------------------
// Shaders. Ports of winduo2/render/shaders.py; keep the two in step.

static const char* SHADER_SOURCE = R"HLSL(
// A single oversized triangle covering the viewport, needing no vertex buffer.
float4 vs_main(uint id : SV_VertexID) : SV_Position {
    float2 corners[3] = { float2(-1.0, -3.0), float2(-1.0, 1.0), float2(3.0, 1.0) };
    return float4(corners[id], 0.0, 1.0);
}

SamplerState linearClamp : register(s0);

// --- Scaling the captured screen into the padded picture -------------------

Texture2D desktop : register(t0);

cbuffer ScaleConstants : register(b0) {
    float2 targetOrigin;  // the picture's top-left in the texture, in pixels
    float2 targetSize;    // the picture's size, in pixels
    float2 sourceTexel;   // one destination pixel, in source texture coordinates
    float2 scalePad;
};

// Four taps a quarter of a destination pixel apart: close to an area filter
// for the downscales this does, where one bilinear tap would alias.
float4 ps_scale(float4 position : SV_Position) : SV_Target {
    float2 uv = (position.xy - targetOrigin) / targetSize;
    float2 q = sourceTexel * 0.25;
    float3 colour = desktop.SampleLevel(linearClamp, uv + float2(-q.x, -q.y), 0).rgb
                  + desktop.SampleLevel(linearClamp, uv + float2( q.x, -q.y), 0).rgb
                  + desktop.SampleLevel(linearClamp, uv + float2(-q.x,  q.y), 0).rgb
                  + desktop.SampleLevel(linearClamp, uv + float2( q.x,  q.y), 0).rgb;
    return float4(colour * 0.25, 1.0);
}

// A solid colour, for the black stand-in held over a shut lid.
cbuffer FillConstants : register(b0) {
    float4 fillColour;
};

float4 ps_fill(float4 position : SV_Position) : SV_Target {
    return fillColour;
}

// --- The blur pyramid -------------------------------------------------------
// The 13-tap downsample from Jimenez, "Next Generation Post Processing in Call
// of Duty" (SIGGRAPH 2014), as in DOWNSAMPLE.

Texture2D source : register(t0);

cbuffer DownsampleConstants : register(b0) {
    float2 sourceTexelSize;  // one source texel, in texture coordinates
    float2 levelSize;        // the level being written, in texels
};

float3 tap(float2 uv, float2 offset) {
    return source.SampleLevel(linearClamp, uv + offset * sourceTexelSize, 0).rgb;
}

float4 ps_downsample(float4 position : SV_Position) : SV_Target {
    float2 uv = position.xy / levelSize;
    float3 a = tap(uv, float2(-2.0, -2.0));
    float3 b = tap(uv, float2( 0.0, -2.0));
    float3 c = tap(uv, float2( 2.0, -2.0));
    float3 d = tap(uv, float2(-2.0,  0.0));
    float3 e = tap(uv, float2( 0.0,  0.0));
    float3 f = tap(uv, float2( 2.0,  0.0));
    float3 g = tap(uv, float2(-2.0,  2.0));
    float3 h = tap(uv, float2( 0.0,  2.0));
    float3 i = tap(uv, float2( 2.0,  2.0));
    float3 j = tap(uv, float2(-1.0, -1.0));
    float3 k = tap(uv, float2( 1.0, -1.0));
    float3 l = tap(uv, float2(-1.0,  1.0));
    float3 m = tap(uv, float2( 1.0,  1.0));
    float3 colour = e * 0.125
        + (a + c + g + i) * 0.03125
        + (b + d + f + h) * 0.0625
        + (j + k + l + m) * 0.125;
    return float4(colour, 1.0);
}

// --- The effect -------------------------------------------------------------
// FRAGMENT, line for line where it can be. Differences from GLSL: SV_Position
// runs y-down from the top left, so the screen point is flipped to the y-up
// convention the geometry uses; the profile is packed two entries to a float4,
// because a constant buffer array pads every element to 16 bytes.

#define PROFILE_SAMPLES 128

Texture2D picture : register(t0);

cbuffer EffectConstants : register(b0) {
    float4 uProfile4[PROFILE_SAMPLES / 2];
    float2 uScreenSize;
    float2 uPaddedOrigin;
    float2 uPaddedSize;
    float uProfileEnd;
    float uProfileSlope;
    float uMaxRadius;
    float uBlurStrength;
    float uBlurFloor;
    float uMaxLevel;
    float uMaxDim;
    float uDimStrength;
    float uDimFloor;
    float uDimReach;
    float uHingeGlow;
    float uReflectionIntensity;
    float uTurn;
    float uPictureFade;
};

float2 profileEntry(int i) {
    float4 pair = uProfile4[i >> 1];
    return (i & 1) ? pair.zw : pair.xy;
}

float3 bsplineLevel(float2 uv, float lod) {
    uint width, height, levels;
    picture.GetDimensions((uint)lod, width, height, levels);
    float2 size = float2(width, height);
    float2 texel = uv * size - 0.5;
    float2 f = frac(texel);
    float2 base = texel - f;
    float2 f2 = f * f;
    float2 f3 = f2 * f;
    float2 w0 = (1.0 - 3.0 * f + 3.0 * f2 - f3) / 6.0;
    float2 w1 = (4.0 - 6.0 * f2 + 3.0 * f3) / 6.0;
    float2 w2 = (1.0 + 3.0 * f + 3.0 * f2 - 3.0 * f3) / 6.0;
    float2 w3 = f3 / 6.0;
    float2 g0 = w0 + w1;
    float2 g1 = w2 + w3;
    float2 h0 = (base - 0.5 + w1 / g0) / size;
    float2 h1 = (base + 1.5 + w3 / g1) / size;
    return g0.y * (g0.x * picture.SampleLevel(linearClamp, float2(h0.x, h0.y), lod).rgb
                 + g1.x * picture.SampleLevel(linearClamp, float2(h1.x, h0.y), lod).rgb)
         + g1.y * (g0.x * picture.SampleLevel(linearClamp, float2(h0.x, h1.y), lod).rgb
                 + g1.x * picture.SampleLevel(linearClamp, float2(h1.x, h1.y), lod).rgb);
}

float3 softBlur(float2 uv, float level) {
    level -= 1.0;
    if (level <= 0.0) {
        return picture.SampleLevel(linearClamp, uv, max(level + 1.0, 0.0)).rgb;
    }
    float low = floor(level);
    float high = min(low + 1.0, uMaxLevel);
    return lerp(bsplineLevel(uv, low), bsplineLevel(uv, high), level - low);
}

float3 linearToSrgb(float3 linearColour) {
    float3 low = linearColour * 12.92;
    float3 high = 1.055 * pow(max(linearColour, 0.0), 1.0 / 2.4) - 0.055;
    return lerp(low, high, step(0.0031308, linearColour));
}

float4 ps_effect(float4 position : SV_Position) : SV_Target {
    float2 screenPoint = float2(position.x, uScreenSize.y - position.y);

    float2 entry;
    if (screenPoint.y >= uProfileEnd) {
        entry = profileEntry(PROFILE_SAMPLES - 1);
        entry.x += (screenPoint.y - uProfileEnd) * uProfileSlope;
    } else {
        float position01 = max(screenPoint.y, 0.0) / uProfileEnd * float(PROFILE_SAMPLES - 1);
        int i = min(int(position01), PROFILE_SAMPLES - 2);
        entry = lerp(profileEntry(i), profileEntry(i + 1), position01 - float(i));
    }
    float centre = uScreenSize.x * 0.5;
    float2 picturePoint = float2(
        centre + (screenPoint.x - centre) / max(entry.y, 1e-4),
        entry.x * uScreenSize.y
    );

    float2 unit = (picturePoint - uPaddedOrigin) / uPaddedSize;
    if (unit.x < 0.0 || unit.x > 1.0 || unit.y < 0.0 || unit.y > 1.0) {
        return float4(0.0, 0.0, 0.0, 1.0);
    }
    // Texture row 0 is the top of the screen; picture y runs up.
    float2 texCoord = float2(unit.x, 1.0 - unit.y);
    float2 pictureUnit = picturePoint / uScreenSize;
    float height = saturate(pictureUnit.y);
    float2 inside = step(0.0, pictureUnit) * step(pictureUnit, 1.0);
    float onPicture = inside.x * inside.y;

    float blur = uBlurStrength * (uBlurFloor + (1.0 - uBlurFloor) * height);
    float level = clamp(log2(max(blur * uMaxRadius, 1.0)), 0.0, uMaxLevel);

    float3 colour = softBlur(texCoord, level);

    float spread = smoothstep(0.0, max(uDimReach, 0.02), height);
    float fade = uDimStrength * (uDimFloor + (1.0 - uDimFloor) * spread);
    colour *= pow(1.0 - uMaxDim * fade, 2.2);

    float hingeLine = exp(-pow(height / 0.05, 2.0)) * uTurn * onPicture;
    colour += float3(0.92, 0.95, 0.97) * hingeLine * uHingeGlow * 0.006;

    float reflectionBand = exp(-pow((height - 0.65) / 0.28, 2.0)) * uTurn * onPicture;
    colour += float3(0.85, 0.88, 0.9) * reflectionBand * uReflectionIntensity * 0.004;

    colour = saturate(colour);
    // Opaque: the window's fade is DirectComposition's, not the shader's.
    return float4(linearToSrgb(colour) * uPictureFade, 1.0);
}
)HLSL";

struct EffectConstants {
    float profile[WD_PROFILE_SAMPLES * 2];
    float screen_size[2];
    float padded_origin[2];
    float padded_size[2];
    float profile_end;
    float profile_slope;
    float max_radius;
    float blur_strength;
    float blur_floor;
    float max_level;
    float max_dim;
    float dim_strength;
    float dim_floor;
    float dim_reach;
    float hinge_glow;
    float reflection_intensity;
    float turn;
    float picture_fade;
};
static_assert(sizeof(EffectConstants) % 16 == 0, "constant buffers are 16-byte multiples");

struct ScaleConstants {
    float target_origin[2];
    float target_size[2];
    float source_texel[2];
    float pad[2];
};

struct DownsampleConstants {
    float source_texel[2];
    float level_size[2];
};

struct FillConstants {
    float colour[4];
};

// ---------------------------------------------------------------------------
// The picture: the captured screen scaled down, inside a black margin, with a
// blur pyramid in its mip levels. Shared by the renderer and the self-test.

// PADDING_POINTS in overlay.py: the margin, in screen pixels, on every side.
static const float PADDING_POINTS = 400.0f;

struct Pipeline {
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;

    ComPtr<ID3D11VertexShader> vs;
    ComPtr<ID3D11PixelShader> ps_scale, ps_fill, ps_downsample, ps_effect;
    ComPtr<ID3D11SamplerState> sampler;
    ComPtr<ID3D11RasterizerState> raster;
    ComPtr<ID3D11BlendState> no_blend;
    ComPtr<ID3D11Buffer> cb_scale, cb_fill, cb_down, cb_effect;

    // The geometry, as texture_layout() in overlay.py computes it.
    int screen_w = 0, screen_h = 0;
    int picture_w = 0, picture_h = 0;
    int texture_w = 0, texture_h = 0;
    int inset = 0;
    int levels = 1;
    float padded_origin[2] = {0, 0};
    float padded_size[2] = {0, 0};

    // The padded picture, sRGB, with its whole mip chain.
    ComPtr<ID3D11Texture2D> picture;
    ComPtr<ID3D11ShaderResourceView> picture_srv;
    std::vector<ComPtr<ID3D11ShaderResourceView>> level_srv;
    std::vector<ComPtr<ID3D11RenderTargetView>> level_rtv;

    // A copy of the duplicated desktop, viewable as sRGB.
    ComPtr<ID3D11Texture2D> desktop;
    ComPtr<ID3D11ShaderResourceView> desktop_srv;
    int desktop_w = 0, desktop_h = 0;

    bool has_picture = false;

    bool compile(const char* entry, const char* profile, ComPtr<ID3DBlob>& blob) {
        ComPtr<ID3DBlob> errors;
        HRESULT hr = D3DCompile(SHADER_SOURCE, strlen(SHADER_SOURCE), "winduo2_native", nullptr,
                                nullptr, entry, profile, D3DCOMPILE_OPTIMIZATION_LEVEL3, 0,
                                &blob, &errors);
        if (FAILED(hr)) {
            set_error("shader %s did not compile: %s", entry,
                      errors ? (const char*)errors->GetBufferPointer() : "no message");
            return false;
        }
        return true;
    }

    bool pixel(const char* entry, ComPtr<ID3D11PixelShader>& out) {
        ComPtr<ID3DBlob> blob;
        if (!compile(entry, "ps_5_0", blob)) return false;
        CHECK(device->CreatePixelShader(blob->GetBufferPointer(), blob->GetBufferSize(), nullptr,
                                        &out),
              entry);
        return true;
    }

    bool buffer(UINT size, ComPtr<ID3D11Buffer>& out) {
        D3D11_BUFFER_DESC desc = {};
        desc.ByteWidth = (size + 15) & ~15u;
        desc.Usage = D3D11_USAGE_DEFAULT;
        desc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        CHECK(device->CreateBuffer(&desc, nullptr, &out), "CreateBuffer");
        return true;
    }

    bool init_shaders() {
        ComPtr<ID3DBlob> blob;
        if (!compile("vs_main", "vs_5_0", blob)) return false;
        CHECK(device->CreateVertexShader(blob->GetBufferPointer(), blob->GetBufferSize(), nullptr,
                                         &vs),
              "CreateVertexShader");
        if (!pixel("ps_scale", ps_scale) || !pixel("ps_fill", ps_fill) ||
            !pixel("ps_downsample", ps_downsample) || !pixel("ps_effect", ps_effect))
            return false;

        D3D11_SAMPLER_DESC sd = {};
        sd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        sd.MaxLOD = D3D11_FLOAT32_MAX;
        CHECK(device->CreateSamplerState(&sd, &sampler), "CreateSamplerState");

        D3D11_RASTERIZER_DESC rd = {};
        rd.FillMode = D3D11_FILL_SOLID;
        rd.CullMode = D3D11_CULL_NONE;
        rd.DepthClipEnable = TRUE;
        CHECK(device->CreateRasterizerState(&rd, &raster), "CreateRasterizerState");

        D3D11_BLEND_DESC bd = {};
        bd.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
        CHECK(device->CreateBlendState(&bd, &no_blend), "CreateBlendState");

        return buffer(sizeof(ScaleConstants), cb_scale) && buffer(sizeof(FillConstants), cb_fill) &&
               buffer(sizeof(DownsampleConstants), cb_down) &&
               buffer(sizeof(EffectConstants), cb_effect);
    }

    // texture_layout() from overlay.py, so both renderers draw the same.
    void layout(int sw, int sh, int width_cap) {
        screen_w = sw;
        screen_h = sh;
        picture_w = sw;
        picture_h = sh;
        if (width_cap > 0 && sw > width_cap) {
            picture_w = width_cap;
            picture_h = std::max(2, (int)std::lround(sh * (double)width_cap / sw) / 2 * 2);
        }
        double scale_x = (double)picture_w / sw;
        double scale_y = (double)picture_h / sh;
        inset = (int)std::lround(PADDING_POINTS * scale_x);
        texture_w = std::max(picture_w + 2 * inset, 2);
        texture_h = std::max(picture_h + 2 * inset, 2);
        double pad_x = inset / scale_x;
        double pad_y = inset / scale_y;
        padded_origin[0] = (float)-pad_x;
        padded_origin[1] = (float)-pad_y;
        padded_size[0] = (float)(sw + 2 * pad_x);
        padded_size[1] = (float)(sh + 2 * pad_y);
        levels = (int)std::floor(std::log2((double)std::max(texture_w, texture_h))) + 1;
    }

    bool create_picture() {
        D3D11_TEXTURE2D_DESC td = {};
        td.Width = texture_w;
        td.Height = texture_h;
        td.MipLevels = levels;
        td.ArraySize = 1;
        td.Format = DXGI_FORMAT_B8G8R8A8_TYPELESS;
        td.SampleDesc.Count = 1;
        td.Usage = D3D11_USAGE_DEFAULT;
        td.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
        CHECK(device->CreateTexture2D(&td, nullptr, &picture), "CreateTexture2D (picture)");

        D3D11_SHADER_RESOURCE_VIEW_DESC sv = {};
        sv.Format = DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
        sv.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        sv.Texture2D.MipLevels = levels;
        CHECK(device->CreateShaderResourceView(picture.Get(), &sv, &picture_srv), "picture SRV");

        level_srv.assign(levels, nullptr);
        level_rtv.assign(levels, nullptr);
        for (int level = 0; level < levels; ++level) {
            D3D11_SHADER_RESOURCE_VIEW_DESC lv = sv;
            lv.Texture2D.MostDetailedMip = level;
            lv.Texture2D.MipLevels = 1;
            CHECK(device->CreateShaderResourceView(picture.Get(), &lv, &level_srv[level]),
                  "level SRV");
            D3D11_RENDER_TARGET_VIEW_DESC rv = {};
            rv.Format = DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
            rv.ViewDimension = D3D11_RTV_DIMENSION_TEXTURE2D;
            rv.Texture2D.MipSlice = level;
            CHECK(device->CreateRenderTargetView(picture.Get(), &rv, &level_rtv[level]),
                  "level RTV");
        }
        // Black, so the margin is black from the start; only the interior is
        // ever drawn over.
        const float clear[4] = {0, 0, 0, 0};
        for (auto& rtv : level_rtv) context->ClearRenderTargetView(rtv.Get(), clear);
        has_picture = false;
        return true;
    }

    // The desktop copy, typeless so it can be read as sRGB. Recreated when the
    // duplicated desktop's size changes.
    bool ensure_desktop(int width, int height) {
        if (desktop && desktop_w == width && desktop_h == height) return true;
        desktop.Reset();
        desktop_srv.Reset();
        D3D11_TEXTURE2D_DESC td = {};
        td.Width = width;
        td.Height = height;
        td.MipLevels = 1;
        td.ArraySize = 1;
        td.Format = DXGI_FORMAT_B8G8R8A8_TYPELESS;
        td.SampleDesc.Count = 1;
        td.Usage = D3D11_USAGE_DEFAULT;
        td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        CHECK(device->CreateTexture2D(&td, nullptr, &desktop), "CreateTexture2D (desktop)");
        D3D11_SHADER_RESOURCE_VIEW_DESC sv = {};
        sv.Format = DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
        sv.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        sv.Texture2D.MipLevels = 1;
        CHECK(device->CreateShaderResourceView(desktop.Get(), &sv, &desktop_srv), "desktop SRV");
        desktop_w = width;
        desktop_h = height;
        return true;
    }

    void begin_pass(ID3D11RenderTargetView* target, float x, float y, float w, float h,
                    ID3D11PixelShader* ps, ID3D11Buffer* cb) {
        ID3D11ShaderResourceView* none[1] = {nullptr};
        context->PSSetShaderResources(0, 1, none);
        context->OMSetRenderTargets(1, &target, nullptr);
        D3D11_VIEWPORT viewport = {x, y, w, h, 0.0f, 1.0f};
        context->RSSetViewports(1, &viewport);
        context->RSSetState(raster.Get());
        context->OMSetBlendState(no_blend.Get(), nullptr, 0xffffffff);
        context->IASetInputLayout(nullptr);
        context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        context->VSSetShader(vs.Get(), nullptr, 0);
        context->PSSetShader(ps, nullptr, 0);
        context->PSSetConstantBuffers(0, 1, &cb);
        ID3D11SamplerState* samplers[1] = {sampler.Get()};
        context->PSSetSamplers(0, 1, samplers);
    }

    void draw_with(ID3D11ShaderResourceView* srv) {
        context->PSSetShaderResources(0, 1, &srv);
        context->Draw(3, 0);
        ID3D11ShaderResourceView* none[1] = {nullptr};
        context->PSSetShaderResources(0, 1, none);
        ID3D11RenderTargetView* no_target[1] = {nullptr};
        context->OMSetRenderTargets(1, no_target, nullptr);
    }

    // The desktop copy, scaled into the picture's interior, then the pyramid.
    void scale_desktop_into_picture() {
        ScaleConstants sc = {};
        sc.target_origin[0] = sc.target_origin[1] = (float)inset;
        sc.target_size[0] = (float)picture_w;
        sc.target_size[1] = (float)picture_h;
        sc.source_texel[0] = 1.0f / (float)picture_w;
        sc.source_texel[1] = 1.0f / (float)picture_h;
        context->UpdateSubresource(cb_scale.Get(), 0, nullptr, &sc, 0, 0);
        begin_pass(level_rtv[0].Get(), (float)inset, (float)inset, (float)picture_w,
                   (float)picture_h, ps_scale.Get(), cb_scale.Get());
        draw_with(desktop_srv.Get());
        build_pyramid();
        has_picture = true;
    }

    // The interior filled with opaque black: the stand-in held over a shut lid.
    void fill_picture_black() {
        FillConstants fc = {{0.0f, 0.0f, 0.0f, 1.0f}};
        context->UpdateSubresource(cb_fill.Get(), 0, nullptr, &fc, 0, 0);
        begin_pass(level_rtv[0].Get(), (float)inset, (float)inset, (float)picture_w,
                   (float)picture_h, ps_fill.Get(), cb_fill.Get());
        context->Draw(3, 0);
        ID3D11RenderTargetView* no_target[1] = {nullptr};
        context->OMSetRenderTargets(1, no_target, nullptr);
        build_pyramid();
        has_picture = true;
    }

    void build_pyramid() {
        int source_w = texture_w, source_h = texture_h;
        for (int level = 1; level < levels; ++level) {
            int w = std::max(texture_w >> level, 1);
            int h = std::max(texture_h >> level, 1);
            DownsampleConstants dc = {{1.0f / (float)source_w, 1.0f / (float)source_h}, {(float)w, (float)h}};
            context->UpdateSubresource(cb_down.Get(), 0, nullptr, &dc, 0, 0);
            begin_pass(level_rtv[level].Get(), 0, 0, (float)w, (float)h, ps_downsample.Get(),
                       cb_down.Get());
            draw_with(level_srv[level - 1].Get());
            source_w = w;
            source_h = h;
        }
    }

    // The effect, into ``target`` (screen-sized).
    void draw_effect(ID3D11RenderTargetView* target, const WdParams& p) {
        EffectConstants ec = {};
        memcpy(ec.profile, p.profile, sizeof ec.profile);
        ec.screen_size[0] = (float)screen_w;
        ec.screen_size[1] = (float)screen_h;
        memcpy(ec.padded_origin, padded_origin, sizeof padded_origin);
        memcpy(ec.padded_size, padded_size, sizeof padded_size);
        ec.profile_end = p.profile_end;
        ec.profile_slope = p.profile_slope;
        ec.max_radius = p.max_radius;
        ec.blur_strength = p.blur_strength;
        ec.blur_floor = p.blur_floor;
        ec.max_level = (float)(levels - 1);
        ec.max_dim = p.max_dim;
        ec.dim_strength = p.dim_strength;
        ec.dim_floor = p.dim_floor;
        ec.dim_reach = p.dim_reach;
        ec.hinge_glow = p.hinge_glow;
        ec.reflection_intensity = p.reflection_intensity;
        ec.turn = p.turn;
        ec.picture_fade = p.picture_fade;
        context->UpdateSubresource(cb_effect.Get(), 0, nullptr, &ec, 0, 0);
        begin_pass(target, 0, 0, (float)screen_w, (float)screen_h, ps_effect.Get(),
                   cb_effect.Get());
        draw_with(picture_srv.Get());
    }
};

// ---------------------------------------------------------------------------
// The overlay window. Click-through, out of Alt-Tab, topmost, never activated,
// and kept out of every screen capture, Desktop Duplication included.

static const wchar_t* WINDOW_CLASS = L"WinDuo2NativeOverlay";

static LRESULT CALLBACK overlay_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    switch (message) {
        case WM_NCHITTEST:
            return HTTRANSPARENT;
        case WM_MOUSEACTIVATE:
            return MA_NOACTIVATE;
        case WM_ERASEBKGND:
            return 1;
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}

static bool register_class() {
    static bool registered = false;
    if (registered) return true;
    WNDCLASSEXW wc = {};
    wc.cbSize = sizeof wc;
    wc.lpfnWndProc = overlay_proc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = WINDOW_CLASS;
    if (!RegisterClassExW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        set_error("RegisterClassEx failed (%lu)", GetLastError());
        return false;
    }
    registered = true;
    return true;
}

// How long a picture may be waited for without a real desktop frame before
// an empty one is taken as the picture anyway.
static const double EMPTY_FRAME_GRACE_MS = 250.0;

#ifndef WDA_EXCLUDEFROMCAPTURE
#define WDA_EXCLUDEFROMCAPTURE 0x00000011
#endif

// ---------------------------------------------------------------------------
// The renderer.

struct Renderer {
    Pipeline pipe;
    HWND hwnd = nullptr;
    HMONITOR monitor = nullptr;
    RECT monitor_rect = {};
    int width_cap = 0;
    bool excluded = false;

    ComPtr<IDXGIAdapter1> adapter;
    ComPtr<IDXGIOutput1> output;
    ComPtr<IDXGIOutputDuplication> duplication;
    int rotation = DXGI_MODE_ROTATION_IDENTITY;
    // When a picture was first asked for and none was there, and whether the
    // desktop copy holds an empty frame kept in case no real one comes.
    double wanted_since_ms = 0.0;
    bool held_empty = false;

    ComPtr<IDXGISwapChain1> swapchain;
    // Signalled when the swap chain can take another frame without waiting:
    // once per vertical blank while frames are being presented.
    HANDLE frame_ready = nullptr;
    // A frame_ready signal taken by wd_wait and not yet spent on a present.
    // Waiting again would lose it, and with one frame of latency a lost
    // signal never comes back; so it is held until a present uses it.
    std::atomic<bool> permit{false};
    ComPtr<IDCompositionDevice> dcomp;
    ComPtr<IDCompositionTarget> target;
    ComPtr<IDCompositionVisual> visual;
    ComPtr<IDCompositionEffectGroup> effects;

    ~Renderer() {
        duplication.Reset();
        if (frame_ready) CloseHandle(frame_ready);
        if (hwnd) DestroyWindow(hwnd);
    }

    // The primary display's output, and the adapter that drives it. Desktop
    // Duplication only works on a device made on that adapter.
    bool find_output() {
        POINT origin = {0, 0};
        monitor = MonitorFromPoint(origin, MONITOR_DEFAULTTOPRIMARY);
        ComPtr<IDXGIFactory1> factory;
        CHECK(CreateDXGIFactory1(IID_PPV_ARGS(&factory)), "CreateDXGIFactory1");
        ComPtr<IDXGIAdapter1> candidate;
        for (UINT a = 0; factory->EnumAdapters1(a, &candidate) != DXGI_ERROR_NOT_FOUND; ++a) {
            ComPtr<IDXGIOutput> out;
            for (UINT o = 0; candidate->EnumOutputs(o, &out) != DXGI_ERROR_NOT_FOUND; ++o) {
                DXGI_OUTPUT_DESC desc;
                if (SUCCEEDED(out->GetDesc(&desc)) && desc.Monitor == monitor) {
                    adapter = candidate;
                    CHECK(out.As(&output), "IDXGIOutput1");
                    monitor_rect = desc.DesktopCoordinates;
                    rotation = desc.Rotation;
                    return true;
                }
                out.Reset();
            }
            candidate.Reset();
        }
        set_error("no adapter drives the primary display");
        return false;
    }

    bool create_device() {
        D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
        UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
        HRESULT hr = D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, flags,
                                       levels, 2, D3D11_SDK_VERSION, &pipe.device, nullptr,
                                       &pipe.context);
        if (FAILED(hr))
            hr = D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, flags,
                                   levels + 1, 1, D3D11_SDK_VERSION, &pipe.device, nullptr,
                                   &pipe.context);
        CHECK(hr, "D3D11CreateDevice");
        return true;
    }

    bool create_window() {
        if (!register_class()) return false;
        int x = monitor_rect.left, y = monitor_rect.top;
        int w = monitor_rect.right - monitor_rect.left, h = monitor_rect.bottom - monitor_rect.top;
        hwnd = CreateWindowExW(WS_EX_NOREDIRECTIONBITMAP | WS_EX_TOPMOST | WS_EX_TOOLWINDOW |
                                   WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TRANSPARENT,
                               WINDOW_CLASS, L"WinDuo2 overlay", WS_POPUP, x, y, w, h, nullptr,
                               nullptr, GetModuleHandleW(nullptr), nullptr);
        if (!hwnd) {
            set_error("CreateWindowEx failed (%lu)", GetLastError());
            return false;
        }
        // Layered and transparent is what passes the mouse straight through;
        // fully opaque layering leaves the DirectComposition content as it is.
        SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA);
        excluded = SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE) != FALSE;
        ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW);
        return true;
    }

    bool create_swapchain() {
        ComPtr<IDXGIDevice> dxgi_device;
        CHECK(pipe.device.As(&dxgi_device), "IDXGIDevice");
        ComPtr<IDXGIFactory2> factory;
        CHECK(adapter->GetParent(IID_PPV_ARGS(&factory)), "IDXGIFactory2");

        DXGI_SWAP_CHAIN_DESC1 desc = {};
        desc.Width = pipe.screen_w;
        desc.Height = pipe.screen_h;
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        desc.SampleDesc.Count = 1;
        desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        desc.BufferCount = 2;
        desc.Scaling = DXGI_SCALING_STRETCH;
        desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL;
        desc.AlphaMode = DXGI_ALPHA_MODE_PREMULTIPLIED;
        desc.Flags = DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT;
        CHECK(factory->CreateSwapChainForComposition(pipe.device.Get(), &desc, nullptr,
                                                     &swapchain),
              "CreateSwapChainForComposition");

        // One frame of latency, and a handle that says when to draw it. The
        // frame loop waits on that rather than on a timer, so every frame is
        // drawn just after a vertical blank and presenting never blocks.
        ComPtr<IDXGISwapChain2> swapchain2;
        CHECK(swapchain.As(&swapchain2), "IDXGISwapChain2");
        CHECK(swapchain2->SetMaximumFrameLatency(1), "SetMaximumFrameLatency");
        frame_ready = swapchain2->GetFrameLatencyWaitableObject();

        CHECK(DCompositionCreateDevice(dxgi_device.Get(), IID_PPV_ARGS(&dcomp)),
              "DCompositionCreateDevice");
        CHECK(dcomp->CreateTargetForHwnd(hwnd, TRUE, &target), "CreateTargetForHwnd");
        CHECK(dcomp->CreateVisual(&visual), "CreateVisual");
        CHECK(visual->SetContent(swapchain.Get()), "SetContent");
        CHECK(dcomp->CreateEffectGroup(&effects), "CreateEffectGroup");
        CHECK(effects->SetOpacity(0.0f), "SetOpacity");
        CHECK(visual->SetEffect(effects.Get()), "SetEffect");
        CHECK(target->SetRoot(visual.Get()), "SetRoot");
        CHECK(dcomp->Commit(), "Commit");
        return present_clear();
    }

    // A fully transparent frame: nothing of the effect on screen.
    bool present_clear() {
        ComPtr<ID3D11Texture2D> back;
        CHECK(swapchain->GetBuffer(0, IID_PPV_ARGS(&back)), "GetBuffer");
        ComPtr<ID3D11RenderTargetView> rtv;
        CHECK(pipe.device->CreateRenderTargetView(back.Get(), nullptr, &rtv), "back buffer RTV");
        const float clear[4] = {0, 0, 0, 0};
        pipe.context->ClearRenderTargetView(rtv.Get(), clear);
        HRESULT hr = swapchain->Present(0, 0);
        permit = false;
        if (FAILED(hr)) {
            set_error("Present failed (0x%08lx)", (unsigned long)hr);
            return false;
        }
        return true;
    }

    bool start_capture() {
        if (duplication) return true;
        HRESULT hr = output->DuplicateOutput(pipe.device.Get(), &duplication);
        if (FAILED(hr)) {
            set_error("DuplicateOutput failed (0x%08lx)", (unsigned long)hr);
            duplication.Reset();
            return false;
        }
        DXGI_OUTDUPL_DESC desc;
        duplication->GetDesc(&desc);
        rotation = desc.Rotation;
        reset_wait();
        return true;
    }

    void stop_capture() { duplication.Reset(); }

    void reset_wait() {
        wanted_since_ms = 0.0;
        held_empty = false;
    }

    // While a picture is wanted and none has come, a transparent frame on the
    // overlay, which is still at zero opacity: it makes the compositor draw
    // the desktop again, and so hands Desktop Duplication a real frame.
    void nudge_compositor() {
        ComPtr<ID3D11Texture2D> back;
        if (FAILED(swapchain->GetBuffer(0, IID_PPV_ARGS(&back)))) return;
        ComPtr<ID3D11RenderTargetView> rtv;
        if (FAILED(pipe.device->CreateRenderTargetView(back.Get(), nullptr, &rtv))) return;
        const float clear[4] = {0, 0, 0, 0};
        pipe.context->ClearRenderTargetView(rtv.Get(), clear);
        if (SUCCEEDED(swapchain->Present(1, DXGI_PRESENT_DO_NOT_WAIT))) permit = false;
    }

    // The newest desktop image into the picture, if there is a new one.
    // Returns WD_NEW_PICTURE and/or WD_CAPTURE_LOST.
    int pull_picture() {
        if (!duplication && !start_capture()) return WD_CAPTURE_LOST;
        if (!pipe.has_picture && wanted_since_ms == 0.0) wanted_since_ms = now_ms();
        int result = take_frame();
        if (!pipe.has_picture && held_empty && !(result & WD_CAPTURE_LOST) &&
            now_ms() - wanted_since_ms >= EMPTY_FRAME_GRACE_MS) {
            pipe.scale_desktop_into_picture();
            result |= WD_NEW_PICTURE;
        }
        if (pipe.has_picture) reset_wait();
        return result;
    }

    int take_frame() {
        DXGI_OUTDUPL_FRAME_INFO info;
        ComPtr<IDXGIResource> resource;
        HRESULT hr = duplication->AcquireNextFrame(0, &info, &resource);
        if (hr == DXGI_ERROR_WAIT_TIMEOUT) return 0;
        if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_INVALID_CALL) {
            // A mode change, the secure desktop, or the session switching:
            // start over next frame. Starting over is quick.
            duplication.Reset();
            return WD_CAPTURE_LOST;
        }
        if (FAILED(hr)) {
            set_error("AcquireNextFrame failed (0x%08lx)", (unsigned long)hr);
            duplication.Reset();
            return WD_CAPTURE_LOST;
        }
        int result = 0;
        // A frame with no present time has no desktop image in it: a pointer
        // move, or the first frame of a new duplication, which after the
        // desktop has sat still for a while is empty and comes out black.
        // With no picture yet, that one is only kept aside, and taken once no
        // real frame has come for a while, as on a desktop where nothing
        // changes at all.
        bool real = info.LastPresentTime.QuadPart != 0;
        if (real || (!pipe.has_picture && !held_empty)) {
            ComPtr<ID3D11Texture2D> frame;
            if (SUCCEEDED(resource.As(&frame))) {
                D3D11_TEXTURE2D_DESC fd;
                frame->GetDesc(&fd);
                if (pipe.ensure_desktop((int)fd.Width, (int)fd.Height)) {
                    pipe.context->CopyResource(pipe.desktop.Get(), frame.Get());
                    if (real) {
                        pipe.scale_desktop_into_picture();
                        result |= WD_NEW_PICTURE;
                    } else {
                        held_empty = true;
                    }
                }
            }
        }
        duplication->ReleaseFrame();
        return result;
    }

    bool create(int cap) {
        width_cap = cap;
        if (!find_output() || !create_device() || !pipe.init_shaders()) return false;
        pipe.layout(monitor_rect.right - monitor_rect.left,
                    monitor_rect.bottom - monitor_rect.top, width_cap);
        return pipe.create_picture() && create_window() && create_swapchain();
    }

    // Waits up to ``timeout_ms`` for the swap chain to want a frame. 1 when it
    // does, 2 when an earlier signal is still unspent, 0 on timeout.
    int wait(int timeout_ms) {
        if (permit) return 2;
        if (!frame_ready) return 0;
        DWORD waited = WaitForSingleObjectEx(frame_ready, (DWORD)std::max(timeout_ms, 0), TRUE);
        if (waited != WAIT_OBJECT_0) return 0;
        permit = true;
        return 1;
    }

    int frame(const WdParams& params, int flags, WdTimings* timings) {
        int result = 0;
        double t0 = now_ms();
        if (flags & WD_WANT_PICTURE) result |= pull_picture();
        double t1 = now_ms();
        if (!pipe.has_picture) {
            if ((flags & WD_WANT_PICTURE) && duplication) nudge_compositor();
            if (timings) *timings = {(float)(t1 - t0), 0.0f, 0.0f};
            return result;
        }
        ComPtr<ID3D11Texture2D> back;
        if (FAILED(swapchain->GetBuffer(0, IID_PPV_ARGS(&back)))) return result;
        ComPtr<ID3D11RenderTargetView> rtv;
        if (FAILED(pipe.device->CreateRenderTargetView(back.Get(), nullptr, &rtv))) return result;
        pipe.draw_effect(rtv.Get(), params);
        double t2 = now_ms();
        // On the next vertical blank. Never waiting, unless told to: a frame
        // drawn when the swap chain is full is dropped rather than blocking
        // the app, which a display turning off would do for most of a second.
        HRESULT hr = swapchain->Present(1, (flags & WD_MUST_PRESENT) ? 0 : DXGI_PRESENT_DO_NOT_WAIT);
        double t3 = now_ms();
        if (SUCCEEDED(hr)) {
            result |= WD_DREW;
            permit = false;
        } else if (hr != DXGI_ERROR_WAS_STILL_DRAWING) {
            set_error("Present failed (0x%08lx)", (unsigned long)hr);
        }
        result |= WD_HAS_PICTURE;
        if (timings) *timings = {(float)(t1 - t0), (float)(t2 - t1), (float)(t3 - t2)};
        return result;
    }

    bool set_opacity(float value, float seconds) {
        if (seconds <= 0.0f) {
            CHECK(effects->SetOpacity(value), "SetOpacity");
        } else {
            ComPtr<IDCompositionAnimation> animation;
            CHECK(dcomp->CreateAnimation(&animation), "CreateAnimation");
            // Linear from wherever it was asked to start; the caller passes it.
            CHECK(animation->End(seconds, value), "animation End");
            CHECK(effects->SetOpacity(animation.Get()), "SetOpacity (animated)");
        }
        CHECK(dcomp->Commit(), "Commit");
        return true;
    }

    bool fade(float from, float to, float seconds) {
        ComPtr<IDCompositionAnimation> animation;
        CHECK(dcomp->CreateAnimation(&animation), "CreateAnimation");
        CHECK(animation->AddCubic(0.0, from, (to - from) / seconds, 0.0f, 0.0f), "AddCubic");
        CHECK(animation->End(seconds, to), "animation End");
        CHECK(effects->SetOpacity(animation.Get()), "SetOpacity (animated)");
        CHECK(dcomp->Commit(), "Commit");
        return true;
    }
};

// ---------------------------------------------------------------------------
// The C API.

WD_API int wd_version() { return WD_VERSION; }

WD_API const char* wd_last_error() { return g_error; }

// Makes the renderer for the primary display, its overlay window shown but
// fully transparent. NULL on failure, with wd_last_error saying why.
WD_API void* wd_create(int width_cap) {
    Renderer* renderer = new (std::nothrow) Renderer();
    if (!renderer) {
        set_error("out of memory");
        return nullptr;
    }
    if (!renderer->create(width_cap)) {
        delete renderer;
        return nullptr;
    }
    return renderer;
}

WD_API void wd_destroy(void* handle) { delete static_cast<Renderer*>(handle); }

WD_API void wd_info(void* handle, WdInfo* info) {
    auto* r = static_cast<Renderer*>(handle);
    info->screen_width = r->pipe.screen_w;
    info->screen_height = r->pipe.screen_h;
    info->picture_width = r->pipe.picture_w;
    info->picture_height = r->pipe.picture_h;
    info->texture_width = r->pipe.texture_w;
    info->texture_height = r->pipe.texture_h;
    info->inset = r->pipe.inset;
    info->max_level = r->pipe.levels - 1;
    info->rotation = r->rotation;
    info->excluded = r->excluded ? 1 : 0;
}

WD_API void* wd_hwnd(void* handle) { return static_cast<Renderer*>(handle)->hwnd; }

// Starts or stops Desktop Duplication. Starting is quick: the first frame
// arrives on a later wd_frame. 1 on success.
WD_API int wd_capture(void* handle, int on) {
    auto* r = static_cast<Renderer*>(handle);
    if (!on) {
        r->stop_capture();
        return 1;
    }
    return r->start_capture() ? 1 : 0;
}

// One frame: a new picture first if WD_WANT_PICTURE and the desktop has one,
// then the effect, presented on the next vertical blank. Returns WD_* bits.
WD_API int wd_frame(void* handle, const WdParams* params, int flags, WdTimings* timings) {
    return static_cast<Renderer*>(handle)->frame(*params, flags, timings);
}

// The swap chain's "ready for a frame" handle, for a loop that waits on it
// alongside other things (MsgWaitForMultipleObjectsEx). A loop that does must
// call wd_signalled when the wait returns it, since that wait spent the signal.
WD_API void* wd_waitable(void* handle) { return static_cast<Renderer*>(handle)->frame_ready; }
WD_API void wd_signalled(void* handle) { static_cast<Renderer*>(handle)->permit = true; }
WD_API int wd_holds_signal(void* handle) {
    return static_cast<Renderer*>(handle)->permit ? 1 : 0;
}

// Waits for the swap chain to want a frame; see Renderer::wait. Safe to call
// from another thread than the one drawing, but not while wd_destroy runs.
WD_API int wd_wait(void* handle, int timeout_ms) {
    return static_cast<Renderer*>(handle)->wait(timeout_ms);
}

// The picture replaced with black, keeping its margin and pyramid in shape.
WD_API void wd_blank(void* handle) { static_cast<Renderer*>(handle)->pipe.fill_picture_black(); }

// The picture dropped and a transparent frame presented.
WD_API int wd_clear(void* handle) {
    auto* r = static_cast<Renderer*>(handle);
    r->pipe.has_picture = false;
    r->reset_wait();
    return r->present_clear() ? 1 : 0;
}

// The overlay's opacity, at once (``seconds`` 0) or as a linear fade from
// ``from`` that DirectComposition runs by itself. 1 on success.
WD_API int wd_opacity(void* handle, float from, float to, float seconds) {
    auto* r = static_cast<Renderer*>(handle);
    bool ok = seconds > 0.0f ? r->fade(from, to, seconds) : r->set_opacity(to, 0.0f);
    return ok ? 1 : 0;
}

// Checks the shaders and the whole pipeline without a display or a GPU: WARP,
// a synthetic desktop, the scale, the pyramid and one effect frame into an
// offscreen target, read back. 0 on success; otherwise wd_last_error says.
WD_API int wd_selftest() {
    Pipeline pipe;
    D3D_FEATURE_LEVEL level = D3D_FEATURE_LEVEL_11_0;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
                                   D3D11_CREATE_DEVICE_BGRA_SUPPORT, &level, 1, D3D11_SDK_VERSION,
                                   &pipe.device, nullptr, &pipe.context);
    if (FAILED(hr)) {
        set_error("WARP device failed (0x%08lx)", (unsigned long)hr);
        return 1;
    }
    if (!pipe.init_shaders()) return 2;
    const int sw = 640, sh = 400;
    pipe.layout(sw, sh, 320);
    if (!pipe.create_picture() || !pipe.ensure_desktop(sw, sh)) return 3;

    // A mid-grey desktop: 50% sRGB.
    std::vector<uint32_t> grey(sw * sh, 0xff808080u);
    pipe.context->UpdateSubresource(pipe.desktop.Get(), 0, nullptr, grey.data(), sw * 4, 0);
    pipe.scale_desktop_into_picture();

    D3D11_TEXTURE2D_DESC td = {};
    td.Width = sw;
    td.Height = sh;
    td.MipLevels = 1;
    td.ArraySize = 1;
    td.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_RENDER_TARGET;
    ComPtr<ID3D11Texture2D> target;
    ComPtr<ID3D11RenderTargetView> rtv;
    if (FAILED(pipe.device->CreateTexture2D(&td, nullptr, &target)) ||
        FAILED(pipe.device->CreateRenderTargetView(target.Get(), nullptr, &rtv))) {
        set_error("offscreen target failed");
        return 4;
    }

    // A flat sheet: every screen row samples its own picture row at scale 1,
    // no blur, no dimming. The centre must come back the grey that went in.
    WdParams p = {};
    for (int i = 0; i < WD_PROFILE_SAMPLES; ++i) {
        p.profile[i * 2] = (float)i / (WD_PROFILE_SAMPLES - 1);
        p.profile[i * 2 + 1] = 1.0f;
    }
    p.profile_end = (float)sh;
    p.profile_slope = 1.0f / sh;
    p.max_radius = 40.0f;
    p.dim_floor = 0.2f;
    p.dim_reach = 0.5f;
    p.max_dim = 1.0f;
    p.picture_fade = 1.0f;
    pipe.draw_effect(rtv.Get(), p);

    // And blurred and dimmed, which must come back darker but not black.
    td.BindFlags = 0;
    td.Usage = D3D11_USAGE_STAGING;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ComPtr<ID3D11Texture2D> staging;
    if (FAILED(pipe.device->CreateTexture2D(&td, nullptr, &staging))) {
        set_error("staging texture failed");
        return 5;
    }
    auto centre = [&](uint32_t& out) -> bool {
        pipe.context->CopyResource(staging.Get(), target.Get());
        D3D11_MAPPED_SUBRESOURCE mapped;
        if (FAILED(pipe.context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped))) return false;
        out = ((uint32_t*)((uint8_t*)mapped.pData + (sh / 2) * mapped.RowPitch))[sw / 2];
        pipe.context->Unmap(staging.Get(), 0);
        return true;
    };
    uint32_t flat = 0, dimmed = 0;
    if (!centre(flat)) {
        set_error("reading the flat frame back failed");
        return 6;
    }
    p.blur_strength = 1.0f;
    p.dim_strength = 1.0f;
    p.max_dim = 0.6f;
    pipe.draw_effect(rtv.Get(), p);
    if (!centre(dimmed)) {
        set_error("reading the dimmed frame back failed");
        return 6;
    }
    int flat_green = (flat >> 8) & 0xff, dimmed_green = (dimmed >> 8) & 0xff;
    if (std::abs(flat_green - 0x80) > 6) {
        set_error("a flat sheet drew 0x%08x at the centre, not the 0x80 grey it was given", flat);
        return 7;
    }
    if (dimmed_green >= flat_green || dimmed_green == 0) {
        set_error("dimming drew 0x%08x from 0x%08x", dimmed, flat);
        return 8;
    }
    set_error("ok: flat 0x%08x, dimmed 0x%08x", flat, dimmed);
    return 0;
}
