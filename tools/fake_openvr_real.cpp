// Fake app-local OpenVR DLL used only to smoke-test the submit shim ABI.
//
// Build from this repo root on macOS with:
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
//     -static-libstdc++ -shared tools/fake_openvr_real.cpp \
//     -I$HOME/Developer/alvr/openvr/headers \
//     -o $PROBE_OUT/fake_openvr_real.dll

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <openvr.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace {

constexpr size_t kSystemSlots = 46;
constexpr size_t kLegacySystem019Slots = 47;
constexpr size_t kCompositorSlots = 51;
constexpr size_t kChaperoneSlots = 9;
constexpr size_t kChaperoneSetupSlots = 20;
constexpr size_t kOverlaySlots = 90;
constexpr size_t kRenderModelsSlots = 19;
constexpr size_t kScreenshotsSlots = 7;
constexpr size_t kLegacyInput005Slots = 25;
constexpr const char* kFnTablePrefix = "FnTable:";
constexpr const char* kLegacySystem019 = "IVRSystem_019";
constexpr const char* kLegacyChaperone003 = "IVRChaperone_003";
constexpr const char* kLegacyChaperoneSetup005 = "IVRChaperoneSetup_005";
constexpr const char* kLegacyOverlay018 = "IVROverlay_018";
constexpr const char* kLegacyOverlay019 = "IVROverlay_019";
constexpr const char* kLegacyRenderModels006 = "IVRRenderModels_006";
constexpr const char* kLegacyScreenshots001 = "IVRScreenshots_001";
constexpr const char* kLegacyInput005 = "IVRInput_005";

bool is_system_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRSystem_Version) == 0
            || std::strcmp(version, kLegacySystem019) == 0);
}

bool is_legacy_system019_interface(const char* version) {
    return version && std::strcmp(version, kLegacySystem019) == 0;
}

bool is_compositor_interface(const char* version) {
    return version && std::strcmp(version, vr::IVRCompositor_Version) == 0;
}

bool is_chaperone_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRChaperone_Version) == 0
            || std::strcmp(version, kLegacyChaperone003) == 0);
}

bool is_chaperone_setup_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRChaperoneSetup_Version) == 0
            || std::strcmp(version, kLegacyChaperoneSetup005) == 0);
}

bool is_legacy_chaperone_setup005_interface(const char* version) {
    return version && std::strcmp(version, kLegacyChaperoneSetup005) == 0;
}

bool is_overlay_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVROverlay_Version) == 0
            || std::strcmp(version, kLegacyOverlay018) == 0
            || std::strcmp(version, kLegacyOverlay019) == 0);
}

bool is_legacy_input005_interface(const char* version) {
    return version && std::strcmp(version, kLegacyInput005) == 0;
}

bool is_render_models_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRRenderModels_Version) == 0
            || std::strcmp(version, kLegacyRenderModels006) == 0);
}

bool is_screenshots_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRScreenshots_Version) == 0
            || std::strcmp(version, kLegacyScreenshots001) == 0);
}

void log_line(const char* text) {
    FILE* file = std::fopen("Z:\\tmp\\fake_openvr_real.log", "ab");
    if (!file) {
        return;
    }
    std::fprintf(file, "%s\n", text);
    std::fclose(file);
}

void log_call(const char* name) {
    char message[128] = {};
    std::snprintf(message, sizeof(message), "fake call %s", name);
    log_line(message);
}

void log_interface(const char* kind, const char* interface_version) {
    char message[256] = {};
    std::snprintf(
        message,
        sizeof(message),
        "fake VR_GetGenericInterface %s %s",
        kind,
        interface_version ? interface_version : "<null>"
    );
    log_line(message);
}

vr::HmdMatrix34_t identity34(float x = 0.0f) {
    vr::HmdMatrix34_t m = {};
    m.m[0][0] = 1.0f;
    m.m[1][1] = 1.0f;
    m.m[2][2] = 1.0f;
    m.m[0][3] = x;
    return m;
}

vr::HmdMatrix44_t identity44() {
    vr::HmdMatrix44_t m = {};
    m.m[0][0] = 1.0f;
    m.m[1][1] = 1.0f;
    m.m[2][2] = 1.0f;
    m.m[3][3] = 1.0f;
    return m;
}

bool is_fake_hmd(vr::TrackedDeviceIndex_t device);
bool is_fake_controller(vr::TrackedDeviceIndex_t device);
bool is_fake_tracked_device(vr::TrackedDeviceIndex_t device);
vr::ETrackedControllerRole fake_controller_role(vr::TrackedDeviceIndex_t device);

void fill_pose(vr::TrackedDevicePose_t* pose) {
    if (!pose) {
        return;
    }
    std::memset(pose, 0, sizeof(*pose));
    pose->mDeviceToAbsoluteTracking = identity34();
    pose->eTrackingResult = vr::TrackingResult_Running_OK;
    pose->bPoseIsValid = true;
    pose->bDeviceIsConnected = true;
}

void fill_poses(vr::TrackedDevicePose_t* poses, uint32_t count) {
    if (!poses) {
        return;
    }
    for (uint32_t index = 0; index < count; ++index) {
        fill_pose(&poses[index]);
        if (!is_fake_tracked_device(index)) {
            poses[index].bPoseIsValid = false;
            poses[index].bDeviceIsConnected = false;
        }
    }
}

void copy_string(const char* value, char* buffer, uint32_t buffer_size) {
    if (!buffer || buffer_size == 0) {
        return;
    }
    std::snprintf(buffer, buffer_size, "%s", value);
}

const char* render_model_name(uint32_t index) {
    switch (index) {
    case 0:
        return "fake_hmd";
    case 1:
        return "fake_controller_left";
    case 2:
        return "fake_controller_right";
    default:
        return nullptr;
    }
}

bool is_fake_controller_model(const char* model_name) {
    return model_name
        && (std::strcmp(model_name, "fake_controller_left") == 0
            || std::strcmp(model_name, "fake_controller_right") == 0);
}

bool is_fake_model(const char* model_name) {
    return model_name
        && (std::strcmp(model_name, "fake_hmd") == 0 || is_fake_controller_model(model_name));
}

const char* render_model_component_name(uint32_t index) {
    static const char* names[] = {
        vr::k_pch_Controller_Component_Base,
        vr::k_pch_Controller_Component_Tip,
        "handgrip",
        "body",
        "status",
    };
    if (index >= sizeof(names) / sizeof(names[0])) {
        return nullptr;
    }
    return names[index];
}

bool is_render_model_component(const char* component_name) {
    if (!component_name) {
        return false;
    }
    for (uint32_t index = 0;; ++index) {
        const char* name = render_model_component_name(index);
        if (!name) {
            return false;
        }
        if (std::strcmp(component_name, name) == 0) {
            return true;
        }
    }
}

const char* render_model_error_name(vr::EVRRenderModelError error) {
    switch (error) {
    case vr::VRRenderModelError_None:
        return "VRRenderModelError_None";
    case vr::VRRenderModelError_Loading:
        return "VRRenderModelError_Loading";
    case vr::VRRenderModelError_NotSupported:
        return "VRRenderModelError_NotSupported";
    case vr::VRRenderModelError_InvalidArg:
        return "VRRenderModelError_InvalidArg";
    case vr::VRRenderModelError_InvalidModel:
        return "VRRenderModelError_InvalidModel";
    case vr::VRRenderModelError_InvalidTexture:
        return "VRRenderModelError_InvalidTexture";
    default:
        return "VRRenderModelError_Unknown";
    }
}

vr::RenderModel_Vertex_t g_render_model_vertices[] = {
    {{{-0.03f, -0.03f, 0.0f}}, {{0.0f, 0.0f, -1.0f}}, {0.0f, 1.0f}},
    {{{0.03f, -0.03f, 0.0f}}, {{0.0f, 0.0f, -1.0f}}, {1.0f, 1.0f}},
    {{{0.0f, 0.04f, 0.0f}}, {{0.0f, 0.0f, -1.0f}}, {0.5f, 0.0f}},
};
uint16_t g_render_model_indices[] = { 0, 1, 2 };
uint8_t g_render_texture_data[] = { 255, 255, 255, 255 };
vr::RenderModel_TextureMap_t g_render_texture = {
    1,
    1,
    g_render_texture_data,
    vr::VRRenderModelTextureFormat_RGBA8_SRGB,
};
vr::RenderModel_t g_render_model = {
    g_render_model_vertices,
    static_cast<uint32_t>(sizeof(g_render_model_vertices) / sizeof(g_render_model_vertices[0])),
    g_render_model_indices,
    1,
    1,
};

bool is_fake_hmd(vr::TrackedDeviceIndex_t device) {
    return device == vr::k_unTrackedDeviceIndex_Hmd;
}

bool is_fake_controller(vr::TrackedDeviceIndex_t device) {
    return device == 1 || device == 2;
}

bool is_fake_tracked_device(vr::TrackedDeviceIndex_t device) {
    return is_fake_hmd(device) || is_fake_controller(device);
}

vr::ETrackedControllerRole fake_controller_role(vr::TrackedDeviceIndex_t device) {
    if (device == 1) {
        return vr::TrackedControllerRole_LeftHand;
    }
    if (device == 2) {
        return vr::TrackedControllerRole_RightHand;
    }
    return vr::TrackedControllerRole_Invalid;
}

void* __thiscall fake_ret0(void*) { return nullptr; }
void* __stdcall fake_c_ret0() { return nullptr; }

void __thiscall fake_get_recommended_render_target_size(void*, uint32_t* width, uint32_t* height) {
    log_call("IVRSystem::GetRecommendedRenderTargetSize");
    if (width) {
        *width = 1280;
    }
    if (height) {
        *height = 720;
    }
}

void __stdcall fake_c_get_recommended_render_target_size(uint32_t* width, uint32_t* height) {
    fake_get_recommended_render_target_size(nullptr, width, height);
}

vr::HmdMatrix44_t fake_projection_matrix(float near_z, float far_z) {
    if (near_z <= 0.0f) {
        near_z = 0.01f;
    }
    if (far_z <= near_z) {
        far_z = near_z + 1000.0f;
    }

    vr::HmdMatrix44_t m = {};
    m.m[0][0] = 1.0f;
    m.m[1][1] = 1.0f;
    m.m[2][2] = far_z / (near_z - far_z);
    m.m[2][3] = (far_z * near_z) / (near_z - far_z);
    m.m[3][2] = -1.0f;
    return m;
}

vr::HmdMatrix44_t __thiscall fake_get_projection_matrix(void*, vr::EVREye, float near_z, float far_z) {
    log_call("IVRSystem::GetProjectionMatrix");
    return fake_projection_matrix(near_z, far_z);
}

void __thiscall fake_cpp_get_projection_matrix(
    void*, vr::HmdMatrix44_t* output, vr::EVREye, float near_z, float far_z
) {
    log_call("IVRSystem::GetProjectionMatrix");
    if (output) {
        *output = fake_projection_matrix(near_z, far_z);
    }
}

vr::HmdMatrix44_t __stdcall fake_c_get_projection_matrix(vr::EVREye eye, float near_z, float far_z) {
    return fake_get_projection_matrix(nullptr, eye, near_z, far_z);
}

void __thiscall fake_get_projection_raw(
    void*, vr::EVREye, float* left, float* right, float* top, float* bottom
) {
    log_call("IVRSystem::GetProjectionRaw");
    if (left) {
        *left = -1.0f;
    }
    if (right) {
        *right = 1.0f;
    }
    if (top) {
        *top = -1.0f;
    }
    if (bottom) {
        *bottom = 1.0f;
    }
}

void __stdcall fake_c_get_projection_raw(vr::EVREye eye, float* left, float* right, float* top, float* bottom) {
    fake_get_projection_raw(nullptr, eye, left, right, top, bottom);
}

bool __thiscall fake_compute_distortion(
    void*, vr::EVREye, float u, float v, vr::DistortionCoordinates_t* coordinates
) {
    log_call("IVRSystem::ComputeDistortion");
    if (!coordinates) {
        return false;
    }
    coordinates->rfRed[0] = u;
    coordinates->rfRed[1] = v;
    coordinates->rfGreen[0] = u;
    coordinates->rfGreen[1] = v;
    coordinates->rfBlue[0] = u;
    coordinates->rfBlue[1] = v;
    return true;
}

bool __stdcall fake_c_compute_distortion(vr::EVREye eye, float u, float v, vr::DistortionCoordinates_t* coordinates) {
    return fake_compute_distortion(nullptr, eye, u, v, coordinates);
}

vr::HmdMatrix34_t __thiscall fake_get_eye_to_head_transform(void*, vr::EVREye eye) {
    log_call("IVRSystem::GetEyeToHeadTransform");
    return identity34(eye == vr::Eye_Left ? -0.032f : 0.032f);
}

void __thiscall fake_cpp_get_eye_to_head_transform(void*, vr::HmdMatrix34_t* output, vr::EVREye eye) {
    log_call("IVRSystem::GetEyeToHeadTransform");
    if (output) {
        *output = identity34(eye == vr::Eye_Left ? -0.032f : 0.032f);
    }
}

vr::HmdMatrix34_t __stdcall fake_c_get_eye_to_head_transform(vr::EVREye eye) {
    return fake_get_eye_to_head_transform(nullptr, eye);
}

bool __thiscall fake_get_time_since_last_vsync(void*, float* seconds, uint64_t* frame_counter) {
    log_call("IVRSystem::GetTimeSinceLastVsync");
    if (seconds) {
        *seconds = 0.0f;
    }
    if (frame_counter) {
        *frame_counter = 0;
    }
    return false;
}

bool __stdcall fake_c_get_time_since_last_vsync(float* seconds, uint64_t* frame_counter) {
    return fake_get_time_since_last_vsync(nullptr, seconds, frame_counter);
}

int32_t __thiscall fake_get_d3d9_adapter_index(void*) { return -1; }
int32_t __stdcall fake_c_get_d3d9_adapter_index() { return -1; }

void __thiscall fake_get_dxgi_output_info(void*, int32_t* adapter_index) {
    log_call("IVRSystem::GetDXGIOutputInfo");
    if (adapter_index) {
        *adapter_index = 0;
    }
}

