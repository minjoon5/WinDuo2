"""The whole effect in one fragment shader.

Ported from ``DepthShaders.swift`` (Apache 2.0, Copyright 2026 Makito), with
Metal's implicit sRGB handling made explicit because a Qt default framebuffer is
not sRGB-capable.

Each screen pixel is mapped back into the picture through a per-row lookup of
the projected sheet (see ``DepthGeometry.profile``), then takes one sample from a mip pyramid at a level chosen by how
much blur belongs at that height. The picture already sits on a black margin, so
the two blur into each other and the picture's edge needs no special handling.

One sample per pixel, whatever the blur radius. A separable Gaussian at a
135-pixel radius would be hundreds of taps; picking a mip level is one.
"""

from __future__ import annotations

__all__ = ["VERTEX", "FRAGMENT"]

VERTEX = """
#version 330 core

// A single oversized triangle covering the viewport. Cheaper to set up than a
// quad and it needs no vertex buffer at all.
const vec2 CORNERS[3] = vec2[3](
    vec2(-1.0, -3.0),
    vec2(-1.0,  1.0),
    vec2( 3.0,  1.0)
);

void main() {
    gl_Position = vec4(CORNERS[gl_VertexID], 0.0, 1.0);
}
"""

FRAGMENT = """
#version 330 core

uniform sampler2D uPicture;

// Screen row to picture row, as a lookup. Entry i covers screen row
// i / (PROFILE_SAMPLES - 1) * uProfileEnd; x is the picture row as a fraction of
// the height and y the horizontal scale about the centre at that row. Above
// uProfileEnd the last entry continues at uProfileSlope rows per point, so the
// blur can still reach the black margin past the top edge.
#define PROFILE_SAMPLES 128
uniform vec2 uProfile[PROFILE_SAMPLES];
uniform float uProfileEnd;
uniform float uProfileSlope;

uniform vec2 uScreenSize;     // the display, in points
uniform vec2 uPaddedOrigin;   // top-left of the padded picture, in picture points
uniform vec2 uPaddedSize;     // the padded picture, in points

uniform float uMaxRadius;     // blur radius at full strength, in texture pixels
uniform float uBlurStrength;  // 0 to 1, from the closing travel
uniform float uBlurFloor;     // blur at the hinge edge, as a fraction of the far edge
uniform float uMaxLevel;      // highest mip level the texture has

uniform float uMaxDim;
uniform float uDimStrength;
uniform float uDimFloor;
uniform float uDimReach;

// Idea adapted from macTilt's hinge specular and reflection band
// (github.com/lqSky7/iphone-duo-macos-animation, MIT), reworked for a receding
// plane rather than a folding one: no bend point here, so both are functions
// of height and progress alone.
uniform float uHingeGlow;           // 0 to 1, strength of the hinge highlight
uniform float uReflectionIntensity; // 0 to 1, strength of the reflection band
uniform float uTurn;                // 0 to 1, how far the picture has turned
uniform float uOpacity;             // 0 to 1, the fade where windows cannot fade

out vec4 fragColour;

// A mip level alone is a box filter, and magnifying one draws its texels as
// visible blocks: on a Galaxy Book Pro 360 the blur showed pixels. So the blur
// reads one and a half levels finer and averages a disc of taps over it, Gaussian
// weighted, which spreads to about the same width without the grid.
#define BLUR_TAPS 16
// A Vogel spiral: even coverage of the disc with no preferred direction.
const vec2 DISC[BLUR_TAPS] = vec2[](
    vec2( 0.265,  0.000), vec2(-0.339,  0.310), vec2( 0.052, -0.591), vec2( 0.427,  0.557),
    vec2(-0.783, -0.139), vec2( 0.742, -0.472), vec2(-0.248,  0.923), vec2(-0.473, -0.911),
    vec2( 1.027,  0.375), vec2(-1.068,  0.441), vec2( 0.515, -1.101), vec2( 0.381,  1.213),
    vec2(-1.147, -0.665), vec2( 1.346, -0.296), vec2(-0.821,  1.168), vec2(-0.190, -1.464)
);

vec3 softBlur(vec2 uv, float level) {
    if (level < 0.5) {
        return textureLod(uPicture, uv, level).rgb;
    }
    float base = max(level - 1.5, 0.0);
    vec2 step = exp2(base) * 2.0 / vec2(textureSize(uPicture, 0));
    vec3 sum = vec3(0.0);
    float total = 0.0;
    for (int i = 0; i < BLUR_TAPS; ++i) {
        float weight = exp(-dot(DISC[i], DISC[i]) * 0.8);
        sum += textureLod(uPicture, uv + DISC[i] * step, base).rgb * weight;
        total += weight;
    }
    return sum / total;
}

// The picture is stored as sRGB and sampled to linear light, so the dimming has
// to be encoded back on the way out.
vec3 linearToSrgb(vec3 linear) {
    vec3 low = linear * 12.92;
    vec3 high = 1.055 * pow(max(linear, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055;
    return mix(low, high, step(vec3(0.0031308), linear));
}

void main() {
    // gl_FragCoord already runs y-up from the bottom left, which is the same
    // convention the geometry uses. Metal needed a flip here; OpenGL does not.
    vec2 screenPoint = gl_FragCoord.xy;

    vec2 entry;
    if (screenPoint.y >= uProfileEnd) {
        entry = uProfile[PROFILE_SAMPLES - 1];
        entry.x += (screenPoint.y - uProfileEnd) * uProfileSlope;
    } else {
        float position = max(screenPoint.y, 0.0) / uProfileEnd * float(PROFILE_SAMPLES - 1);
        int i = min(int(position), PROFILE_SAMPLES - 2);
        entry = mix(uProfile[i], uProfile[i + 1], position - float(i));
    }
    float centre = uScreenSize.x * 0.5;
    vec2 picturePoint = vec2(
        centre + (screenPoint.x - centre) / max(entry.y, 1e-4),
        entry.x * uScreenSize.y
    );

    vec2 unit = (picturePoint - uPaddedOrigin) / uPaddedSize;
    if (unit.x < 0.0 || unit.x > 1.0 || unit.y < 0.0 || unit.y > 1.0) {
        fragColour = vec4(0.0, 0.0, 0.0, uOpacity);
        return;
    }
    // Texture row 0 is the top of the screen; picture y runs up.
    vec2 texCoord = vec2(unit.x, 1.0 - unit.y);

    // Where this pixel falls on the picture, 0 to 1 on each axis inside it.
    vec2 pictureUnit = picturePoint / uScreenSize;

    // Height across the picture itself, 0 at the hinge edge and 1 at the far
    // edge. Not across the padded texture, so the margin does not shift it.
    float height = clamp(pictureUnit.y, 0.0, 1.0);

    // Whether this pixel is on the picture at all, rather than on the black
    // margin around it. Blur and dimming can use the clamped height freely,
    // because both bottom out at zero off the picture. Anything *additive*
    // cannot: the clamp maps the whole bottom margin onto height 0, which is
    // exactly where the hinge highlight peaks, so without this the "narrow
    // line" would paint the entire strip below the hinge.
    //
    // Both axes have to be tested, not just height. The picture narrows as it
    // recedes, so the left and right margins sit at the same heights as the
    // picture itself and a height-only test lets them through.
    vec2 inside = step(vec2(0.0), pictureUnit) * step(pictureUnit, vec2(1.0));
    float onPicture = inside.x * inside.y;

    float blur = uBlurStrength * (uBlurFloor + (1.0 - uBlurFloor) * height);
    float level = clamp(log2(max(blur * uMaxRadius, 1.0)), 0.0, uMaxLevel);

    vec3 colour = softBlur(texCoord, level);

    // smoothstep rather than a clamped ratio, so the height where the dimming
    // reaches full strength leaves no visible seam across the picture.
    float spread = smoothstep(0.0, max(uDimReach, 0.02), height);
    float fade = uDimStrength * (uDimFloor + (1.0 - uDimFloor) * spread);
    colour *= pow(1.0 - uMaxDim * fade, 2.2);

    // Both light terms below are gated on uTurn, not on uDimStrength. Reusing
    // the dimming curve looked free and was not: dim_curve is 0.7, so five per
    // cent of travel already carries twelve per cent of the effect, which made
    // both terms pop in near the trigger instead of growing with the turn. They
    // also inherit any future retune of the dimming. uTurn is progress shaped
    // by its own exponent above 1, so the light lags the turn instead.
    //
    // Amplitudes are set from the *encoded* result rather than the linear
    // value, which is the part that is easy to get wrong here: these are added
    // in linear light, and 0.05 linear encodes to roughly 24% sRGB, a quarter
    // grey stripe rather than the hint it reads as in the source.

    // A narrow line right at the hinge, growing with how far the picture has
    // turned. Sells a real crease catching light without faking a bend the
    // geometry does not have.
    float hingeLine = exp(-pow(height / 0.05, 2.0)) * uTurn * onPicture;
    colour += vec3(0.92, 0.95, 0.97) * hingeLine * uHingeGlow * 0.006;

    // A soft pale band roughly two thirds of the way up, standing in for
    // glass catching light as it turns rather than a literal reflection,
    // which would need real scene data this shader does not have.
    float reflectionBand = exp(-pow((height - 0.65) / 0.28, 2.0)) * uTurn * onPicture;
    colour += vec3(0.85, 0.88, 0.9) * reflectionBand * uReflectionIntensity * 0.004;

    // Clamped before encoding. The additions are radiometric and can push a
    // bright picture past 1, which clips to a flat blown edge rather than
    // reading as a highlight.
    colour = clamp(colour, 0.0, 1.0);

    // Alpha 1 everywhere. The window is translucent so that the compositor
    // never treats the windows underneath as fully hidden, which would stop
    // them drawing and freeze the very picture being captured. Opaque pixels in
    // a translucent window give the same image without that side effect.
    // Premultiplied. uOpacity stays 1 on Windows, where the window's own
    // opacity does the fade; Wayland has no window opacity, so it fades here.
    fragColour = vec4(linearToSrgb(colour) * uOpacity, uOpacity);
}
"""
