// The hinge sensors, through C++/WinRT. Ported from the Python app's
// winduo2/angle/sensor.py (in the git history).
//
// Two are tried, in this order:
// - Windows' own HingeAngleSensor, on machines whose firmware publishes one.
// - The Intel Integrated Sensor Hub's hinge sensor, a custom sensor (HID
//   usage 0x020B) that Galaxy Book 360 models expose instead. Its firmware
//   works the angle out from accelerometers in both lid and base.
//
// Both are read on the sensor thread, which is in the multithreaded apartment,
// so blocking on their async calls with .get() is allowed.

#include "platform.h"

#include <winrt/Windows.Devices.Enumeration.h>
#include <winrt/Windows.Devices.Sensors.Custom.h>
#include <winrt/Windows.Devices.Sensors.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/base.h>

#include <cwctype>

using namespace winrt;
using namespace winrt::Windows::Devices::Enumeration;
using namespace winrt::Windows::Devices::Sensors;
using namespace winrt::Windows::Devices::Sensors::Custom;
using namespace winrt::Windows::Foundation;

namespace wd {

void enter_winrt_thread() {
    try {
        init_apartment(apartment_type::multi_threaded);
    } catch (...) {
        // Already initialised on this thread, which is all that is needed.
    }
}

static double confidence_for(double angle) {
    return angle >= 0.0 && angle <= MAX_LID_ANGLE ? 1.0 : 0.0;
}

// --- Windows' own hinge angle sensor -------------------------------------------

namespace {
struct WindowsHinge : HingeReader {
    HingeAngleSensor sensor{nullptr};
    explicit WindowsHinge(HingeAngleSensor s) : sensor(std::move(s)) {}
    const char* name() const override { return "hinge angle sensor"; }
    bool read(double& angle, double& confidence) override {
        HingeAngleReading reading = sensor.GetCurrentReadingAsync().get();
        if (!reading) return false;
        angle = reading.AngleInDegrees();
        confidence = confidence_for(angle);
        return true;
    }
};

// --- The Intel ISH hinge sensor --------------------------------------------------

// The custom sensor type Intel publishes the hinge under.
const guid INTEL_HINGE_TYPE{0x0000020b, 0x766d, 0x4333, {0x82, 0x62, 0x27, 0xe8, 0x2d, 0xd1, 0x58, 0xb1}};
// The property holding the angle. The others carry a second angle, both
// accelerometer vectors and a posture mode, none of which this needs.
const wchar_t* ANGLE_KEY = L"{C458F8A7-4AE8-4777-9607-2E9BDD65110A} 161";

bool same_key(const hstring& key) {
    const wchar_t* a = key.c_str();
    const wchar_t* b = ANGLE_KEY;
    for (; *a && *b; ++a, ++b)
        if (std::towupper(*a) != std::towupper(*b)) return false;
    return *a == 0 && *b == 0;
}

// A boxed number, whatever numeric type the firmware chose.
template <typename T>
bool unbox_as(const IInspectable& value, double& out) {
    if (auto boxed = value.try_as<IReference<T>>()) {
        out = (double)boxed.Value();
        return true;
    }
    return false;
}

bool unbox_number(const IInspectable& value, double& out) {
    return unbox_as<double>(value, out) || unbox_as<float>(value, out) ||
           unbox_as<int32_t>(value, out) || unbox_as<uint32_t>(value, out) ||
           unbox_as<int16_t>(value, out) || unbox_as<uint16_t>(value, out) ||
           unbox_as<int64_t>(value, out) || unbox_as<uint64_t>(value, out) ||
           unbox_as<uint8_t>(value, out);
}

struct IntelHinge : HingeReader {
    CustomSensor sensor{nullptr};
    hstring key;
    bool has_key = false;
    explicit IntelHinge(CustomSensor s) : sensor(std::move(s)) {}
    const char* name() const override { return "Intel ISH hinge sensor"; }
    bool read(double& angle, double& confidence) override {
        CustomSensorReading reading = sensor.GetCurrentReading();
        if (!reading) return false;
        auto properties = reading.Properties();
        if (!has_key) {
            for (auto const& pair : properties) {
                if (same_key(pair.Key())) {
                    key = pair.Key();
                    has_key = true;
                    break;
                }
            }
            if (!has_key) return false;
        }
        if (!properties.HasKey(key)) return false;
        IInspectable value = properties.Lookup(key);
        if (!value || !unbox_number(value, angle)) return false;
        confidence = confidence_for(angle);
        return true;
    }
};
}  // namespace

HingeReader* open_hinge_sensor() {
    try {
        HingeAngleSensor sensor = HingeAngleSensor::GetDefaultAsync().get();
        if (!sensor) return nullptr;
        return new WindowsHinge(sensor);
    } catch (hresult_error const& error) {
        LOGD("sensor", "hinge angle sensor: %s", narrow(std::wstring(error.message())).c_str());
        return nullptr;
    }
}

HingeReader* open_intel_hinge_sensor() {
    try {
        hstring selector = CustomSensor::GetDeviceSelector(INTEL_HINGE_TYPE);
        DeviceInformationCollection devices = DeviceInformation::FindAllAsync(selector).get();
        if (!devices || devices.Size() == 0) return nullptr;
        CustomSensor sensor = CustomSensor::FromIdAsync(devices.GetAt(0).Id()).get();
        if (!sensor) return nullptr;
        try {
            uint32_t wanted = std::max<uint32_t>(sensor.MinimumReportInterval(), 1000u / 60u);
            sensor.ReportInterval(wanted);
        } catch (hresult_error const&) {
            LOGI("sensor", "hinge sensor kept its own report interval");
        }
        return new IntelHinge(sensor);
    } catch (hresult_error const& error) {
        LOGD("sensor", "Intel hinge sensor: %s", narrow(std::wstring(error.message())).c_str());
        return nullptr;
    }
}

}  // namespace wd