void __stdcall fake_c_get_dxgi_output_info(int32_t* adapter_index) {
    fake_get_dxgi_output_info(nullptr, adapter_index);
}

void __thiscall fake_get_output_device(void*, uint64_t* device, vr::ETextureType, VkInstance_T*) {
    log_call("IVRSystem::GetOutputDevice");
    if (device) {
        *device = 0;
    }
}

void __stdcall fake_c_get_output_device(uint64_t* device, vr::ETextureType texture_type, VkInstance_T* instance) {
    fake_get_output_device(nullptr, device, texture_type, instance);
}

bool __thiscall fake_true(void*) { return true; }
bool __stdcall fake_c_true() { return true; }
bool __thiscall fake_set_display_visibility(void*, bool) { return true; }
bool __stdcall fake_c_set_display_visibility(bool) { return true; }

void __thiscall fake_get_tracking_pose(
    void*, vr::ETrackingUniverseOrigin, float, vr::TrackedDevicePose_t* poses, uint32_t count
) {
    log_call("IVRSystem::GetDeviceToAbsoluteTrackingPose");
    fill_poses(poses, count);
}

void __stdcall fake_c_get_tracking_pose(
    vr::ETrackingUniverseOrigin origin, float predicted_seconds, vr::TrackedDevicePose_t* poses, uint32_t count
) {
    fake_get_tracking_pose(nullptr, origin, predicted_seconds, poses, count);
}

vr::HmdMatrix34_t __thiscall fake_identity34(void*) { return identity34(); }
void __thiscall fake_cpp_identity34(void*, vr::HmdMatrix34_t* output) {
    if (output) {
        *output = identity34();
    }
}
vr::HmdMatrix34_t __stdcall fake_c_identity34() { return identity34(); }
void __thiscall fake_reset_seated_zero_pose(void*) { log_call("IVRSystem::ResetSeatedZeroPose"); }
void __stdcall fake_c_reset_seated_zero_pose() {}

uint32_t __thiscall fake_get_sorted_tracked_device_indices(
    void*, vr::ETrackedDeviceClass device_class, vr::TrackedDeviceIndex_t* indices, uint32_t count, vr::TrackedDeviceIndex_t
) {
    log_call("IVRSystem::GetSortedTrackedDeviceIndicesOfClass");
    if (device_class == vr::TrackedDeviceClass_HMD) {
        if (indices && count > 0) {
            indices[0] = vr::k_unTrackedDeviceIndex_Hmd;
        }
        return 1;
    }
    if (device_class == vr::TrackedDeviceClass_Controller) {
        if (indices && count > 0) {
            indices[0] = 1;
        }
        if (indices && count > 1) {
            indices[1] = 2;
        }
        return 2;
    }
    return 0;
}

uint32_t __stdcall fake_c_get_sorted_tracked_device_indices(
    vr::ETrackedDeviceClass device_class, vr::TrackedDeviceIndex_t* indices, uint32_t count, vr::TrackedDeviceIndex_t relative_to
) {
    return fake_get_sorted_tracked_device_indices(nullptr, device_class, indices, count, relative_to);
}

vr::EDeviceActivityLevel __thiscall fake_get_activity_level(void*, vr::TrackedDeviceIndex_t) {
    log_call("IVRSystem::GetTrackedDeviceActivityLevel");
    return vr::k_EDeviceActivityLevel_UserInteraction;
}

vr::EDeviceActivityLevel __stdcall fake_c_get_activity_level(vr::TrackedDeviceIndex_t device) {
    return fake_get_activity_level(nullptr, device);
}

void __thiscall fake_apply_transform(
    void*, vr::TrackedDevicePose_t* output, const vr::TrackedDevicePose_t* pose, const vr::HmdMatrix34_t* transform
) {
    log_call("IVRSystem::ApplyTransform");
    if (output && pose) {
        *output = *pose;
        if (transform) {
            output->mDeviceToAbsoluteTracking = *transform;
        }
    }
}

void __stdcall fake_c_apply_transform(
    vr::TrackedDevicePose_t* output, vr::TrackedDevicePose_t* pose, vr::HmdMatrix34_t* transform
) {
    fake_apply_transform(nullptr, output, pose, transform);
}

vr::TrackedDeviceIndex_t __thiscall fake_invalid_device_index(void*, vr::ETrackedControllerRole role) {
    log_call("IVRSystem::GetTrackedDeviceIndexForControllerRole");
    if (role == vr::TrackedControllerRole_LeftHand) {
        return 1;
    }
    if (role == vr::TrackedControllerRole_RightHand) {
        return 2;
    }
    return vr::k_unTrackedDeviceIndexInvalid;
}

vr::TrackedDeviceIndex_t __stdcall fake_c_invalid_device_index(vr::ETrackedControllerRole role) {
    return fake_invalid_device_index(nullptr, role);
}

vr::ETrackedControllerRole __thiscall fake_invalid_controller_role(void*, vr::TrackedDeviceIndex_t device) {
    log_call("IVRSystem::GetControllerRoleForTrackedDeviceIndex");
    return fake_controller_role(device);
}

vr::ETrackedControllerRole __stdcall fake_c_invalid_controller_role(vr::TrackedDeviceIndex_t device) {
    return fake_invalid_controller_role(nullptr, device);
}

vr::ETrackedDeviceClass __thiscall fake_get_tracked_device_class(void*, vr::TrackedDeviceIndex_t device) {
    log_call("IVRSystem::GetTrackedDeviceClass");
    if (is_fake_hmd(device)) {
        return vr::TrackedDeviceClass_HMD;
    }
    if (is_fake_controller(device)) {
        return vr::TrackedDeviceClass_Controller;
    }
    return vr::TrackedDeviceClass_Invalid;
}

vr::ETrackedDeviceClass __stdcall fake_c_get_tracked_device_class(vr::TrackedDeviceIndex_t device) {
    return fake_get_tracked_device_class(nullptr, device);
}

bool __thiscall fake_is_tracked_device_connected(void*, vr::TrackedDeviceIndex_t device) {
    log_call("IVRSystem::IsTrackedDeviceConnected");
    return is_fake_tracked_device(device);
}

bool __stdcall fake_c_is_tracked_device_connected(vr::TrackedDeviceIndex_t device) {
    return fake_is_tracked_device_connected(nullptr, device);
}

void set_property_error(vr::ETrackedPropertyError* error, vr::ETrackedPropertyError value) {
    if (error) {
        *error = value;
    }
}

bool __thiscall fake_get_bool_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_call("IVRSystem::GetBoolTrackedDeviceProperty");
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return false;
    }
    if (prop == vr::Prop_HasDisplayComponent_Bool) {
        set_property_error(error, vr::TrackedProp_Success);
        return is_fake_hmd(device);
    }
    if (prop == vr::Prop_HasControllerComponent_Bool) {
        set_property_error(error, vr::TrackedProp_Success);
        return is_fake_controller(device);
    }
    if (prop == vr::Prop_IsOnDesktop_Bool || prop == vr::Prop_DisplaySuppressed_Bool
        || prop == vr::Prop_NeverTracked_Bool) {
        set_property_error(error, vr::TrackedProp_Success);
        return false;
    }
    if (prop == vr::Prop_WillDriftInYaw_Bool || prop == vr::Prop_DeviceIsWireless_Bool
        || prop == vr::Prop_DeviceProvidesBatteryStatus_Bool) {
        set_property_error(error, vr::TrackedProp_Success);
        return is_fake_controller(device);
    }
    set_property_error(error, vr::TrackedProp_UnknownProperty);
    return false;
}

bool __stdcall fake_c_get_bool_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_bool_property(nullptr, device, prop, error);
}

float __thiscall fake_get_float_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_call("IVRSystem::GetFloatTrackedDeviceProperty");
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return 0.0f;
    }
    if (prop == vr::Prop_DeviceBatteryPercentage_Float) {
        set_property_error(error, vr::TrackedProp_Success);
        return 1.0f;
    }
    if (!is_fake_hmd(device)) {
        set_property_error(error, vr::TrackedProp_UnknownProperty);
        return 0.0f;
    }
    if (prop == vr::Prop_DisplayFrequency_Float) {
        set_property_error(error, vr::TrackedProp_Success);
        return 90.0f;
    }
    if (prop == vr::Prop_SecondsFromVsyncToPhotons_Float) {
        set_property_error(error, vr::TrackedProp_Success);
        return 0.011f;
    }
    if (prop == vr::Prop_UserIpdMeters_Float) {
        set_property_error(error, vr::TrackedProp_Success);
        return 0.064f;
    }
    set_property_error(error, vr::TrackedProp_UnknownProperty);
    return 0.0f;
}

float __stdcall fake_c_get_float_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_float_property(nullptr, device, prop, error);
}

int32_t __thiscall fake_get_int_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_call("IVRSystem::GetInt32TrackedDeviceProperty");
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return 0;
    }
    if (prop == vr::Prop_ControllerRoleHint_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return static_cast<int32_t>(fake_controller_role(device));
    }
    if (prop == vr::Prop_ExpectedControllerCount_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return 2;
    }
    if (prop >= vr::Prop_Axis0Type_Int32 && prop <= vr::Prop_Axis4Type_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return 0;
    }
    set_property_error(error, vr::TrackedProp_UnknownProperty);
    return 0;
}

int32_t __stdcall fake_c_get_int_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_int_property(nullptr, device, prop, error);
}

uint64_t __thiscall fake_get_uint64_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_call("IVRSystem::GetUint64TrackedDeviceProperty");
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return 0;
    }
    if (prop == vr::Prop_CurrentUniverseId_Uint64) {
        set_property_error(error, vr::TrackedProp_Success);
        return 1;
    }
    set_property_error(error, vr::TrackedProp_UnknownProperty);
    return 0;
}

uint64_t __stdcall fake_c_get_uint64_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_uint64_property(nullptr, device, prop, error);
}

vr::HmdMatrix34_t __thiscall fake_get_matrix34_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty, vr::ETrackedPropertyError* error) {
    log_call("IVRSystem::GetMatrix34TrackedDeviceProperty");
    set_property_error(error, device == vr::k_unTrackedDeviceIndex_Hmd ? vr::TrackedProp_UnknownProperty : vr::TrackedProp_InvalidDevice);
    return identity34();
}

void __thiscall fake_cpp_get_matrix34_property(
    void*,
    vr::HmdMatrix34_t* output,
    vr::TrackedDeviceIndex_t device,
    vr::ETrackedDeviceProperty,
    vr::ETrackedPropertyError* error
) {
    log_call("IVRSystem::GetMatrix34TrackedDeviceProperty");
    set_property_error(error, device == vr::k_unTrackedDeviceIndex_Hmd ? vr::TrackedProp_UnknownProperty : vr::TrackedProp_InvalidDevice);
    if (output) {
        *output = identity34();
    }
}

vr::HmdMatrix34_t __stdcall fake_c_get_matrix34_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_matrix34_property(nullptr, device, prop, error);
}

uint32_t __thiscall fake_get_array_property(
    void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty, vr::PropertyTypeTag_t, void*, uint32_t, vr::ETrackedPropertyError* error
) {
    log_call("IVRSystem::GetArrayTrackedDeviceProperty");
    set_property_error(error, device == vr::k_unTrackedDeviceIndex_Hmd ? vr::TrackedProp_UnknownProperty : vr::TrackedProp_InvalidDevice);
    return 0;
}

uint32_t __stdcall fake_c_get_array_property(
    vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::PropertyTypeTag_t type, void* buffer, uint32_t size, vr::ETrackedPropertyError* error
) {
    return fake_get_array_property(nullptr, device, prop, type, buffer, size, error);
}

uint32_t __thiscall fake_get_string_property(
    void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, char* value, uint32_t value_size, vr::ETrackedPropertyError* error
) {
    log_call("IVRSystem::GetStringTrackedDeviceProperty");
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return 0;
    }

    const char* text = nullptr;
    if (prop == vr::Prop_TrackingSystemName_String) {
        text = "FakeOpenVR";
    } else if (prop == vr::Prop_ModelNumber_String) {
        text = is_fake_hmd(device) ? "FakeOpenVR Null HMD" : "FakeOpenVR Controller";
    } else if (prop == vr::Prop_SerialNumber_String) {
        text = is_fake_hmd(device) ? "FAKEOPENVR0001" : (device == 1 ? "FAKEOPENVRCTRLLEFT" : "FAKEOPENVRCTRLRIGHT");
    } else if (prop == vr::Prop_ManufacturerName_String) {
        text = "FakeOpenVR";
    } else if (prop == vr::Prop_RenderModelName_String) {
        text = is_fake_hmd(device) ? "fake_hmd" : (device == 1 ? "fake_controller_left" : "fake_controller_right");
    } else if (prop == vr::Prop_ControllerType_String) {
        text = is_fake_controller(device) ? "vive_controller" : "";
    } else if (prop == vr::Prop_RegisteredDeviceType_String) {
        text = is_fake_hmd(device) ? "fake/hmd" : (device == 1 ? "fake/controller_left" : "fake/controller_right");
    } else if (prop == vr::Prop_InputProfilePath_String) {
        text = is_fake_controller(device) ? "{indexcontroller}/input/index_controller_profile.json" : "";
    }
    if (!text) {
        set_property_error(error, vr::TrackedProp_UnknownProperty);
        return 0;
    }

    set_property_error(error, vr::TrackedProp_Success);
    copy_string(text, value, value_size);
    return static_cast<uint32_t>(std::strlen(text) + 1);
}

uint32_t __stdcall fake_c_get_string_property(
    vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, char* value, uint32_t value_size, vr::ETrackedPropertyError* error
) {
    return fake_get_string_property(nullptr, device, prop, value, value_size, error);
}

const char* __thiscall fake_get_prop_error_name(void*, vr::ETrackedPropertyError error) {
    log_call("IVRSystem::GetPropErrorNameFromEnum");
    switch (error) {
    case vr::TrackedProp_Success:
        return "TrackedProp_Success";
    case vr::TrackedProp_InvalidDevice:
        return "TrackedProp_InvalidDevice";
    case vr::TrackedProp_UnknownProperty:
        return "TrackedProp_UnknownProperty";
    default:
        return "TrackedProp_Error";
    }
}

const char* __stdcall fake_c_get_prop_error_name(vr::ETrackedPropertyError error) {
    return fake_get_prop_error_name(nullptr, error);
}

bool __thiscall fake_poll_next_event(void*, vr::VREvent_t*, uint32_t) { return false; }
bool __stdcall fake_c_poll_next_event(vr::VREvent_t* event, uint32_t event_size) {
    return fake_poll_next_event(nullptr, event, event_size);
}

bool __thiscall fake_poll_next_event_with_pose(
    void*, vr::ETrackingUniverseOrigin, vr::VREvent_t*, uint32_t, vr::TrackedDevicePose_t* pose
) {
    fill_pose(pose);
    return false;
}

bool __stdcall fake_c_poll_next_event_with_pose(
    vr::ETrackingUniverseOrigin origin, vr::VREvent_t* event, uint32_t event_size, vr::TrackedDevicePose_t* pose
) {
    return fake_poll_next_event_with_pose(nullptr, origin, event, event_size, pose);
}

const char* __thiscall fake_get_event_type_name(void*, vr::EVREventType) { return "VREvent_None"; }
const char* __stdcall fake_c_get_event_type_name(vr::EVREventType type) {
    return fake_get_event_type_name(nullptr, type);
}

vr::HiddenAreaMesh_t __thiscall fake_get_hidden_area_mesh(void*, vr::EVREye, vr::EHiddenAreaMeshType) {
    vr::HiddenAreaMesh_t mesh = {};
    return mesh;
}

void __thiscall fake_cpp_get_hidden_area_mesh(
    void*, vr::HiddenAreaMesh_t* output, vr::EVREye, vr::EHiddenAreaMeshType
) {
    log_call("IVRSystem::GetHiddenAreaMesh");
    if (output) {
        std::memset(output, 0, sizeof(*output));
    }
}

vr::HiddenAreaMesh_t __stdcall fake_c_get_hidden_area_mesh(vr::EVREye eye, vr::EHiddenAreaMeshType type) {
    return fake_get_hidden_area_mesh(nullptr, eye, type);
}

bool __thiscall fake_get_controller_state(void*, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, uint32_t state_size) {
    if (state && state_size >= sizeof(*state)) {
        std::memset(state, 0, sizeof(*state));
    }
    return is_fake_controller(device);
}

bool __stdcall fake_c_get_controller_state(vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, uint32_t state_size) {
    return fake_get_controller_state(nullptr, device, state, state_size);
}

bool __thiscall fake_get_controller_state_with_pose(
    void*, vr::ETrackingUniverseOrigin, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, uint32_t state_size, vr::TrackedDevicePose_t* pose
) {
    const bool ok = fake_get_controller_state(nullptr, device, state, state_size);
    if (is_fake_tracked_device(device)) {
        fill_pose(pose);
    } else if (pose) {
        std::memset(pose, 0, sizeof(*pose));
    }
    return ok;
}

bool __stdcall fake_c_get_controller_state_with_pose(
    vr::ETrackingUniverseOrigin origin, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, uint32_t state_size, vr::TrackedDevicePose_t* pose
) {
    return fake_get_controller_state_with_pose(nullptr, origin, device, state, state_size, pose);
}

const char* __thiscall fake_button_name(void*, vr::EVRButtonId) { return "Unknown"; }
const char* __stdcall fake_c_button_name(vr::EVRButtonId button) { return fake_button_name(nullptr, button); }
const char* __thiscall fake_axis_name(void*, vr::EVRControllerAxisType) { return "Unknown"; }
const char* __stdcall fake_c_axis_name(vr::EVRControllerAxisType axis) { return fake_axis_name(nullptr, axis); }
bool __thiscall fake_false(void*) { return false; }
bool __stdcall fake_c_false() { return false; }
void __thiscall fake_trigger_haptic_pulse(void*, vr::TrackedDeviceIndex_t, uint32_t, unsigned short) {}
void __stdcall fake_c_trigger_haptic_pulse(vr::TrackedDeviceIndex_t, uint32_t, unsigned short) {}
uint32_t __thiscall fake_driver_debug_request(
    void*, vr::TrackedDeviceIndex_t, const char*, char* response, uint32_t response_size
) {
    copy_string("", response, response_size);
    return 1;
}
uint32_t __stdcall fake_c_driver_debug_request(
    vr::TrackedDeviceIndex_t device, const char* request, char* response, uint32_t response_size
) {
    return fake_driver_debug_request(nullptr, device, request, response, response_size);
}
vr::EVRFirmwareError __thiscall fake_firmware_update(void*, vr::TrackedDeviceIndex_t) { return vr::VRFirmwareError_None; }
vr::EVRFirmwareError __stdcall fake_c_firmware_update(vr::TrackedDeviceIndex_t device) {
    return fake_firmware_update(nullptr, device);
}
void __thiscall fake_acknowledge_quit(void*) {}
void __stdcall fake_c_acknowledge_quit() {}
uint32_t __thiscall fake_get_app_container_file_paths(void*, char* buffer, uint32_t size) {
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_get_app_container_file_paths(char* buffer, uint32_t size) {
    return fake_get_app_container_file_paths(nullptr, buffer, size);
}
const char* __thiscall fake_get_runtime_version(void*) { return "FakeOpenVR_022"; }
const char* __stdcall fake_c_get_runtime_version() { return "FakeOpenVR_022"; }

vr::ChaperoneCalibrationState __thiscall fake_get_chaperone_calibration_state(void*) {
    log_call("IVRChaperone::GetCalibrationState");
    return vr::ChaperoneCalibrationState_OK;
}

vr::ChaperoneCalibrationState __stdcall fake_c_get_chaperone_calibration_state() {
    log_call("FnTable:IVRChaperone::GetCalibrationState");
    return vr::ChaperoneCalibrationState_OK;
}

bool __thiscall fake_get_play_area_size(void*, float* size_x, float* size_z) {
    log_call("IVRChaperone::GetPlayAreaSize");
    if (size_x) {
        *size_x = 2.0f;
    }
    if (size_z) {
        *size_z = 2.0f;
    }
    return true;
}

bool __stdcall fake_c_get_play_area_size(float* size_x, float* size_z) {
    log_call("FnTable:IVRChaperone::GetPlayAreaSize");
    return fake_get_play_area_size(nullptr, size_x, size_z);
}

bool __thiscall fake_get_play_area_rect(void*, vr::HmdQuad_t* rect) {
    log_call("IVRChaperone::GetPlayAreaRect");
    if (rect) {
        std::memset(rect, 0, sizeof(*rect));
        rect->vCorners[0].v[0] = -1.0f;
        rect->vCorners[0].v[2] = -1.0f;
        rect->vCorners[1].v[0] = 1.0f;
        rect->vCorners[1].v[2] = -1.0f;
        rect->vCorners[2].v[0] = 1.0f;
        rect->vCorners[2].v[2] = 1.0f;
        rect->vCorners[3].v[0] = -1.0f;
        rect->vCorners[3].v[2] = 1.0f;
    }
    return true;
}

bool __stdcall fake_c_get_play_area_rect(vr::HmdQuad_t* rect) {
    log_call("FnTable:IVRChaperone::GetPlayAreaRect");
    return fake_get_play_area_rect(nullptr, rect);
}

void __thiscall fake_noop_void(void*) {}
void __stdcall fake_c_noop_void() {}
void __thiscall fake_noop_bool(void*, bool) {}
void __stdcall fake_c_noop_bool(bool) {}
void __thiscall fake_noop_color(void*, vr::HmdColor_t) {}
void __stdcall fake_c_noop_color(vr::HmdColor_t) {}

void __thiscall fake_get_bounds_color(
    void*, vr::HmdColor_t* colors, int count, float, vr::HmdColor_t* camera_color
) {
    log_call("IVRChaperone::GetBoundsColor");
    for (int index = 0; colors && index < count; ++index) {
        colors[index] = { 0.0f, 0.6f, 1.0f, 1.0f };
    }
    if (camera_color) {
        *camera_color = { 0.0f, 0.6f, 1.0f, 1.0f };
    }
}

void __stdcall fake_c_get_bounds_color(
    vr::HmdColor_t* colors, int count, float fade_distance, vr::HmdColor_t* camera_color
) {
    fake_get_bounds_color(nullptr, colors, count, fade_distance, camera_color);
}

bool __thiscall fake_commit_working_copy(void*, vr::EChaperoneConfigFile) { return true; }
bool __stdcall fake_c_commit_working_copy(vr::EChaperoneConfigFile) { return true; }
void __thiscall fake_reload_from_disk(void*, vr::EChaperoneConfigFile) {}
void __stdcall fake_c_reload_from_disk(vr::EChaperoneConfigFile) {}
bool __thiscall fake_get_pose34(void*, vr::HmdMatrix34_t* pose) {
    if (pose) {
        *pose = identity34();
    }
    return true;
}
bool __stdcall fake_c_get_pose34(vr::HmdMatrix34_t* pose) { return fake_get_pose34(nullptr, pose); }
bool __thiscall fake_collision_bounds(void*, vr::HmdQuad_t* buffer, uint32_t* count) {
    constexpr uint32_t bounds_count = 1;
    if (!count) {
        return false;
    }
    if (!buffer) {
        *count = bounds_count;
        return true;
    }

    uint32_t capacity = *count;
    *count = bounds_count;
    if (capacity < bounds_count) {
        return false;
    }

    return fake_get_play_area_rect(nullptr, buffer);
}
bool __stdcall fake_c_collision_bounds(vr::HmdQuad_t* buffer, uint32_t* count) {
    return fake_collision_bounds(nullptr, buffer, count);
}
void __thiscall fake_set_play_area_size(void*, float, float) {}
void __stdcall fake_c_set_play_area_size(float, float) {}
void __thiscall fake_set_collision_bounds(void*, vr::HmdQuad_t*, uint32_t) {}
void __stdcall fake_c_set_collision_bounds(vr::HmdQuad_t*, uint32_t) {}
void __thiscall fake_set_perimeter(void*, vr::HmdVector2_t*, uint32_t) {}
void __stdcall fake_c_set_perimeter(vr::HmdVector2_t*, uint32_t) {}
void __thiscall fake_set_pose34(void*, const vr::HmdMatrix34_t*) {}
void __stdcall fake_c_set_pose34(const vr::HmdMatrix34_t*) {}
void __thiscall fake_set_tags(void*, uint8_t*, uint32_t) {}
void __stdcall fake_c_set_tags(uint8_t*, uint32_t) {}
bool __thiscall fake_get_tags(void*, uint8_t*, uint32_t* count) {
    if (count) {
        *count = 0;
    }
    return true;
}
bool __stdcall fake_c_get_tags(uint8_t* buffer, uint32_t* count) { return fake_get_tags(nullptr, buffer, count); }
bool __thiscall fake_set_physical_bounds(void*, vr::HmdQuad_t*, uint32_t) { return true; }
bool __stdcall fake_c_set_physical_bounds(vr::HmdQuad_t*, uint32_t) { return true; }
bool __thiscall fake_export_buffer(void*, char* buffer, uint32_t* length) {
    if (length) {
        *length = 1;
    }
    if (buffer) {
        buffer[0] = '\0';
    }
    return true;
}
bool __stdcall fake_c_export_buffer(char* buffer, uint32_t* length) {
    return fake_export_buffer(nullptr, buffer, length);
}
bool __thiscall fake_import_buffer(void*, const char*, uint32_t) { return true; }
bool __stdcall fake_c_import_buffer(const char*, uint32_t) { return true; }

vr::EVROverlayError __thiscall fake_overlay_error(void*) { return vr::VROverlayError_UnknownOverlay; }
vr::EVROverlayError __stdcall fake_c_overlay_error() { return vr::VROverlayError_UnknownOverlay; }
vr::EVROverlayError __thiscall fake_overlay_find(void*, const char*, vr::VROverlayHandle_t* handle) {
    if (handle) {
        *handle = vr::k_ulOverlayHandleInvalid;
    }
    return vr::VROverlayError_UnknownOverlay;
}
vr::EVROverlayError __stdcall fake_c_overlay_find(const char*, vr::VROverlayHandle_t* handle) {
    return fake_overlay_find(nullptr, nullptr, handle);
}
vr::EVROverlayError __thiscall fake_overlay_create(void*, const char*, const char*, vr::VROverlayHandle_t* handle) {
    if (handle) {
        *handle = vr::k_ulOverlayHandleInvalid;
    }
    return vr::VROverlayError_UnknownOverlay;
}
vr::EVROverlayError __stdcall fake_c_overlay_create(const char*, const char*, vr::VROverlayHandle_t* handle) {
    return fake_overlay_create(nullptr, nullptr, nullptr, handle);
}
vr::VROverlayHandle_t __thiscall fake_overlay_handle(void*) { return vr::k_ulOverlayHandleInvalid; }
vr::VROverlayHandle_t __stdcall fake_c_overlay_handle() { return vr::k_ulOverlayHandleInvalid; }
const char* __thiscall fake_overlay_error_name(void*, vr::EVROverlayError) {
    log_call("IVROverlay::GetOverlayErrorNameFromEnum");
    return "FakeOverlay";
}
const char* __stdcall fake_c_overlay_error_name(vr::EVROverlayError error) {
    log_call("FnTable:IVROverlay::GetOverlayErrorNameFromEnum");
    return fake_overlay_error_name(nullptr, error);
}
uint32_t __thiscall fake_overlay_string(void*, vr::VROverlayHandle_t, char* buffer, uint32_t size, vr::EVROverlayError* error) {
    log_call("IVROverlay::GetOverlayString");
    if (error) {
        *error = vr::VROverlayError_UnknownOverlay;
    }
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_overlay_string(vr::VROverlayHandle_t handle, char* buffer, uint32_t size, vr::EVROverlayError* error) {
    log_call("FnTable:IVROverlay::GetOverlayString");
    return fake_overlay_string(nullptr, handle, buffer, size, error);
}
bool __thiscall fake_overlay_visible(void*, vr::VROverlayHandle_t) {
    log_call("IVROverlay::IsOverlayVisible");
    return false;
}
bool __stdcall fake_c_overlay_visible(vr::VROverlayHandle_t) {
    log_call("FnTable:IVROverlay::IsOverlayVisible");
    return false;
}
bool __thiscall fake_overlay_poll(void*, vr::VROverlayHandle_t, vr::VREvent_t*, uint32_t) {
    log_call("IVROverlay::PollNextOverlayEvent");
    return false;
}
bool __stdcall fake_c_overlay_poll(vr::VROverlayHandle_t, vr::VREvent_t*, uint32_t) {
    log_call("FnTable:IVROverlay::PollNextOverlayEvent");
    return false;
}
uint32_t __thiscall fake_overlay_pid(void*, vr::VROverlayHandle_t) {
    log_call("IVROverlay::GetOverlayRenderingPid");
    return 0;
}
uint32_t __stdcall fake_c_overlay_pid(vr::VROverlayHandle_t) {
    log_call("FnTable:IVROverlay::GetOverlayRenderingPid");
    return 0;
}

void __thiscall fake_set_tracking_space(void*, vr::ETrackingUniverseOrigin) {}
void __stdcall fake_c_set_tracking_space(vr::ETrackingUniverseOrigin) {}
vr::ETrackingUniverseOrigin __thiscall fake_tracking_space(void*) {
    return vr::TrackingUniverseStanding;
}

vr::ETrackingUniverseOrigin __stdcall fake_c_tracking_space() {
    return vr::TrackingUniverseStanding;
}

vr::EVRCompositorError __thiscall fake_wait_get_poses(
    void*, vr::TrackedDevicePose_t* render_poses, uint32_t render_count, vr::TrackedDevicePose_t* game_poses, uint32_t game_count
) {
    fill_poses(render_poses, render_count);
    fill_poses(game_poses, game_count);
    return vr::VRCompositorError_None;
}

vr::EVRCompositorError __stdcall fake_c_wait_get_poses(
    vr::TrackedDevicePose_t* render_poses, uint32_t render_count, vr::TrackedDevicePose_t* game_poses, uint32_t game_count
) {
    return fake_wait_get_poses(nullptr, render_poses, render_count, game_poses, game_count);
}

vr::EVRCompositorError __thiscall fake_last_pose(
    void*, vr::TrackedDeviceIndex_t, vr::TrackedDevicePose_t* render_pose, vr::TrackedDevicePose_t* game_pose
) {
    fill_pose(render_pose);
    fill_pose(game_pose);
    return vr::VRCompositorError_None;
}

vr::EVRCompositorError __stdcall fake_c_last_pose(
    vr::TrackedDeviceIndex_t device, vr::TrackedDevicePose_t* render_pose, vr::TrackedDevicePose_t* game_pose
) {
    return fake_last_pose(nullptr, device, render_pose, game_pose);
}

vr::EVRCompositorError __thiscall fake_submit(
    void*, vr::EVREye, const vr::Texture_t*, const vr::VRTextureBounds_t*, vr::EVRSubmitFlags
) {
    log_line("fake Submit called");
    return vr::VRCompositorError_None;
}

vr::EVRCompositorError __stdcall fake_c_submit(
    vr::EVREye, vr::Texture_t*, vr::VRTextureBounds_t*, vr::EVRSubmitFlags
) {
    log_line("fake C Submit called");
    return vr::VRCompositorError_None;
}

void __thiscall fake_post_present_handoff(void*) {}
void __stdcall fake_c_post_present_handoff() {}
bool __thiscall fake_get_frame_timing(void*, vr::Compositor_FrameTiming* timing, uint32_t) {
    if (timing) {
        uint32_t size = timing->m_nSize;
        std::memset(timing, 0, sizeof(*timing));
        timing->m_nSize = size ? size : sizeof(*timing);
    }
    return false;
}
bool __stdcall fake_c_get_frame_timing(vr::Compositor_FrameTiming* timing, uint32_t frames_ago) {
    return fake_get_frame_timing(nullptr, timing, frames_ago);
}
uint32_t __thiscall fake_get_frame_timings(void*, vr::Compositor_FrameTiming*, uint32_t) { return 0; }
uint32_t __stdcall fake_c_get_frame_timings(vr::Compositor_FrameTiming*, uint32_t) { return 0; }
float __thiscall fake_get_frame_time_remaining(void*) { return 0.011f; }
float __stdcall fake_c_get_frame_time_remaining() { return 0.011f; }
void __thiscall fake_get_cumulative_stats(void*, vr::Compositor_CumulativeStats* stats, uint32_t stats_size) {
    if (stats) {
        std::memset(stats, 0, std::min<size_t>(sizeof(*stats), stats_size));
    }
}
void __stdcall fake_c_get_cumulative_stats(vr::Compositor_CumulativeStats* stats, uint32_t stats_size) {
    fake_get_cumulative_stats(nullptr, stats, stats_size);
}
void __thiscall fake_fade_to_color(void*, float, float, float, float, float, bool) {}
void __stdcall fake_c_fade_to_color(float, float, float, float, float, bool) {}
vr::HmdColor_t __thiscall fake_current_fade_color(void*, bool) { return { 0.0f, 0.0f, 0.0f, 0.0f }; }
void __thiscall fake_cpp_current_fade_color(void*, vr::HmdColor_t* output, bool) {
    log_call("IVRCompositor::GetCurrentFadeColor");
    if (output) {
        *output = { 0.0f, 0.0f, 0.0f, 0.0f };
    }
}
vr::HmdColor_t __stdcall fake_c_current_fade_color(bool) { return { 0.0f, 0.0f, 0.0f, 0.0f }; }
void __thiscall fake_fade_grid(void*, float, bool) {}
void __stdcall fake_c_fade_grid(float, bool) {}
float __thiscall fake_grid_alpha(void*) { return 0.0f; }
float __stdcall fake_c_grid_alpha() { return 0.0f; }
vr::EVRCompositorError __thiscall fake_compositor_ok(void*) { return vr::VRCompositorError_None; }
vr::EVRCompositorError __stdcall fake_c_compositor_ok() { return vr::VRCompositorError_None; }
vr::EVRCompositorError __thiscall fake_compositor_request_failed(void*) { return vr::VRCompositorError_RequestFailed; }
vr::EVRCompositorError __stdcall fake_c_compositor_request_failed() { return vr::VRCompositorError_RequestFailed; }
uint32_t __thiscall fake_compositor_uint0(void*) { return 0; }
uint32_t __stdcall fake_c_compositor_uint0() { return 0; }
uint32_t __thiscall fake_compositor_string0(void*, char* buffer, uint32_t size) {
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_compositor_string0(char* buffer, uint32_t size) {
    return fake_compositor_string0(nullptr, buffer, size);
}
uint32_t __thiscall fake_compositor_device_string0(void*, VkPhysicalDevice_T*, char* buffer, uint32_t size) {
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_compositor_device_string0(VkPhysicalDevice_T* device, char* buffer, uint32_t size) {
    return fake_compositor_device_string0(nullptr, device, buffer, size);
}
bool __thiscall fake_release_shared_gl_texture(void*, vr::glUInt_t, vr::glSharedTextureHandle_t) { return false; }
bool __stdcall fake_c_release_shared_gl_texture(vr::glUInt_t, vr::glSharedTextureHandle_t) { return false; }
void __thiscall fake_gl_shared_texture_access(void*, vr::glSharedTextureHandle_t) {}
void __stdcall fake_c_gl_shared_texture_access(vr::glSharedTextureHandle_t) {}
void __thiscall fake_set_explicit_timing_mode(void*, vr::EVRCompositorTimingMode) {}
void __stdcall fake_c_set_explicit_timing_mode(vr::EVRCompositorTimingMode) {}

vr::VRInputValueHandle_t input_origin_for_handle(vr::VRInputValueHandle_t restrict_to_device) {
    return restrict_to_device != vr::k_ulInvalidInputValueHandle ? restrict_to_device : 1;
}

uint64_t stable_input_handle(const char* text, uint64_t salt) {
    uint64_t value = 1469598103934665603ULL ^ salt;
    for (const unsigned char* ch = reinterpret_cast<const unsigned char*>(text); ch && *ch; ++ch) {
        value ^= *ch;
        value *= 1099511628211ULL;
    }
    value &= 0x0000FFFFFFFFFFFFULL;
    return value ? value : salt;
}

vr::EVRInputError __stdcall fake_c_input_set_action_manifest_path(const char*) { return vr::VRInputError_None; }

vr::EVRInputError __stdcall fake_c_input_get_action_set_handle(const char* name, vr::VRActionSetHandle_t* handle) {
    if (handle) {
        *handle = stable_input_handle(name, 0x1000ULL);
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_action_handle(const char* name, vr::VRActionHandle_t* handle) {
    if (handle) {
        *handle = stable_input_handle(name, 0x2000ULL);
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_source_handle(const char* path, vr::VRInputValueHandle_t* handle) {
    if (handle) {
        if (path && std::strstr(path, "/user/hand/right")) {
            *handle = 2;
        } else if (path && std::strstr(path, "/user/hand/left")) {
            *handle = 1;
        } else if (path && std::strstr(path, "/user/head")) {
            *handle = 3;
        } else if (path && std::strstr(path, "/user/gamepad")) {
            *handle = 4;
        } else if (path && std::strstr(path, "/user/treadmill")) {
            *handle = 5;
        } else {
            *handle = stable_input_handle(path, 0x3000ULL);
        }
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_update_action_state(vr::VRActiveActionSet_t*, uint32_t, uint32_t) {
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_digital_action_data(
    vr::VRActionHandle_t, vr::InputDigitalActionData_t* data, uint32_t size, vr::VRInputValueHandle_t restrict_to_device
) {
    if (data && size >= sizeof(*data)) {
        std::memset(data, 0, sizeof(*data));
        data->bActive = true;
        data->activeOrigin = input_origin_for_handle(restrict_to_device);
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_analog_action_data(
    vr::VRActionHandle_t, vr::InputAnalogActionData_t* data, uint32_t size, vr::VRInputValueHandle_t restrict_to_device
) {
    if (data && size >= sizeof(*data)) {
        std::memset(data, 0, sizeof(*data));
        data->bActive = true;
        data->activeOrigin = input_origin_for_handle(restrict_to_device);
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_pose_action_data(
    vr::VRActionHandle_t,
    vr::ETrackingUniverseOrigin,
    float,
    vr::InputPoseActionData_t* data,
    uint32_t size,
    vr::VRInputValueHandle_t restrict_to_device
) {
    if (data && size >= sizeof(*data)) {
        std::memset(data, 0, sizeof(*data));
        data->bActive = true;
        data->activeOrigin = input_origin_for_handle(restrict_to_device);
        fill_pose(&data->pose);
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_action_data(
    vr::VRActionHandle_t, vr::InputSkeletalActionData_t* data, uint32_t size
) {
    if (data && size >= sizeof(*data)) {
        std::memset(data, 0, sizeof(*data));
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_bone_count(vr::VRActionHandle_t, uint32_t* count) {
    if (count) {
        *count = 0;
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_bone_hierarchy(vr::VRActionHandle_t, vr::BoneIndex_t*, uint32_t) {
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_bone_name(vr::VRActionHandle_t, vr::BoneIndex_t, char* name, uint32_t size) {
    copy_string("", name, size);
    return vr::VRInputError_InvalidBoneIndex;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_reference_transforms(
    vr::VRActionHandle_t,
    vr::EVRSkeletalTransformSpace,
    vr::EVRSkeletalReferencePose,
    vr::VRBoneTransform_t* transforms,
    uint32_t count
) {
    if (transforms) {
        std::memset(transforms, 0, static_cast<size_t>(count) * sizeof(*transforms));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_tracking_level(
    vr::VRActionHandle_t, vr::EVRSkeletalTrackingLevel* level
) {
    if (level) {
        *level = vr::VRSkeletalTracking_Estimated;
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_bone_data(
    vr::VRActionHandle_t,
    vr::EVRSkeletalTransformSpace,
    vr::EVRSkeletalMotionRange,
    vr::VRBoneTransform_t* transforms,
    uint32_t count
) {
    if (transforms) {
        std::memset(transforms, 0, static_cast<size_t>(count) * sizeof(*transforms));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_summary_data(vr::VRActionHandle_t, vr::VRSkeletalSummaryData_t* summary) {
    if (summary) {
        std::memset(summary, 0, sizeof(*summary));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_bone_data_compressed(
    vr::VRActionHandle_t, vr::EVRSkeletalMotionRange, void*, uint32_t, uint32_t* required_size
) {
    if (required_size) {
        *required_size = 0;
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_decompress_skeletal_bone_data(
    const void*, uint32_t, vr::EVRSkeletalTransformSpace, vr::VRBoneTransform_t* transforms, uint32_t count
) {
    if (transforms) {
        std::memset(transforms, 0, static_cast<size_t>(count) * sizeof(*transforms));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_trigger_haptic(
    vr::VRActionHandle_t, float, float, float, float, vr::VRInputValueHandle_t
) {
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_action_origins(
    vr::VRActionSetHandle_t, vr::VRActionHandle_t, vr::VRInputValueHandle_t* origins, uint32_t count
) {
    if (origins && count > 0) {
        origins[0] = 1;
        if (count > 1) {
            origins[1] = 2;
        }
        for (uint32_t index = 2; index < count; ++index) {
            origins[index] = 0;
        }
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_origin_localized_name(
    vr::VRInputValueHandle_t origin, char* name, uint32_t size, int32_t
) {
    copy_string(origin == 2 ? "Right Hand" : "Left Hand", name, size);
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_origin_tracked_device_info(
    vr::VRInputValueHandle_t origin, vr::InputOriginInfo_t* info, uint32_t size
) {
    if (info && size >= sizeof(*info)) {
        std::memset(info, 0, sizeof(*info));
        info->devicePath = origin;
        info->trackedDeviceIndex = origin == 2 ? 2 : 1;
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_show_action_origins(vr::VRActionSetHandle_t, vr::VRActionHandle_t) {
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_show_bindings_for_action_set(
    vr::VRActiveActionSet_t*, uint32_t, uint32_t, vr::VRInputValueHandle_t
) {
    return vr::VRInputError_None;
}

bool __stdcall fake_c_input_is_using_legacy_input() { return false; }

vr::EVRRenderModelError __stdcall fake_c_render_models_load_model(
    const char* model_name,
    vr::RenderModel_t** model
) {
    log_call("IVRRenderModels::LoadRenderModel_Async");
    if (model) {
        *model = nullptr;
    }
    if (!model_name || !model) {
        return vr::VRRenderModelError_InvalidArg;
    }
    if (!is_fake_model(model_name)) {
        return vr::VRRenderModelError_InvalidModel;
    }
    *model = &g_render_model;
    return vr::VRRenderModelError_None;
}

void __stdcall fake_c_render_models_free_model(vr::RenderModel_t*) {}

vr::EVRRenderModelError __stdcall fake_c_render_models_load_texture(
    vr::TextureID_t texture_id,
    vr::RenderModel_TextureMap_t** texture
) {
    log_call("IVRRenderModels::LoadTexture_Async");
    if (texture) {
        *texture = nullptr;
    }
    if (!texture) {
        return vr::VRRenderModelError_InvalidArg;
    }
    if (texture_id != 1) {
        return vr::VRRenderModelError_InvalidTexture;
    }
    *texture = &g_render_texture;
    return vr::VRRenderModelError_None;
}

void __stdcall fake_c_render_models_free_texture(vr::RenderModel_TextureMap_t*) {}

vr::EVRRenderModelError __stdcall fake_c_render_models_load_texture_d3d11(
    vr::TextureID_t texture_id,
    void*,
    void** texture
) {
    log_call("IVRRenderModels::LoadTextureD3D11_Async");
    if (texture) {
        *texture = nullptr;
    }
    return texture_id == vr::INVALID_TEXTURE_ID
        ? vr::VRRenderModelError_InvalidTexture
        : vr::VRRenderModelError_NotSupported;
}

vr::EVRRenderModelError __stdcall fake_c_render_models_load_into_texture_d3d11(
    vr::TextureID_t texture_id,
    void*
) {
    log_call("IVRRenderModels::LoadIntoTextureD3D11_Async");
    return texture_id == vr::INVALID_TEXTURE_ID
        ? vr::VRRenderModelError_InvalidTexture
        : vr::VRRenderModelError_NotSupported;
}

void __stdcall fake_c_render_models_free_texture_d3d11(void*) {}

uint32_t __stdcall fake_c_render_models_get_model_name(uint32_t index, char* name, uint32_t name_size) {
    const char* model_name = render_model_name(index);
    if (!model_name) {
        return 0;
    }
    copy_string(model_name, name, name_size);
    return static_cast<uint32_t>(std::strlen(model_name) + 1);
}

uint32_t __stdcall fake_c_render_models_get_model_count() { return 3; }

uint32_t __stdcall fake_c_render_models_get_component_count(const char* model_name) {
    return is_fake_controller_model(model_name) ? 5 : 0;
}

uint32_t __stdcall fake_c_render_models_get_component_name(
    const char* model_name, uint32_t index, char* component_name, uint32_t component_name_size
) {
    if (!is_fake_controller_model(model_name)) {
        return 0;
    }
    const char* name = render_model_component_name(index);
    if (!name) {
        return 0;
    }
    copy_string(name, component_name, component_name_size);
    return static_cast<uint32_t>(std::strlen(name) + 1);
}

uint64_t __stdcall fake_c_render_models_get_component_button_mask(const char*, const char*) {
    return 0;
}

uint32_t __stdcall fake_c_render_models_get_component_render_model_name(
    const char* model_name, const char* component_name, char* component_model_name, uint32_t component_model_name_size
) {
    if (!is_fake_controller_model(model_name) || !is_render_model_component(component_name)) {
        return 0;
    }
    copy_string(model_name, component_model_name, component_model_name_size);
    return static_cast<uint32_t>(std::strlen(model_name) + 1);
}

void fill_component_state(vr::RenderModel_ComponentState_t* state) {
    if (!state) {
        return;
    }
    std::memset(state, 0, sizeof(*state));
    state->mTrackingToComponentRenderModel = identity34();
    state->mTrackingToComponentLocal = identity34();
    state->uProperties = vr::VRComponentProperty_IsStatic | vr::VRComponentProperty_IsVisible;
}

bool __stdcall fake_c_render_models_get_component_state_for_device_path(
    const char* model_name,
    const char* component_name,
    vr::VRInputValueHandle_t,
    vr::RenderModel_ControllerMode_State_t*,
    vr::RenderModel_ComponentState_t* component_state
) {
    fill_component_state(component_state);
    return is_fake_controller_model(model_name) && is_render_model_component(component_name);
}

bool __stdcall fake_c_render_models_get_component_state(
    const char* model_name,
    const char* component_name,
    vr::VRControllerState_t*,
    vr::RenderModel_ControllerMode_State_t*,
    vr::RenderModel_ComponentState_t* component_state
) {
    fill_component_state(component_state);
    return is_fake_controller_model(model_name) && is_render_model_component(component_name);
}

bool __stdcall fake_c_render_models_has_component(const char* model_name, const char* component_name) {
    return is_fake_controller_model(model_name) && is_render_model_component(component_name);
}

uint32_t __stdcall fake_c_render_models_get_thumbnail_url(
    const char*, char* url, uint32_t url_size, vr::EVRRenderModelError* error
) {
    if (error) {
        *error = vr::VRRenderModelError_None;
    }
    copy_string("", url, url_size);
    return 1;
}

uint32_t __stdcall fake_c_render_models_get_original_path(
    const char* model_name, char* path, uint32_t path_size, vr::EVRRenderModelError* error
) {
    if (error) {
        *error = model_name ? vr::VRRenderModelError_None : vr::VRRenderModelError_InvalidArg;
    }
    if (!model_name) {
        return 0;
    }
    copy_string(model_name, path, path_size);
    return static_cast<uint32_t>(std::strlen(model_name) + 1);
}

const char* __stdcall fake_c_render_models_get_error_name(vr::EVRRenderModelError error) {
    return render_model_error_name(error);
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_request(
    vr::ScreenshotHandle_t* handle, vr::EVRScreenshotType, const char*, const char*
) {
    if (handle) {
        *handle = 1;
    }
    return vr::VRScreenshotError_RequestFailed;
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_hook(vr::EVRScreenshotType*, int) {
    return vr::VRScreenshotError_None;
}

vr::EVRScreenshotType __stdcall fake_c_screenshots_get_type(
    vr::ScreenshotHandle_t, vr::EVRScreenshotError* error
) {
    if (error) {
        *error = vr::VRScreenshotError_NotFound;
    }
    return vr::VRScreenshotType_None;
}

uint32_t __stdcall fake_c_screenshots_get_filename(
    vr::ScreenshotHandle_t,
    vr::EVRScreenshotPropertyFilenames,
    char* filename,
    uint32_t filename_size,
    vr::EVRScreenshotError* error
) {
    if (error) {
        *error = vr::VRScreenshotError_NotFound;
    }
    copy_string("", filename, filename_size);
    return 1;
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_update_progress(vr::ScreenshotHandle_t, float) {
    return vr::VRScreenshotError_NotFound;
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_take_stereo(
    vr::ScreenshotHandle_t* handle, const char*, const char*
) {
    if (handle) {
        *handle = 1;
    }
    return vr::VRScreenshotError_RequestFailed;
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_submit(
    vr::ScreenshotHandle_t, vr::EVRScreenshotType, const char*, const char*
) {
    return vr::VRScreenshotError_NotFound;
}

void* g_system_vtable[kSystemSlots] = {};
void* g_system_fntable[kSystemSlots] = {};
void* g_system019_vtable[kLegacySystem019Slots] = {};
void* g_system019_fntable[kLegacySystem019Slots] = {};
void* g_compositor_vtable[kCompositorSlots] = {};
void* g_compositor_fntable[kCompositorSlots] = {};
void* g_chaperone_vtable[kChaperoneSlots] = {};
void* g_chaperone_fntable[kChaperoneSlots] = {};
void* g_chaperone_setup_vtable[kChaperoneSetupSlots] = {};
void* g_chaperone_setup_fntable[kChaperoneSetupSlots] = {};
void* g_chaperone_setup005_vtable[kChaperoneSetupSlots] = {};
void* g_chaperone_setup005_fntable[kChaperoneSetupSlots] = {};
void* g_overlay_vtable[kOverlaySlots] = {};
void* g_overlay_fntable[kOverlaySlots] = {};
void* g_render_models_fntable[kRenderModelsSlots] = {};
void* g_screenshots_fntable[kScreenshotsSlots] = {};
void* g_input005_fntable[kLegacyInput005Slots] = {};
bool g_tables_initialized = false;

struct FakeSystemObject {
    void** vtable;
};

struct FakeCompositorObject {
    void** vtable;
};

struct FakeChaperoneObject {
    void** vtable;
};

struct FakeChaperoneSetupObject {
    void** vtable;
};

struct FakeOverlayObject {
    void** vtable;
};

FakeSystemObject g_system = { g_system_vtable };
FakeSystemObject g_system019 = { g_system019_vtable };
FakeCompositorObject g_compositor = { g_compositor_vtable };
FakeChaperoneObject g_chaperone = { g_chaperone_vtable };
FakeChaperoneSetupObject g_chaperone_setup = { g_chaperone_setup_vtable };
FakeChaperoneSetupObject g_chaperone_setup005 = { g_chaperone_setup005_vtable };
FakeOverlayObject g_overlay = { g_overlay_vtable };

void ensure_tables_initialized() {
    if (g_tables_initialized) {
        return;
    }

    for (size_t index = 0; index < kSystemSlots; ++index) {
        g_system_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_system_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_system_vtable[0] = reinterpret_cast<void*>(&fake_get_recommended_render_target_size);
    g_system_vtable[1] = reinterpret_cast<void*>(&fake_cpp_get_projection_matrix);
    g_system_vtable[2] = reinterpret_cast<void*>(&fake_get_projection_raw);
    g_system_vtable[3] = reinterpret_cast<void*>(&fake_compute_distortion);
    g_system_vtable[4] = reinterpret_cast<void*>(&fake_cpp_get_eye_to_head_transform);
    g_system_vtable[5] = reinterpret_cast<void*>(&fake_get_time_since_last_vsync);
    g_system_vtable[6] = reinterpret_cast<void*>(&fake_get_d3d9_adapter_index);
    g_system_vtable[7] = reinterpret_cast<void*>(&fake_get_dxgi_output_info);
    g_system_vtable[8] = reinterpret_cast<void*>(&fake_get_output_device);
    g_system_vtable[9] = reinterpret_cast<void*>(&fake_true);
    g_system_vtable[10] = reinterpret_cast<void*>(&fake_set_display_visibility);
    g_system_vtable[11] = reinterpret_cast<void*>(&fake_get_tracking_pose);
    g_system_vtable[12] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system_vtable[13] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system_vtable[14] = reinterpret_cast<void*>(&fake_get_sorted_tracked_device_indices);
    g_system_vtable[15] = reinterpret_cast<void*>(&fake_get_activity_level);
    g_system_vtable[16] = reinterpret_cast<void*>(&fake_apply_transform);
    g_system_vtable[17] = reinterpret_cast<void*>(&fake_invalid_device_index);
    g_system_vtable[18] = reinterpret_cast<void*>(&fake_invalid_controller_role);
    g_system_vtable[19] = reinterpret_cast<void*>(&fake_get_tracked_device_class);
    g_system_vtable[20] = reinterpret_cast<void*>(&fake_is_tracked_device_connected);
    g_system_vtable[21] = reinterpret_cast<void*>(&fake_get_bool_property);
    g_system_vtable[22] = reinterpret_cast<void*>(&fake_get_float_property);
    g_system_vtable[23] = reinterpret_cast<void*>(&fake_get_int_property);
    g_system_vtable[24] = reinterpret_cast<void*>(&fake_get_uint64_property);
    g_system_vtable[25] = reinterpret_cast<void*>(&fake_cpp_get_matrix34_property);
    g_system_vtable[26] = reinterpret_cast<void*>(&fake_get_array_property);
    g_system_vtable[27] = reinterpret_cast<void*>(&fake_get_string_property);
    g_system_vtable[28] = reinterpret_cast<void*>(&fake_get_prop_error_name);
    g_system_vtable[29] = reinterpret_cast<void*>(&fake_poll_next_event);
    g_system_vtable[30] = reinterpret_cast<void*>(&fake_poll_next_event_with_pose);
    g_system_vtable[31] = reinterpret_cast<void*>(&fake_get_event_type_name);
    g_system_vtable[32] = reinterpret_cast<void*>(&fake_cpp_get_hidden_area_mesh);
    g_system_vtable[33] = reinterpret_cast<void*>(&fake_get_controller_state);
    g_system_vtable[34] = reinterpret_cast<void*>(&fake_get_controller_state_with_pose);
    g_system_vtable[36] = reinterpret_cast<void*>(&fake_button_name);
    g_system_vtable[37] = reinterpret_cast<void*>(&fake_axis_name);
    g_system_vtable[38] = reinterpret_cast<void*>(&fake_true);
    g_system_vtable[39] = reinterpret_cast<void*>(&fake_false);
    g_system_vtable[40] = reinterpret_cast<void*>(&fake_false);
    g_system_vtable[41] = reinterpret_cast<void*>(&fake_false);
    g_system_vtable[42] = reinterpret_cast<void*>(&fake_firmware_update);
    g_system_vtable[44] = reinterpret_cast<void*>(&fake_get_app_container_file_paths);
    g_system_vtable[45] = reinterpret_cast<void*>(&fake_get_runtime_version);

    g_system_fntable[0] = reinterpret_cast<void*>(&fake_c_get_recommended_render_target_size);
    g_system_fntable[1] = reinterpret_cast<void*>(&fake_c_get_projection_matrix);
    g_system_fntable[2] = reinterpret_cast<void*>(&fake_c_get_projection_raw);
    g_system_fntable[3] = reinterpret_cast<void*>(&fake_c_compute_distortion);
    g_system_fntable[4] = reinterpret_cast<void*>(&fake_c_get_eye_to_head_transform);
    g_system_fntable[5] = reinterpret_cast<void*>(&fake_c_get_time_since_last_vsync);
    g_system_fntable[6] = reinterpret_cast<void*>(&fake_c_get_d3d9_adapter_index);
    g_system_fntable[7] = reinterpret_cast<void*>(&fake_c_get_dxgi_output_info);
    g_system_fntable[8] = reinterpret_cast<void*>(&fake_c_get_output_device);
    g_system_fntable[9] = reinterpret_cast<void*>(&fake_c_true);
    g_system_fntable[10] = reinterpret_cast<void*>(&fake_c_set_display_visibility);
    g_system_fntable[11] = reinterpret_cast<void*>(&fake_c_get_tracking_pose);
    g_system_fntable[12] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system_fntable[13] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system_fntable[14] = reinterpret_cast<void*>(&fake_c_get_sorted_tracked_device_indices);
    g_system_fntable[15] = reinterpret_cast<void*>(&fake_c_get_activity_level);
    g_system_fntable[16] = reinterpret_cast<void*>(&fake_c_apply_transform);
    g_system_fntable[17] = reinterpret_cast<void*>(&fake_c_invalid_device_index);
    g_system_fntable[18] = reinterpret_cast<void*>(&fake_c_invalid_controller_role);
    g_system_fntable[19] = reinterpret_cast<void*>(&fake_c_get_tracked_device_class);
    g_system_fntable[20] = reinterpret_cast<void*>(&fake_c_is_tracked_device_connected);
    g_system_fntable[21] = reinterpret_cast<void*>(&fake_c_get_bool_property);
    g_system_fntable[22] = reinterpret_cast<void*>(&fake_c_get_float_property);
    g_system_fntable[23] = reinterpret_cast<void*>(&fake_c_get_int_property);
    g_system_fntable[24] = reinterpret_cast<void*>(&fake_c_get_uint64_property);
    g_system_fntable[25] = reinterpret_cast<void*>(&fake_c_get_matrix34_property);
    g_system_fntable[26] = reinterpret_cast<void*>(&fake_c_get_array_property);
    g_system_fntable[27] = reinterpret_cast<void*>(&fake_c_get_string_property);
    g_system_fntable[28] = reinterpret_cast<void*>(&fake_c_get_prop_error_name);
    g_system_fntable[29] = reinterpret_cast<void*>(&fake_c_poll_next_event);
    g_system_fntable[30] = reinterpret_cast<void*>(&fake_c_poll_next_event_with_pose);
    g_system_fntable[31] = reinterpret_cast<void*>(&fake_c_get_event_type_name);
    g_system_fntable[32] = reinterpret_cast<void*>(&fake_c_get_hidden_area_mesh);
    g_system_fntable[33] = reinterpret_cast<void*>(&fake_c_get_controller_state);
    g_system_fntable[34] = reinterpret_cast<void*>(&fake_c_get_controller_state_with_pose);
    g_system_fntable[36] = reinterpret_cast<void*>(&fake_c_button_name);
    g_system_fntable[37] = reinterpret_cast<void*>(&fake_c_axis_name);
    g_system_fntable[38] = reinterpret_cast<void*>(&fake_c_true);
    g_system_fntable[39] = reinterpret_cast<void*>(&fake_c_false);
    g_system_fntable[40] = reinterpret_cast<void*>(&fake_c_false);
    g_system_fntable[41] = reinterpret_cast<void*>(&fake_c_false);
    g_system_fntable[42] = reinterpret_cast<void*>(&fake_c_firmware_update);
    g_system_fntable[44] = reinterpret_cast<void*>(&fake_c_get_app_container_file_paths);
    g_system_fntable[45] = reinterpret_cast<void*>(&fake_c_get_runtime_version);

    for (size_t index = 0; index < kLegacySystem019Slots; ++index) {
        g_system019_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_system019_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_system019_vtable[0] = reinterpret_cast<void*>(&fake_get_recommended_render_target_size);
    g_system019_vtable[1] = reinterpret_cast<void*>(&fake_cpp_get_projection_matrix);
    g_system019_vtable[2] = reinterpret_cast<void*>(&fake_get_projection_raw);
    g_system019_vtable[3] = reinterpret_cast<void*>(&fake_compute_distortion);
    g_system019_vtable[4] = reinterpret_cast<void*>(&fake_cpp_get_eye_to_head_transform);
    g_system019_vtable[5] = reinterpret_cast<void*>(&fake_get_time_since_last_vsync);
    g_system019_vtable[6] = reinterpret_cast<void*>(&fake_get_d3d9_adapter_index);
    g_system019_vtable[7] = reinterpret_cast<void*>(&fake_get_dxgi_output_info);
    g_system019_vtable[8] = reinterpret_cast<void*>(&fake_get_output_device);
    g_system019_vtable[9] = reinterpret_cast<void*>(&fake_true);
    g_system019_vtable[10] = reinterpret_cast<void*>(&fake_set_display_visibility);
    g_system019_vtable[11] = reinterpret_cast<void*>(&fake_get_tracking_pose);
    g_system019_vtable[12] = reinterpret_cast<void*>(&fake_reset_seated_zero_pose);
    g_system019_vtable[13] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system019_vtable[14] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system019_vtable[15] = reinterpret_cast<void*>(&fake_get_sorted_tracked_device_indices);
    g_system019_vtable[16] = reinterpret_cast<void*>(&fake_get_activity_level);
    g_system019_vtable[17] = reinterpret_cast<void*>(&fake_apply_transform);
    g_system019_vtable[18] = reinterpret_cast<void*>(&fake_invalid_device_index);
    g_system019_vtable[19] = reinterpret_cast<void*>(&fake_invalid_controller_role);
    g_system019_vtable[20] = reinterpret_cast<void*>(&fake_get_tracked_device_class);
    g_system019_vtable[21] = reinterpret_cast<void*>(&fake_is_tracked_device_connected);
    g_system019_vtable[22] = reinterpret_cast<void*>(&fake_get_bool_property);
    g_system019_vtable[23] = reinterpret_cast<void*>(&fake_get_float_property);
    g_system019_vtable[24] = reinterpret_cast<void*>(&fake_get_int_property);
    g_system019_vtable[25] = reinterpret_cast<void*>(&fake_get_uint64_property);
    g_system019_vtable[26] = reinterpret_cast<void*>(&fake_cpp_get_matrix34_property);
    g_system019_vtable[27] = reinterpret_cast<void*>(&fake_get_array_property);
    g_system019_vtable[28] = reinterpret_cast<void*>(&fake_get_string_property);
    g_system019_vtable[29] = reinterpret_cast<void*>(&fake_get_prop_error_name);
    g_system019_vtable[30] = reinterpret_cast<void*>(&fake_poll_next_event);
    g_system019_vtable[31] = reinterpret_cast<void*>(&fake_poll_next_event_with_pose);
    g_system019_vtable[32] = reinterpret_cast<void*>(&fake_get_event_type_name);
    g_system019_vtable[33] = reinterpret_cast<void*>(&fake_cpp_get_hidden_area_mesh);
    g_system019_vtable[34] = reinterpret_cast<void*>(&fake_get_controller_state);
    g_system019_vtable[35] = reinterpret_cast<void*>(&fake_get_controller_state_with_pose);
    g_system019_vtable[36] = reinterpret_cast<void*>(&fake_trigger_haptic_pulse);
    g_system019_vtable[37] = reinterpret_cast<void*>(&fake_button_name);
    g_system019_vtable[38] = reinterpret_cast<void*>(&fake_axis_name);
    g_system019_vtable[39] = reinterpret_cast<void*>(&fake_true);
    g_system019_vtable[40] = reinterpret_cast<void*>(&fake_false);
    g_system019_vtable[41] = reinterpret_cast<void*>(&fake_false);
    g_system019_vtable[42] = reinterpret_cast<void*>(&fake_false);
    g_system019_vtable[43] = reinterpret_cast<void*>(&fake_driver_debug_request);
    g_system019_vtable[44] = reinterpret_cast<void*>(&fake_firmware_update);
    g_system019_vtable[45] = reinterpret_cast<void*>(&fake_acknowledge_quit);
    g_system019_vtable[46] = reinterpret_cast<void*>(&fake_acknowledge_quit);

    g_system019_fntable[0] = reinterpret_cast<void*>(&fake_c_get_recommended_render_target_size);
    g_system019_fntable[1] = reinterpret_cast<void*>(&fake_c_get_projection_matrix);
    g_system019_fntable[2] = reinterpret_cast<void*>(&fake_c_get_projection_raw);
    g_system019_fntable[3] = reinterpret_cast<void*>(&fake_c_compute_distortion);
    g_system019_fntable[4] = reinterpret_cast<void*>(&fake_c_get_eye_to_head_transform);
    g_system019_fntable[5] = reinterpret_cast<void*>(&fake_c_get_time_since_last_vsync);
    g_system019_fntable[6] = reinterpret_cast<void*>(&fake_c_get_d3d9_adapter_index);
    g_system019_fntable[7] = reinterpret_cast<void*>(&fake_c_get_dxgi_output_info);
    g_system019_fntable[8] = reinterpret_cast<void*>(&fake_c_get_output_device);
    g_system019_fntable[9] = reinterpret_cast<void*>(&fake_c_true);
    g_system019_fntable[10] = reinterpret_cast<void*>(&fake_c_set_display_visibility);
    g_system019_fntable[11] = reinterpret_cast<void*>(&fake_c_get_tracking_pose);
    g_system019_fntable[12] = reinterpret_cast<void*>(&fake_c_reset_seated_zero_pose);
    g_system019_fntable[13] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system019_fntable[14] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system019_fntable[15] = reinterpret_cast<void*>(&fake_c_get_sorted_tracked_device_indices);
    g_system019_fntable[16] = reinterpret_cast<void*>(&fake_c_get_activity_level);
    g_system019_fntable[17] = reinterpret_cast<void*>(&fake_c_apply_transform);
    g_system019_fntable[18] = reinterpret_cast<void*>(&fake_c_invalid_device_index);
    g_system019_fntable[19] = reinterpret_cast<void*>(&fake_c_invalid_controller_role);
    g_system019_fntable[20] = reinterpret_cast<void*>(&fake_c_get_tracked_device_class);
    g_system019_fntable[21] = reinterpret_cast<void*>(&fake_c_is_tracked_device_connected);
    g_system019_fntable[22] = reinterpret_cast<void*>(&fake_c_get_bool_property);
    g_system019_fntable[23] = reinterpret_cast<void*>(&fake_c_get_float_property);
    g_system019_fntable[24] = reinterpret_cast<void*>(&fake_c_get_int_property);
    g_system019_fntable[25] = reinterpret_cast<void*>(&fake_c_get_uint64_property);
    g_system019_fntable[26] = reinterpret_cast<void*>(&fake_c_get_matrix34_property);
    g_system019_fntable[27] = reinterpret_cast<void*>(&fake_c_get_array_property);
    g_system019_fntable[28] = reinterpret_cast<void*>(&fake_c_get_string_property);
    g_system019_fntable[29] = reinterpret_cast<void*>(&fake_c_get_prop_error_name);
    g_system019_fntable[30] = reinterpret_cast<void*>(&fake_c_poll_next_event);
    g_system019_fntable[31] = reinterpret_cast<void*>(&fake_c_poll_next_event_with_pose);
    g_system019_fntable[32] = reinterpret_cast<void*>(&fake_c_get_event_type_name);
    g_system019_fntable[33] = reinterpret_cast<void*>(&fake_c_get_hidden_area_mesh);
    g_system019_fntable[34] = reinterpret_cast<void*>(&fake_c_get_controller_state);
    g_system019_fntable[35] = reinterpret_cast<void*>(&fake_c_get_controller_state_with_pose);
    g_system019_fntable[36] = reinterpret_cast<void*>(&fake_c_trigger_haptic_pulse);
    g_system019_fntable[37] = reinterpret_cast<void*>(&fake_c_button_name);
    g_system019_fntable[38] = reinterpret_cast<void*>(&fake_c_axis_name);
    g_system019_fntable[39] = reinterpret_cast<void*>(&fake_c_true);
    g_system019_fntable[40] = reinterpret_cast<void*>(&fake_c_false);
    g_system019_fntable[41] = reinterpret_cast<void*>(&fake_c_false);
    g_system019_fntable[42] = reinterpret_cast<void*>(&fake_c_false);
    g_system019_fntable[43] = reinterpret_cast<void*>(&fake_c_driver_debug_request);
    g_system019_fntable[44] = reinterpret_cast<void*>(&fake_c_firmware_update);
    g_system019_fntable[45] = reinterpret_cast<void*>(&fake_c_acknowledge_quit);
    g_system019_fntable[46] = reinterpret_cast<void*>(&fake_c_acknowledge_quit);

    for (size_t index = 0; index < kCompositorSlots; ++index) {
        g_compositor_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_compositor_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_compositor_vtable[0] = reinterpret_cast<void*>(&fake_set_tracking_space);
    g_compositor_vtable[1] = reinterpret_cast<void*>(&fake_tracking_space);
    g_compositor_vtable[2] = reinterpret_cast<void*>(&fake_wait_get_poses);
    g_compositor_vtable[3] = reinterpret_cast<void*>(&fake_wait_get_poses);
    g_compositor_vtable[4] = reinterpret_cast<void*>(&fake_last_pose);
    g_compositor_vtable[5] = reinterpret_cast<void*>(&fake_submit);
    g_compositor_vtable[6] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[7] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[8] = reinterpret_cast<void*>(&fake_get_frame_timing);
    g_compositor_vtable[9] = reinterpret_cast<void*>(&fake_get_frame_timings);
    g_compositor_vtable[10] = reinterpret_cast<void*>(&fake_get_frame_time_remaining);
    g_compositor_vtable[11] = reinterpret_cast<void*>(&fake_get_cumulative_stats);
    g_compositor_vtable[12] = reinterpret_cast<void*>(&fake_fade_to_color);
    g_compositor_vtable[13] = reinterpret_cast<void*>(&fake_cpp_current_fade_color);
    g_compositor_vtable[14] = reinterpret_cast<void*>(&fake_fade_grid);
    g_compositor_vtable[15] = reinterpret_cast<void*>(&fake_grid_alpha);
    g_compositor_vtable[16] = reinterpret_cast<void*>(&fake_compositor_ok);
    g_compositor_vtable[17] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[18] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[19] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[20] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[21] = reinterpret_cast<void*>(&fake_false);
    g_compositor_vtable[22] = reinterpret_cast<void*>(&fake_compositor_uint0);
    g_compositor_vtable[23] = reinterpret_cast<void*>(&fake_compositor_uint0);
    g_compositor_vtable[24] = reinterpret_cast<void*>(&fake_true);
    g_compositor_vtable[25] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[26] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[27] = reinterpret_cast<void*>(&fake_false);
    g_compositor_vtable[28] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[29] = reinterpret_cast<void*>(&fake_false);
    g_compositor_vtable[30] = reinterpret_cast<void*>(&fake_noop_bool);
    g_compositor_vtable[31] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[32] = reinterpret_cast<void*>(&fake_noop_bool);
    g_compositor_vtable[33] = reinterpret_cast<void*>(&fake_compositor_request_failed);
    g_compositor_vtable[34] = reinterpret_cast<void*>(&fake_ret0);
    g_compositor_vtable[35] = reinterpret_cast<void*>(&fake_compositor_request_failed);
    g_compositor_vtable[36] = reinterpret_cast<void*>(&fake_release_shared_gl_texture);
    g_compositor_vtable[37] = reinterpret_cast<void*>(&fake_gl_shared_texture_access);
    g_compositor_vtable[38] = reinterpret_cast<void*>(&fake_gl_shared_texture_access);
    g_compositor_vtable[39] = reinterpret_cast<void*>(&fake_compositor_string0);
    g_compositor_vtable[40] = reinterpret_cast<void*>(&fake_compositor_device_string0);
    g_compositor_vtable[41] = reinterpret_cast<void*>(&fake_set_explicit_timing_mode);
    g_compositor_vtable[42] = reinterpret_cast<void*>(&fake_compositor_request_failed);

    g_compositor_fntable[0] = reinterpret_cast<void*>(&fake_c_set_tracking_space);
    g_compositor_fntable[1] = reinterpret_cast<void*>(&fake_c_tracking_space);
    g_compositor_fntable[2] = reinterpret_cast<void*>(&fake_c_wait_get_poses);
    g_compositor_fntable[3] = reinterpret_cast<void*>(&fake_c_wait_get_poses);
    g_compositor_fntable[4] = reinterpret_cast<void*>(&fake_c_last_pose);
    g_compositor_fntable[5] = reinterpret_cast<void*>(&fake_c_submit);
    g_compositor_fntable[6] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[7] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[8] = reinterpret_cast<void*>(&fake_c_get_frame_timing);
    g_compositor_fntable[9] = reinterpret_cast<void*>(&fake_c_get_frame_timings);
    g_compositor_fntable[10] = reinterpret_cast<void*>(&fake_c_get_frame_time_remaining);
    g_compositor_fntable[11] = reinterpret_cast<void*>(&fake_c_get_cumulative_stats);
    g_compositor_fntable[12] = reinterpret_cast<void*>(&fake_c_fade_to_color);
    g_compositor_fntable[13] = reinterpret_cast<void*>(&fake_c_current_fade_color);
    g_compositor_fntable[14] = reinterpret_cast<void*>(&fake_c_fade_grid);
    g_compositor_fntable[15] = reinterpret_cast<void*>(&fake_c_grid_alpha);
    g_compositor_fntable[16] = reinterpret_cast<void*>(&fake_c_compositor_ok);
    g_compositor_fntable[17] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[18] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[19] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[20] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[21] = reinterpret_cast<void*>(&fake_c_false);
    g_compositor_fntable[22] = reinterpret_cast<void*>(&fake_c_compositor_uint0);
    g_compositor_fntable[23] = reinterpret_cast<void*>(&fake_c_compositor_uint0);
    g_compositor_fntable[24] = reinterpret_cast<void*>(&fake_c_true);
    g_compositor_fntable[25] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[26] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[27] = reinterpret_cast<void*>(&fake_c_false);
    g_compositor_fntable[28] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[29] = reinterpret_cast<void*>(&fake_c_false);
    g_compositor_fntable[30] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_compositor_fntable[31] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[32] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_compositor_fntable[33] = reinterpret_cast<void*>(&fake_c_compositor_request_failed);
    g_compositor_fntable[34] = reinterpret_cast<void*>(&fake_c_ret0);
    g_compositor_fntable[35] = reinterpret_cast<void*>(&fake_c_compositor_request_failed);
    g_compositor_fntable[36] = reinterpret_cast<void*>(&fake_c_release_shared_gl_texture);
    g_compositor_fntable[37] = reinterpret_cast<void*>(&fake_c_gl_shared_texture_access);
    g_compositor_fntable[38] = reinterpret_cast<void*>(&fake_c_gl_shared_texture_access);
    g_compositor_fntable[39] = reinterpret_cast<void*>(&fake_c_compositor_string0);
    g_compositor_fntable[40] = reinterpret_cast<void*>(&fake_c_compositor_device_string0);
    g_compositor_fntable[41] = reinterpret_cast<void*>(&fake_c_set_explicit_timing_mode);
    g_compositor_fntable[42] = reinterpret_cast<void*>(&fake_c_compositor_request_failed);

    for (size_t index = 0; index < kChaperoneSlots; ++index) {
        g_chaperone_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_chaperone_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_chaperone_vtable[0] = reinterpret_cast<void*>(&fake_get_chaperone_calibration_state);
    g_chaperone_vtable[1] = reinterpret_cast<void*>(&fake_get_play_area_size);
    g_chaperone_vtable[2] = reinterpret_cast<void*>(&fake_get_play_area_rect);
    g_chaperone_vtable[3] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_vtable[4] = reinterpret_cast<void*>(&fake_noop_color);
    g_chaperone_vtable[5] = reinterpret_cast<void*>(&fake_get_bounds_color);
    g_chaperone_vtable[6] = reinterpret_cast<void*>(&fake_false);
    g_chaperone_vtable[7] = reinterpret_cast<void*>(&fake_noop_bool);
    g_chaperone_vtable[8] = reinterpret_cast<void*>(&fake_ret0);

    g_chaperone_fntable[0] = reinterpret_cast<void*>(&fake_c_get_chaperone_calibration_state);
    g_chaperone_fntable[1] = reinterpret_cast<void*>(&fake_c_get_play_area_size);
    g_chaperone_fntable[2] = reinterpret_cast<void*>(&fake_c_get_play_area_rect);
    g_chaperone_fntable[3] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_fntable[4] = reinterpret_cast<void*>(&fake_c_noop_color);
    g_chaperone_fntable[5] = reinterpret_cast<void*>(&fake_c_get_bounds_color);
    g_chaperone_fntable[6] = reinterpret_cast<void*>(&fake_c_false);
    g_chaperone_fntable[7] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_chaperone_fntable[8] = reinterpret_cast<void*>(&fake_c_ret0);

    for (size_t index = 0; index < kChaperoneSetupSlots; ++index) {
        g_chaperone_setup_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_chaperone_setup_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_chaperone_setup_vtable[0] = reinterpret_cast<void*>(&fake_commit_working_copy);
    g_chaperone_setup_vtable[1] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_setup_vtable[2] = reinterpret_cast<void*>(&fake_get_play_area_size);
    g_chaperone_setup_vtable[3] = reinterpret_cast<void*>(&fake_get_play_area_rect);
    g_chaperone_setup_vtable[4] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup_vtable[5] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup_vtable[6] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup_vtable[7] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup_vtable[8] = reinterpret_cast<void*>(&fake_set_play_area_size);
    g_chaperone_setup_vtable[9] = reinterpret_cast<void*>(&fake_set_collision_bounds);
    g_chaperone_setup_vtable[10] = reinterpret_cast<void*>(&fake_set_perimeter);
    g_chaperone_setup_vtable[11] = reinterpret_cast<void*>(&fake_set_pose34);
    g_chaperone_setup_vtable[12] = reinterpret_cast<void*>(&fake_set_pose34);
    g_chaperone_setup_vtable[13] = reinterpret_cast<void*>(&fake_reload_from_disk);
    g_chaperone_setup_vtable[14] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup_vtable[15] = reinterpret_cast<void*>(&fake_export_buffer);
    g_chaperone_setup_vtable[16] = reinterpret_cast<void*>(&fake_import_buffer);
    g_chaperone_setup_vtable[17] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_setup_vtable[18] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_setup_vtable[19] = reinterpret_cast<void*>(&fake_noop_void);

    g_chaperone_setup_fntable[0] = reinterpret_cast<void*>(&fake_c_commit_working_copy);
    g_chaperone_setup_fntable[1] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_setup_fntable[2] = reinterpret_cast<void*>(&fake_c_get_play_area_size);
    g_chaperone_setup_fntable[3] = reinterpret_cast<void*>(&fake_c_get_play_area_rect);
    g_chaperone_setup_fntable[4] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup_fntable[5] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup_fntable[6] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup_fntable[7] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup_fntable[8] = reinterpret_cast<void*>(&fake_c_set_play_area_size);
    g_chaperone_setup_fntable[9] = reinterpret_cast<void*>(&fake_c_set_collision_bounds);
    g_chaperone_setup_fntable[10] = reinterpret_cast<void*>(&fake_c_set_perimeter);
    g_chaperone_setup_fntable[11] = reinterpret_cast<void*>(&fake_c_set_pose34);
    g_chaperone_setup_fntable[12] = reinterpret_cast<void*>(&fake_c_set_pose34);
    g_chaperone_setup_fntable[13] = reinterpret_cast<void*>(&fake_c_reload_from_disk);
    g_chaperone_setup_fntable[14] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup_fntable[15] = reinterpret_cast<void*>(&fake_c_export_buffer);
    g_chaperone_setup_fntable[16] = reinterpret_cast<void*>(&fake_c_import_buffer);
    g_chaperone_setup_fntable[17] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_setup_fntable[18] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_setup_fntable[19] = reinterpret_cast<void*>(&fake_c_noop_void);

    for (size_t index = 0; index < kChaperoneSetupSlots; ++index) {
        g_chaperone_setup005_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_chaperone_setup005_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_chaperone_setup005_vtable[0] = reinterpret_cast<void*>(&fake_commit_working_copy);
    g_chaperone_setup005_vtable[1] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_setup005_vtable[2] = reinterpret_cast<void*>(&fake_get_play_area_size);
    g_chaperone_setup005_vtable[3] = reinterpret_cast<void*>(&fake_get_play_area_rect);
    g_chaperone_setup005_vtable[4] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup005_vtable[5] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup005_vtable[6] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup005_vtable[7] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup005_vtable[8] = reinterpret_cast<void*>(&fake_set_play_area_size);
    g_chaperone_setup005_vtable[9] = reinterpret_cast<void*>(&fake_set_collision_bounds);
    g_chaperone_setup005_vtable[10] = reinterpret_cast<void*>(&fake_set_pose34);
    g_chaperone_setup005_vtable[11] = reinterpret_cast<void*>(&fake_set_pose34);
    g_chaperone_setup005_vtable[12] = reinterpret_cast<void*>(&fake_reload_from_disk);
    g_chaperone_setup005_vtable[13] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup005_vtable[14] = reinterpret_cast<void*>(&fake_set_tags);
    g_chaperone_setup005_vtable[15] = reinterpret_cast<void*>(&fake_get_tags);
    g_chaperone_setup005_vtable[16] = reinterpret_cast<void*>(&fake_set_physical_bounds);
    g_chaperone_setup005_vtable[17] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup005_vtable[18] = reinterpret_cast<void*>(&fake_export_buffer);
    g_chaperone_setup005_vtable[19] = reinterpret_cast<void*>(&fake_import_buffer);

    g_chaperone_setup005_fntable[0] = reinterpret_cast<void*>(&fake_c_commit_working_copy);
    g_chaperone_setup005_fntable[1] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_setup005_fntable[2] = reinterpret_cast<void*>(&fake_c_get_play_area_size);
    g_chaperone_setup005_fntable[3] = reinterpret_cast<void*>(&fake_c_get_play_area_rect);
    g_chaperone_setup005_fntable[4] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup005_fntable[5] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup005_fntable[6] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup005_fntable[7] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup005_fntable[8] = reinterpret_cast<void*>(&fake_c_set_play_area_size);
    g_chaperone_setup005_fntable[9] = reinterpret_cast<void*>(&fake_c_set_collision_bounds);
    g_chaperone_setup005_fntable[10] = reinterpret_cast<void*>(&fake_c_set_pose34);
    g_chaperone_setup005_fntable[11] = reinterpret_cast<void*>(&fake_c_set_pose34);
    g_chaperone_setup005_fntable[12] = reinterpret_cast<void*>(&fake_c_reload_from_disk);
    g_chaperone_setup005_fntable[13] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup005_fntable[14] = reinterpret_cast<void*>(&fake_c_set_tags);
    g_chaperone_setup005_fntable[15] = reinterpret_cast<void*>(&fake_c_get_tags);
    g_chaperone_setup005_fntable[16] = reinterpret_cast<void*>(&fake_c_set_physical_bounds);
    g_chaperone_setup005_fntable[17] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup005_fntable[18] = reinterpret_cast<void*>(&fake_c_export_buffer);
    g_chaperone_setup005_fntable[19] = reinterpret_cast<void*>(&fake_c_import_buffer);

    for (size_t index = 0; index < kOverlaySlots; ++index) {
        g_overlay_vtable[index] = reinterpret_cast<void*>(&fake_overlay_error);
        g_overlay_fntable[index] = reinterpret_cast<void*>(&fake_c_overlay_error);
    }
    g_overlay_vtable[0] = reinterpret_cast<void*>(&fake_overlay_find);
    g_overlay_vtable[1] = reinterpret_cast<void*>(&fake_overlay_create);
    g_overlay_vtable[2] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay_vtable[3] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay_vtable[4] = reinterpret_cast<void*>(&fake_overlay_handle);
    g_overlay_vtable[5] = reinterpret_cast<void*>(&fake_overlay_string);
    g_overlay_vtable[6] = reinterpret_cast<void*>(&fake_overlay_string);
    g_overlay_vtable[9] = reinterpret_cast<void*>(&fake_overlay_error_name);
    g_overlay_vtable[11] = reinterpret_cast<void*>(&fake_overlay_pid);
    g_overlay_vtable[41] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay_vtable[42] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay_vtable[43] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay_vtable[45] = reinterpret_cast<void*>(&fake_overlay_poll);
    g_overlay_vtable[66] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay_vtable[67] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay_vtable[70] = reinterpret_cast<void*>(&fake_ret0);

    g_overlay_fntable[0] = reinterpret_cast<void*>(&fake_c_overlay_find);
    g_overlay_fntable[1] = reinterpret_cast<void*>(&fake_c_overlay_create);
    g_overlay_fntable[2] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay_fntable[3] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay_fntable[4] = reinterpret_cast<void*>(&fake_c_overlay_handle);
    g_overlay_fntable[5] = reinterpret_cast<void*>(&fake_c_overlay_string);
    g_overlay_fntable[6] = reinterpret_cast<void*>(&fake_c_overlay_string);
    g_overlay_fntable[9] = reinterpret_cast<void*>(&fake_c_overlay_error_name);
    g_overlay_fntable[11] = reinterpret_cast<void*>(&fake_c_overlay_pid);
    g_overlay_fntable[41] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay_fntable[42] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay_fntable[43] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay_fntable[45] = reinterpret_cast<void*>(&fake_c_overlay_poll);
    g_overlay_fntable[66] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay_fntable[67] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay_fntable[70] = reinterpret_cast<void*>(&fake_c_ret0);

    for (size_t index = 0; index < kRenderModelsSlots; ++index) {
        g_render_models_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_render_models_fntable[0] = reinterpret_cast<void*>(&fake_c_render_models_load_model);
    g_render_models_fntable[1] = reinterpret_cast<void*>(&fake_c_render_models_free_model);
    g_render_models_fntable[2] = reinterpret_cast<void*>(&fake_c_render_models_load_texture);
    g_render_models_fntable[3] = reinterpret_cast<void*>(&fake_c_render_models_free_texture);
    g_render_models_fntable[4] = reinterpret_cast<void*>(&fake_c_render_models_load_texture_d3d11);
    g_render_models_fntable[5] = reinterpret_cast<void*>(&fake_c_render_models_load_into_texture_d3d11);
    g_render_models_fntable[6] = reinterpret_cast<void*>(&fake_c_render_models_free_texture_d3d11);
    g_render_models_fntable[7] = reinterpret_cast<void*>(&fake_c_render_models_get_model_name);
    g_render_models_fntable[8] = reinterpret_cast<void*>(&fake_c_render_models_get_model_count);
    g_render_models_fntable[9] = reinterpret_cast<void*>(&fake_c_render_models_get_component_count);
    g_render_models_fntable[10] = reinterpret_cast<void*>(&fake_c_render_models_get_component_name);
    g_render_models_fntable[11] = reinterpret_cast<void*>(&fake_c_render_models_get_component_button_mask);
    g_render_models_fntable[12] = reinterpret_cast<void*>(&fake_c_render_models_get_component_render_model_name);
    g_render_models_fntable[13] = reinterpret_cast<void*>(&fake_c_render_models_get_component_state_for_device_path);
    g_render_models_fntable[14] = reinterpret_cast<void*>(&fake_c_render_models_get_component_state);
    g_render_models_fntable[15] = reinterpret_cast<void*>(&fake_c_render_models_has_component);
    g_render_models_fntable[16] = reinterpret_cast<void*>(&fake_c_render_models_get_thumbnail_url);
    g_render_models_fntable[17] = reinterpret_cast<void*>(&fake_c_render_models_get_original_path);
    g_render_models_fntable[18] = reinterpret_cast<void*>(&fake_c_render_models_get_error_name);

    for (size_t index = 0; index < kScreenshotsSlots; ++index) {
        g_screenshots_fntable[index] = reinterpret_cast<void*>(&fake_c_screenshots_update_progress);
    }
    g_screenshots_fntable[0] = reinterpret_cast<void*>(&fake_c_screenshots_request);
    g_screenshots_fntable[1] = reinterpret_cast<void*>(&fake_c_screenshots_hook);
    g_screenshots_fntable[2] = reinterpret_cast<void*>(&fake_c_screenshots_get_type);
    g_screenshots_fntable[3] = reinterpret_cast<void*>(&fake_c_screenshots_get_filename);
    g_screenshots_fntable[4] = reinterpret_cast<void*>(&fake_c_screenshots_update_progress);
    g_screenshots_fntable[5] = reinterpret_cast<void*>(&fake_c_screenshots_take_stereo);
    g_screenshots_fntable[6] = reinterpret_cast<void*>(&fake_c_screenshots_submit);

    for (size_t index = 0; index < kLegacyInput005Slots; ++index) {
        g_input005_fntable[index] = reinterpret_cast<void*>(&fake_c_input_update_action_state);
    }
    g_input005_fntable[0] = reinterpret_cast<void*>(&fake_c_input_set_action_manifest_path);
    g_input005_fntable[1] = reinterpret_cast<void*>(&fake_c_input_get_action_set_handle);
    g_input005_fntable[2] = reinterpret_cast<void*>(&fake_c_input_get_action_handle);
    g_input005_fntable[3] = reinterpret_cast<void*>(&fake_c_input_get_source_handle);
    g_input005_fntable[4] = reinterpret_cast<void*>(&fake_c_input_update_action_state);
    g_input005_fntable[5] = reinterpret_cast<void*>(&fake_c_input_get_digital_action_data);
    g_input005_fntable[6] = reinterpret_cast<void*>(&fake_c_input_get_analog_action_data);
    g_input005_fntable[7] = reinterpret_cast<void*>(&fake_c_input_get_pose_action_data);
    g_input005_fntable[8] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_action_data);
    g_input005_fntable[9] = reinterpret_cast<void*>(&fake_c_input_get_bone_count);
    g_input005_fntable[10] = reinterpret_cast<void*>(&fake_c_input_get_bone_hierarchy);
    g_input005_fntable[11] = reinterpret_cast<void*>(&fake_c_input_get_bone_name);
    g_input005_fntable[12] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_reference_transforms);
    g_input005_fntable[13] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_tracking_level);
    g_input005_fntable[14] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_bone_data);
    g_input005_fntable[15] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_summary_data);
    g_input005_fntable[16] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_bone_data_compressed);
    g_input005_fntable[17] = reinterpret_cast<void*>(&fake_c_input_decompress_skeletal_bone_data);
    g_input005_fntable[18] = reinterpret_cast<void*>(&fake_c_input_trigger_haptic);
    g_input005_fntable[19] = reinterpret_cast<void*>(&fake_c_input_get_action_origins);
    g_input005_fntable[20] = reinterpret_cast<void*>(&fake_c_input_get_origin_localized_name);
    g_input005_fntable[21] = reinterpret_cast<void*>(&fake_c_input_get_origin_tracked_device_info);
    g_input005_fntable[22] = reinterpret_cast<void*>(&fake_c_input_show_action_origins);
    g_input005_fntable[23] = reinterpret_cast<void*>(&fake_c_input_show_bindings_for_action_set);
    g_input005_fntable[24] = reinterpret_cast<void*>(&fake_c_input_is_using_legacy_input);

    g_tables_initialized = true;
}

bool is_known_interface(const char* interface_version) {
    if (!interface_version) {
        return false;
    }
    if (is_system_interface(interface_version) || is_compositor_interface(interface_version)
        || is_chaperone_interface(interface_version) || is_chaperone_setup_interface(interface_version)
        || is_overlay_interface(interface_version)) {
        return true;
    }
    if (std::strncmp(interface_version, kFnTablePrefix, std::strlen(kFnTablePrefix)) != 0) {
        return false;
    }
    const char* version = interface_version + std::strlen(kFnTablePrefix);
    return is_system_interface(version) || is_compositor_interface(version) || is_chaperone_interface(version)
        || is_chaperone_setup_interface(version) || is_overlay_interface(version)
        || is_render_models_interface(version)
        || is_screenshots_interface(version)
        || is_legacy_input005_interface(version);
}

} // namespace

extern "C" __declspec(dllexport) uint32_t VR_InitInternal(
    vr::EVRInitError* error,
    vr::EVRApplicationType
) {
    ensure_tables_initialized();
    if (error) {
        *error = vr::VRInitError_None;
    }
    log_line("fake VR_InitInternal");
    return 1;
}

extern "C" __declspec(dllexport) uint32_t VR_InitInternal2(
    vr::EVRInitError* error,
    vr::EVRApplicationType application_type,
    const char*
) {
    return VR_InitInternal(error, application_type);
}

extern "C" __declspec(dllexport) void VR_ShutdownInternal() { log_line("fake VR_ShutdownInternal"); }
extern "C" __declspec(dllexport) bool VR_IsHmdPresent() { return true; }
extern "C" __declspec(dllexport) bool VR_IsRuntimeInstalled() { return true; }

extern "C" __declspec(dllexport) bool VR_GetRuntimePath(
    char* path_buffer,
    uint32_t buffer_size,
    uint32_t* required_size
) {
    const char* path = "C:\\FakeOpenVR";
    if (required_size) {
        *required_size = static_cast<uint32_t>(std::strlen(path) + 1);
    }
    if (!path_buffer || buffer_size <= std::strlen(path)) {
        return false;
    }
    std::strcpy(path_buffer, path);
    return true;
}

extern "C" __declspec(dllexport) const char* VR_RuntimePath() { return "C:\\FakeOpenVR"; }

extern "C" __declspec(dllexport) void* VR_GetGenericInterface(
    const char* interface_version,
    vr::EVRInitError* error
) {
    ensure_tables_initialized();
    if (error) {
        *error = vr::VRInitError_None;
    }
    if (is_legacy_system019_interface(interface_version)) {
        log_interface("IVRSystem", interface_version);
        return &g_system019;
    }
    if (is_system_interface(interface_version)) {
        log_interface("IVRSystem", interface_version);
        return &g_system;
    }
    if (is_compositor_interface(interface_version)) {
        log_interface("IVRCompositor", interface_version);
        return &g_compositor;
    }
    if (is_chaperone_interface(interface_version)) {
        log_interface("IVRChaperone", interface_version);
        return &g_chaperone;
    }
    if (is_legacy_chaperone_setup005_interface(interface_version)) {
        log_interface("IVRChaperoneSetup", interface_version);
        return &g_chaperone_setup005;
    }
    if (is_chaperone_setup_interface(interface_version)) {
        log_interface("IVRChaperoneSetup", interface_version);
        return &g_chaperone_setup;
    }
    if (is_overlay_interface(interface_version)) {
        log_interface("IVROverlay", interface_version);
        return &g_overlay;
    }
    if (interface_version
        && std::strncmp(interface_version, kFnTablePrefix, std::strlen(kFnTablePrefix)) == 0) {
        const char* version = interface_version + std::strlen(kFnTablePrefix);
        if (is_legacy_system019_interface(version)) {
            log_interface("FnTable IVRSystem", interface_version);
            return g_system019_fntable;
        }
        if (is_system_interface(version)) {
            log_interface("FnTable IVRSystem", interface_version);
            return g_system_fntable;
        }
        if (is_compositor_interface(version)) {
            log_interface("FnTable IVRCompositor", interface_version);
            return g_compositor_fntable;
        }
        if (is_chaperone_interface(version)) {
            log_interface("FnTable IVRChaperone", interface_version);
            return g_chaperone_fntable;
        }
        if (is_legacy_chaperone_setup005_interface(version)) {
            log_interface("FnTable IVRChaperoneSetup", interface_version);
            return g_chaperone_setup005_fntable;
        }
        if (is_chaperone_setup_interface(version)) {
            log_interface("FnTable IVRChaperoneSetup", interface_version);
            return g_chaperone_setup_fntable;
        }
        if (is_overlay_interface(version)) {
            log_interface("FnTable IVROverlay", interface_version);
            return g_overlay_fntable;
        }
        if (is_render_models_interface(version)) {
            log_interface("FnTable IVRRenderModels", interface_version);
            return g_render_models_fntable;
        }
        if (is_screenshots_interface(version)) {
            log_interface("FnTable IVRScreenshots", interface_version);
            return g_screenshots_fntable;
        }
        if (is_legacy_input005_interface(version)) {
            log_interface("FnTable IVRInput", interface_version);
            return g_input005_fntable;
        }
    }
    if (error) {
        *error = vr::VRInitError_Init_InterfaceNotFound;
    }
    char message[256] = {};
    std::snprintf(
        message,
        sizeof(message),
        "fake VR_GetGenericInterface unknown %s",
        interface_version ? interface_version : "<null>"
    );
    log_line(message);
    return nullptr;
}

extern "C" __declspec(dllexport) bool VR_IsInterfaceVersionValid(const char* interface_version) {
    return is_known_interface(interface_version);
}

extern "C" __declspec(dllexport) uint32_t VR_GetInitToken() { return 1; }
extern "C" __declspec(dllexport) const char* VR_GetVRInitErrorAsSymbol(vr::EVRInitError error) {
    return error == vr::VRInitError_None ? "None" : "InterfaceNotFound";
}
extern "C" __declspec(dllexport) const char* VR_GetVRInitErrorAsEnglishDescription(vr::EVRInitError error) {
    return error == vr::VRInitError_None ? "None" : "Interface not found";
}
extern "C" __declspec(dllexport) const char* VR_GetStringForHmdError(vr::EVRInitError error) {
    return VR_GetVRInitErrorAsEnglishDescription(error);
}
