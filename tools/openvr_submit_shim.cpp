#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <dxgi.h>
#include <openvr.h>
#include <wrl/client.h>

#include "dxvk_iosurface_submit_proof.h"
#include "shared/alvr_shm_protocol.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cctype>
#include <cstring>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

using Microsoft::WRL::ComPtr;

#ifndef OPENVR_FNTABLE_CALLTYPE
#define OPENVR_FNTABLE_CALLTYPE __stdcall
#endif

constexpr size_t kCompositorSubmitSlot = 5;
// IVRCompositor_027 exposes 51 methods in this OpenVR header; older Unity-era
// interfaces expose smaller tables. Copy only the requested interface surface so
// legacy callers do not read past their table.
constexpr size_t kCompositor027Slots = 51;
constexpr size_t kLegacyCompositor013Slots = 27;
constexpr size_t kLegacyCompositor014Slots = 29;
constexpr size_t kLegacyCompositor016Slots = 35;
constexpr size_t kLegacyCompositor022Slots = 43;
constexpr size_t kMaxCompositorSlots = kCompositor027Slots;
constexpr const char* kLegacyCompositor013 = "IVRCompositor_013";
constexpr const char* kLegacyCompositor014 = "IVRCompositor_014";
constexpr const char* kLegacyCompositor016 = "IVRCompositor_016";
constexpr const char* kLegacyCompositor022 = "IVRCompositor_022";
constexpr uint32_t kMinPackedEyeWidth = 16;
constexpr uint64_t kMaxBridgeHeartbeatAgeNs = 5'000'000'000ULL;
constexpr uint64_t kBridgeHeartbeatFutureToleranceNs = 250'000'000ULL;

using VR_InitInternalFn = uint32_t(__cdecl*)(vr::EVRInitError*, vr::EVRApplicationType);
using VR_InitInternal2Fn = uint32_t(__cdecl*)(vr::EVRInitError*, vr::EVRApplicationType, const char*);
using VR_ShutdownInternalFn = void(__cdecl*)();
using VR_IsHmdPresentFn = bool(__cdecl*)();
using VR_IsRuntimeInstalledFn = bool(__cdecl*)();
using VR_GetRuntimePathFn = bool(__cdecl*)(char*, uint32_t, uint32_t*);
using VR_RuntimePathFn = const char*(__cdecl*)();
using VR_GetGenericInterfaceFn = void*(__cdecl*)(const char*, vr::EVRInitError*);
using VR_IsInterfaceVersionValidFn = bool(__cdecl*)(const char*);
using VR_GetInitTokenFn = uint32_t(__cdecl*)();
using VR_GetErrorStringFn = const char*(__cdecl*)(vr::EVRInitError);

using CppSubmitFn = vr::EVRCompositorError(__thiscall*)(
    void*, vr::EVREye, const vr::Texture_t*, const vr::VRTextureBounds_t*, vr::EVRSubmitFlags
);
using CSubmitFn = vr::EVRCompositorError(OPENVR_FNTABLE_CALLTYPE*)(
    vr::EVREye, vr::Texture_t*, vr::VRTextureBounds_t*, vr::EVRSubmitFlags
);

struct FlatCompositorTable {
    void* slots[kMaxCompositorSlots];
};

static_assert(kCompositorSubmitSlot < kLegacyCompositor022Slots, "Submit slot must fit legacy IVRCompositor vtable");
static_assert(kCompositorSubmitSlot < kLegacyCompositor016Slots, "Submit slot must fit legacy IVRCompositor vtable");
static_assert(kCompositorSubmitSlot < kLegacyCompositor014Slots, "Submit slot must fit legacy IVRCompositor vtable");
static_assert(kCompositorSubmitSlot < kLegacyCompositor013Slots, "Submit slot must fit legacy IVRCompositor vtable");
static_assert(kCompositorSubmitSlot < kCompositor027Slots, "Submit slot must fit current IVRCompositor vtable");

struct EyeFrame {
    bool valid = false;
    uint32_t width = 0;
    uint32_t height = 0;
    uint64_t frame_number = 0;
    uint64_t submit_timestamp_ns = 0;
    uint32_t real_submit_us = 0;
    uint32_t capture_total_us = 0;
    uint32_t copy_resource_us = 0;
    uint32_t map_wait_us = 0;
    uint32_t copy_pixels_us = 0;
    bool needs_synthetic_fill = false;
    uint64_t synthetic_frame_number = UINT64_MAX;
    std::vector<uint8_t> bgra;
};

struct StagingCache {
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11Texture2D> texture;
    D3D11_TEXTURE2D_DESC desc = {};
};

struct TextureCrop {
    UINT x = 0;
    UINT y = 0;
    UINT width = 0;
    UINT height = 0;
};

struct CropResult {
    TextureCrop crop;
    bool used_fallback = false;
};

struct SubmitDiagnostic {
    uint64_t sequence = 0;
    vr::EVREye eye = vr::Eye_Left;
    vr::EVRSubmitFlags flags = vr::Submit_Default;
    vr::EVRCompositorError real_result = vr::VRCompositorError_None;
    const vr::Texture_t* texture = nullptr;
    const vr::VRTextureBounds_t* bounds = nullptr;
};

struct PoolEyeSubmission {
    bool valid = false;
    bool full_bounds = false;
    uintptr_t handle = 0;
    uint64_t sequence = 0;
    uint64_t submit_timestamp_ns = 0;
    D3D11_TEXTURE2D_DESC description = {};
    ComPtr<ID3D11Texture2D> texture;
    alvr_probe::SubmitProofPose pose;
};

struct SubmitSignature {
    uint32_t flags = 0;
    int real_result = 0;
    int texture_type = -999;
    uint32_t color_space = 0;
    uintptr_t handle = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t format = UINT32_MAX;
    uint32_t samples = 0;
    uint32_t array_size = 0;
    uint32_t mip_levels = 0;
    uint32_t bind_flags = 0;
    uint32_t usage = 0;
    uint32_t misc_flags = 0;
    bool has_bounds = false;
    float u_min = 0.0f;
    float v_min = 0.0f;
    float u_max = 1.0f;
    float v_max = 1.0f;
};

struct RejectionSignature {
    SubmitSignature submit;
    long hr = S_OK;
};

enum class RejectionKind : size_t {
    UnsupportedTextureType = 0,
    NotD3D11Texture2D,
    UnsupportedShape,
    UnsupportedFormat,
    Count,
};

HMODULE g_this_module = nullptr;
HMODULE g_real_openvr = nullptr;
std::mutex g_log_mutex;
std::mutex g_hook_mutex;
std::unordered_map<void*, CppSubmitFn> g_cpp_submit_by_object;
std::unordered_map<void*, FlatCompositorTable*> g_c_table_by_original;
CSubmitFn g_real_c_submit = nullptr;

void log_line(const char* format, ...) {
    std::lock_guard<std::mutex> lock(g_log_mutex);
    FILE* file = std::fopen("Z:\\tmp\\alvr_openvr_submit_shim.log", "ab");
    if (!file) {
        file = stderr;
    }

    SYSTEMTIME time;
    GetLocalTime(&time);
    std::fprintf(
        file,
        "%04u-%02u-%02u %02u:%02u:%02u.%03u ",
        time.wYear,
        time.wMonth,
        time.wDay,
        time.wHour,
        time.wMinute,
        time.wSecond,
        time.wMilliseconds
    );

    va_list args;
    va_start(args, format);
    std::vfprintf(file, format, args);
    va_end(args);
    std::fputc('\n', file);

    if (file != stderr) {
        std::fclose(file);
    }
}

std::string env_string(const char* name) {
    char buffer[4096] = {};
    DWORD len = GetEnvironmentVariableA(name, buffer, sizeof(buffer));
    if (len == 0 || len >= sizeof(buffer)) {
        return {};
    }
    return buffer;
}

bool env_enabled(const char* name) {
    std::string value = env_string(name);
    return value == "1" || value == "true" || value == "TRUE" || value == "yes" || value == "YES";
}

uint32_t env_u32(const char* name, uint32_t default_value) {
    std::string value = env_string(name);
    if (value.empty()) {
        return default_value;
    }

    const char* raw = value.c_str();
    while (*raw && std::isspace(static_cast<unsigned char>(*raw))) {
        ++raw;
    }
    if (!std::isdigit(static_cast<unsigned char>(*raw))) {
        log_line("ignoring invalid %s=%s", name, value.c_str());
        return default_value;
    }

    char* end = nullptr;
    unsigned long long parsed = std::strtoull(raw, &end, 10);
    if ((end && *end) || parsed > UINT32_MAX) {
        log_line("ignoring invalid %s=%s", name, value.c_str());
        return default_value;
    }
    return static_cast<uint32_t>(parsed);
}

uint32_t env_divisor(const char* name, uint32_t default_value) {
    uint32_t value = env_u32(name, default_value);
    if (value == 0) {
        return default_value;
    }
    return value;
}

std::string sibling_real_openvr_path() {
    char module_path[MAX_PATH] = {};
    DWORD len = GetModuleFileNameA(g_this_module, module_path, sizeof(module_path));
    if (len == 0 || len >= sizeof(module_path)) {
        return "openvr_api.real.dll";
    }

    std::string path(module_path);
    size_t slash = path.find_last_of("\\/");
    if (slash == std::string::npos) {
        return "openvr_api.real.dll";
    }
    return path.substr(0, slash + 1) + "openvr_api.real.dll";
}

bool load_real_openvr() {
    if (g_real_openvr) {
        return true;
    }

    std::string real_path = env_string("ALVR_OPENVR_REAL_DLL");
    if (real_path.empty()) {
        real_path = sibling_real_openvr_path();
    }

    g_real_openvr = LoadLibraryA(real_path.c_str());
    if (!g_real_openvr) {
        log_line("failed to load real OpenVR DLL from %s: %lu", real_path.c_str(), GetLastError());
        return false;
    }

    log_line("loaded real OpenVR DLL from %s", real_path.c_str());
    return true;
}

template <typename T>
T real_proc(const char* name) {
    if (!load_real_openvr()) {
        return nullptr;
    }
    FARPROC proc = GetProcAddress(g_real_openvr, name);
    if (!proc) {
        log_line("real OpenVR DLL missing export %s", name);
        return nullptr;
    }
    return reinterpret_cast<T>(proc);
}

std::string wine_shared_memory_path() {
    std::string path = "Z:" ALVR_SHM_PATH;
    for (char& ch : path) {
        if (ch == '/') {
            ch = '\\';
        }
    }
    return path;
}

bool is_bgra_format(DXGI_FORMAT format) {
    return format == DXGI_FORMAT_B8G8R8A8_UNORM || format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB
        || format == DXGI_FORMAT_B8G8R8A8_TYPELESS;
}

bool is_rgba_format(DXGI_FORMAT format) {
    return format == DXGI_FORMAT_R8G8B8A8_UNORM || format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB
        || format == DXGI_FORMAT_R8G8B8A8_TYPELESS;
}

double now_ms() {
    using clock = std::chrono::high_resolution_clock;
    static const auto start = clock::now();
    return std::chrono::duration<double, std::milli>(clock::now() - start).count();
}

uint64_t now_ns() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               clock::now().time_since_epoch()
    )
        .count();
}

uint32_t elapsed_us(std::chrono::steady_clock::time_point start, std::chrono::steady_clock::time_point end) {
    uint64_t micros = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(end - start).count()
    );
    return static_cast<uint32_t>(std::min<uint64_t>(micros, UINT32_MAX));
}

const char* texture_type_name(vr::ETextureType type) {
    switch (type) {
    case vr::TextureType_DirectX:
        return "DirectX";
    case vr::TextureType_OpenGL:
        return "OpenGL";
    case vr::TextureType_Vulkan:
        return "Vulkan";
    case vr::TextureType_IOSurface:
        return "IOSurface";
    case vr::TextureType_DirectX12:
        return "DirectX12";
    case vr::TextureType_DXGISharedHandle:
        return "DXGISharedHandle";
    case vr::TextureType_Metal:
        return "Metal";
    case vr::TextureType_Invalid:
        return "Invalid";
    default:
        return "Unknown";
    }
}

std::string submit_flags_string(vr::EVRSubmitFlags flags) {
    if (flags == vr::Submit_Default) {
        return "Submit_Default";
    }

    struct FlagName {
        vr::EVRSubmitFlags flag;
        const char* name;
    };
    const FlagName known_flags[] = {
        { vr::Submit_LensDistortionAlreadyApplied, "LensDistortionAlreadyApplied" },
        { vr::Submit_GlRenderBuffer, "GlRenderBuffer" },
        { vr::Submit_Reserved, "Reserved" },
        { vr::Submit_TextureWithPose, "TextureWithPose" },
        { vr::Submit_TextureWithDepth, "TextureWithDepth" },
    };

    uint32_t raw = static_cast<uint32_t>(flags);
    uint32_t remaining = raw;
    std::ostringstream stream;
    bool first = true;
    for (const FlagName& item : known_flags) {
        uint32_t bit = static_cast<uint32_t>(item.flag);
        if ((raw & bit) == 0) {
            continue;
        }
        if (!first) {
            stream << '|';
        }
        stream << item.name;
        first = false;
        remaining &= ~bit;
    }
    if (remaining != 0 || first) {
        if (!first) {
            stream << '|';
        }
        stream << "unknown:0x" << std::hex << remaining;
    }
    return stream.str();
}

void log_submit_diagnostic(
    const SubmitDiagnostic& diagnostic,
    const char* phase,
    const D3D11_TEXTURE2D_DESC* desc = nullptr,
    HRESULT hr = S_OK
) {
    const char* type_name = "none";
    int type_value = -999;
    void* handle = nullptr;
    uint32_t color_space = 0;
    if (diagnostic.texture) {
        type_name = texture_type_name(diagnostic.texture->eType);
        type_value = static_cast<int>(diagnostic.texture->eType);
        handle = diagnostic.texture->handle;
        color_space = static_cast<uint32_t>(diagnostic.texture->eColorSpace);
    }

    if (desc) {
        log_line(
            "Submit diagnostic seq=%llu phase=%s eye=%d flags=0x%x(%s) real_result=%d texture=%p type=%s(%d) color_space=%u handle=%p desc=%ux%u format=%u samples=%u array=%u mips=%u bind=0x%x usage=%u misc=0x%x bounds=%s raw=[%.4f %.4f %.4f %.4f] hr=0x%08lx",
            static_cast<unsigned long long>(diagnostic.sequence),
            phase,
            diagnostic.eye,
            static_cast<uint32_t>(diagnostic.flags),
            submit_flags_string(diagnostic.flags).c_str(),
            diagnostic.real_result,
            diagnostic.texture,
            type_name,
            type_value,
            color_space,
            handle,
            desc->Width,
            desc->Height,
            desc->Format,
            desc->SampleDesc.Count,
            desc->ArraySize,
            desc->MipLevels,
            desc->BindFlags,
            desc->Usage,
            desc->MiscFlags,
            diagnostic.bounds ? "provided" : "default",
            diagnostic.bounds ? diagnostic.bounds->uMin : 0.0f,
            diagnostic.bounds ? diagnostic.bounds->vMin : 0.0f,
            diagnostic.bounds ? diagnostic.bounds->uMax : 1.0f,
            diagnostic.bounds ? diagnostic.bounds->vMax : 1.0f,
            static_cast<unsigned long>(hr)
        );
        return;
    }

    log_line(
        "Submit diagnostic seq=%llu phase=%s eye=%d flags=0x%x(%s) real_result=%d texture=%p type=%s(%d) color_space=%u handle=%p bounds=%s raw=[%.4f %.4f %.4f %.4f] hr=0x%08lx",
        static_cast<unsigned long long>(diagnostic.sequence),
        phase,
        diagnostic.eye,
        static_cast<uint32_t>(diagnostic.flags),
        submit_flags_string(diagnostic.flags).c_str(),
        diagnostic.real_result,
        diagnostic.texture,
        type_name,
        type_value,
        color_space,
        handle,
        diagnostic.bounds ? "provided" : "default",
        diagnostic.bounds ? diagnostic.bounds->uMin : 0.0f,
        diagnostic.bounds ? diagnostic.bounds->vMin : 0.0f,
        diagnostic.bounds ? diagnostic.bounds->uMax : 1.0f,
        diagnostic.bounds ? diagnostic.bounds->vMax : 1.0f,
        static_cast<unsigned long>(hr)
    );
}

uint64_t unix_time_ns() {
    FILETIME file_time = {};
    GetSystemTimeAsFileTime(&file_time);
    ULARGE_INTEGER value = {};
    value.LowPart = file_time.dwLowDateTime;
    value.HighPart = file_time.dwHighDateTime;
    constexpr uint64_t kUnixEpochAsFiletime = 116444736000000000ULL;
    if (value.QuadPart < kUnixEpochAsFiletime) {
        return 0;
    }
    return (value.QuadPart - kUnixEpochAsFiletime) * 100ULL;
}

bool bridge_mapping_live(AlvrSharedMemory* shm) {
    if (!shm) {
        return false;
    }
    bool header_ready = shm->magic == ALVR_SHM_MAGIC && shm->version == ALVR_SHM_VERSION;
    bool bridge_ready = _InterlockedOr(reinterpret_cast<volatile long*>(&shm->initialized), 0) != 0;
    bool bridge_shutdown = _InterlockedOr(reinterpret_cast<volatile long*>(&shm->shutdown), 0) != 0;
    uint64_t session_id = shm->bridge_session_id;
    uint64_t heartbeat_ns = shm->bridge_heartbeat_ns;
    uint64_t now = unix_time_ns();
    bool heartbeat_ready = session_id != 0 && heartbeat_ns != 0
        && ((heartbeat_ns <= now && now - heartbeat_ns <= kMaxBridgeHeartbeatAgeNs)
            || (heartbeat_ns > now && heartbeat_ns - now <= kBridgeHeartbeatFutureToleranceNs));
    return header_ready && bridge_ready && !bridge_shutdown && heartbeat_ready;
}

bool wait_for_bridge_ready(AlvrSharedMemory* shm, int timeout_ms) {
    double start = now_ms();
    do {
        if (bridge_mapping_live(shm)) {
            return true;
        }
        if (timeout_ms <= 0 || now_ms() - start >= timeout_ms) {
            break;
        }
        Sleep(10);
    } while (true);
    return false;
}

bool valid_pose_matrix(const float matrix[3][4]) {
    for (int row = 0; row < 3; ++row) {
        for (int col = 0; col < 4; ++col) {
            if (!std::isfinite(matrix[row][col]) || std::fabs(matrix[row][col]) > 1000.0f) {
                return false;
            }
        }
    }

    auto row_len_sq = [&](int row) {
        return matrix[row][0] * matrix[row][0] + matrix[row][1] * matrix[row][1]
            + matrix[row][2] * matrix[row][2];
    };
    auto row_dot = [&](int a, int b) {
        return matrix[a][0] * matrix[b][0] + matrix[a][1] * matrix[b][1]
            + matrix[a][2] * matrix[b][2];
    };

    return row_len_sq(0) >= 0.5f && row_len_sq(0) <= 1.5f && row_len_sq(1) >= 0.5f
        && row_len_sq(1) <= 1.5f && row_len_sq(2) >= 0.5f && row_len_sq(2) <= 1.5f
        && std::fabs(row_dot(0, 1)) <= 0.2f && std::fabs(row_dot(0, 2)) <= 0.2f
        && std::fabs(row_dot(1, 2)) <= 0.2f;
}

struct PoseSnapshot {
    uint32_t sequence = 0;
    uint64_t generation = 0;
    uint64_t session_id = 0;
    uint64_t timestamp_ns = 0;
    float matrix[3][4] = {};
};

bool read_frame_pose_snapshot(const AlvrSharedMemory* shm, PoseSnapshot* pose) {
    if (!shm || !pose) {
        return false;
    }
    auto* frame_pose_sequence = reinterpret_cast<volatile const uint32_t*>(&shm->frame_pose_sequence);

    for (int attempt = 0; attempt < 3; ++attempt) {
        uint32_t before = *frame_pose_sequence;
        if (before == 0 || (before & 1U) != 0) {
            continue;
        }

        std::atomic_thread_fence(std::memory_order_acquire);
        uint64_t session_before = shm->bridge_session_id;
        uint64_t timestamp = shm->frame_pose_timestamp_ns;
        float matrix[3][4] = {};
        std::memcpy(matrix, shm->frame_pose, sizeof(matrix));
        std::atomic_thread_fence(std::memory_order_acquire);

        uint32_t after = *frame_pose_sequence;
        uint64_t session_after = shm->bridge_session_id;
        if (before != after || (after & 1U) != 0 || session_before == 0
            || session_before != session_after) {
            continue;
        }
        if (timestamp == 0 || !valid_pose_matrix(matrix)) {
            return false;
        }

        pose->sequence = after;
        pose->generation = after / 2;
        pose->session_id = session_after;
        pose->timestamp_ns = timestamp;
        std::memcpy(pose->matrix, matrix, sizeof(pose->matrix));
        return true;
    }

    return false;
}

const char* view_params_source(const AlvrSharedMemory* shm, bool has_frame_pose) {
    if (!shm) {
        return "missing-shared-memory";
    }
    const bool has_view_config = shm->view_config_set != 0;
    const bool has_hmd_pose = shm->hmd_pose_set != 0;
    if (has_view_config && has_hmd_pose && has_frame_pose) {
        return "shared-view-shared-hmd-pose-frame-pose";
    }
    if (has_view_config && has_hmd_pose) {
        return "shared-view-shared-hmd-pose-missing-frame-pose";
    }
    if (has_view_config) {
        return "shared-view-missing-hmd-pose";
    }
    return "bridge-fallback-or-missing-view";
}

const char* contract_unknown_fields(const AlvrSharedMemory* shm, bool has_frame_pose) {
    if (!shm) {
        return "shared_memory,view_params,render_pose_generation,clock_alignment,tracking_space_return,projection_matrix_return,exact_app_render_pose_pairing";
    }
    const bool has_view_config = shm->view_config_set != 0;
    const bool has_hmd_pose = shm->hmd_pose_set != 0;
    if (has_view_config && has_hmd_pose && has_frame_pose) {
        return "clock_alignment,tracking_space_return,projection_matrix_return,exact_app_render_pose_pairing";
    }
    if (has_view_config && has_hmd_pose) {
        return "render_pose_generation,frame_pose_timestamp,clock_alignment,tracking_space_return,projection_matrix_return,exact_app_render_pose_pairing";
    }
    if (has_view_config) {
        return "hmd_pose,render_pose_generation,frame_pose_timestamp,clock_alignment,tracking_space_return,projection_matrix_return,exact_app_render_pose_pairing";
    }
    return "view_params,hmd_pose,render_pose_generation,frame_pose_timestamp,clock_alignment,tracking_space_return,projection_matrix_return,exact_app_render_pose_pairing";
}

class SharedMemorySubmitWriter {
public:
    SharedMemorySubmitWriter()
        : m_inner_crop_px(env_u32("ALVR_SHIM_INNER_CROP_PX", 0)),
          m_scale_divisor(env_divisor("ALVR_SHIM_SCALE_DIVISOR", 1)),
          m_synthetic_frame(env_enabled("ALVR_SHIM_SYNTHETIC_FRAME")) {
        if (m_inner_crop_px != 0) {
            log_line("using inner-eye packing crop=%u px", m_inner_crop_px);
        }
        if (m_scale_divisor != 1) {
            log_line("using diagnostic packed-frame scale divisor=%u", m_scale_divisor);
        }
        if (m_synthetic_frame) {
            log_line("using diagnostic synthetic frame; D3D11 readback disabled");
        }
    }

    ~SharedMemorySubmitWriter() { close(); }

    void capture_submit(
        vr::EVREye eye,
        const vr::Texture_t* texture,
        const vr::VRTextureBounds_t* bounds,
        vr::EVRSubmitFlags flags,
        vr::EVRCompositorError real_result,
        uint64_t submit_timestamp_ns,
        uint32_t real_submit_us
    ) {
        SubmitDiagnostic diagnostic;
        diagnostic.sequence = m_submit_diagnostic_counter.fetch_add(1, std::memory_order_relaxed) + 1;
        diagnostic.eye = eye;
        diagnostic.flags = flags;
        diagnostic.real_result = real_result;
        diagnostic.texture = texture;
        diagnostic.bounds = bounds;

        if (!texture || texture->eType != vr::TextureType_DirectX || !texture->handle) {
            if (should_log_rejection(diagnostic, RejectionKind::UnsupportedTextureType)) {
                log_submit_diagnostic(diagnostic, "unsupported-texture-type");
            }
            return;
        }

        if (should_log_submit_metadata(diagnostic)) {
            log_submit_diagnostic(diagnostic, "submit");
        }

        ComPtr<ID3D11Texture2D> submitted;
        HRESULT hr = static_cast<IUnknown*>(texture->handle)->QueryInterface(
            __uuidof(ID3D11Texture2D),
            reinterpret_cast<void**>(submitted.GetAddressOf())
        );
        if (FAILED(hr) || !submitted) {
            if (should_log_rejection(diagnostic, RejectionKind::NotD3D11Texture2D, nullptr, hr)) {
                log_submit_diagnostic(diagnostic, "not-id3d11texture2d", nullptr, hr);
            }
            return;
        }

        if (m_iosurface_proof.usesPool()) {
            maybe_capture_iosurface_pool(
                submitted.Get(), diagnostic, submit_timestamp_ns);
            return;
        }

        EyeFrame frame;
        {
            std::lock_guard<std::mutex> capture_lock(m_capture_mutex);
            if (!read_eye_texture(submitted.Get(), diagnostic, &frame)) {
                return;
            }
            maybe_capture_iosurface_proof(
                submitted.Get(), diagnostic, frame, submit_timestamp_ns);
        }

        std::lock_guard<std::mutex> lock(m_mutex);
        frame.frame_number = ++m_submit_counter;
        frame.submit_timestamp_ns = submit_timestamp_ns;
        frame.real_submit_us = real_submit_us;
        if (eye == vr::Eye_Left) {
            m_left = std::move(frame);
        } else if (eye == vr::Eye_Right) {
            m_right = std::move(frame);
        } else {
            return;
        }

        publish_pair_if_ready_locked();
    }

    void shutdown() {
        m_iosurface_proof.shutdown();
    }

private:
    bool submit_bounds_cover_full_texture(
        const vr::VRTextureBounds_t* bounds
    ) const {
        if (!bounds) {
            return false;
        }
        constexpr float epsilon = 0.0001f;
        return std::fabs(bounds->uMin) <= epsilon
            && std::fabs(bounds->vMin) <= epsilon
            && std::fabs(bounds->uMax - 1.0f) <= epsilon
            && std::fabs(bounds->vMax - 1.0f) <= epsilon;
    }

    alvr_probe::SubmitProofPose pool_pose_for_submit(
        const D3D11_TEXTURE2D_DESC& desc,
        uint64_t submit_timestamp_ns
    ) {
        PoseSnapshot frame_pose;
        bool has_frame_pose = false;
        uint64_t paired_pose_generation = 0;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            has_frame_pose = ensure_mapped_locked(
                desc.Width, desc.Height, sizeof(AlvrSharedMemory))
                && read_frame_pose_snapshot(m_shm, &frame_pose);
            if (has_frame_pose) {
                if (frame_pose.session_id != m_pool_pose_session_id) {
                    m_pool_pose_session_id = frame_pose.session_id;
                    m_last_pool_source_pose_generation = 0;
                }
                has_frame_pose =
                    frame_pose.generation > m_last_pool_source_pose_generation;
                if (has_frame_pose) {
                    m_last_pool_source_pose_generation = frame_pose.generation;
                    paired_pose_generation = ++m_pool_pose_generation;
                }
            }
        }
        if (!has_frame_pose) {
            uint64_t missing_pose_count =
                m_missing_pool_pose_count.fetch_add(1, std::memory_order_relaxed) + 1;
            if (missing_pose_count == 1 || missing_pose_count % 300 == 0) {
                log_line(
                    "iosurface pool using fallback identity pose count=%llu",
                    static_cast<unsigned long long>(missing_pose_count)
                );
            }
        }

        alvr_probe::SubmitProofPose pose;
        if (has_frame_pose) {
            pose.timestampNs = frame_pose.timestamp_ns;
            pose.generation = paired_pose_generation;
            pose.sourceSessionId = frame_pose.session_id;
            pose.sourceGeneration = frame_pose.generation;
            std::memcpy(pose.matrix.data(), frame_pose.matrix, sizeof(frame_pose.matrix));
        } else {
            pose.timestampNs = submit_timestamp_ns;
            pose.fallback = true;
            pose.matrix = {
                1.0f, 0.0f, 0.0f, 0.0f,
                0.0f, 1.0f, 0.0f, 0.0f,
                0.0f, 0.0f, 1.0f, 0.0f,
            };
        }
        return pose;
    }

    void maybe_capture_iosurface_pool(
        ID3D11Texture2D* texture,
        const SubmitDiagnostic& diagnostic,
        uint64_t submit_timestamp_ns
    ) {
        if (diagnostic.real_result != vr::VRCompositorError_None
            || !m_iosurface_proof.pending()) {
            return;
        }

        D3D11_TEXTURE2D_DESC desc = {};
        texture->GetDesc(&desc);
        if (should_log_submit_desc(diagnostic, desc)) {
            log_submit_diagnostic(diagnostic, "d3d11-desc", &desc);
        }
        if (!is_bgra_format(desc.Format) && !is_rgba_format(desc.Format)) {
            return;
        }

        if (diagnostic.eye == vr::Eye_Left) {
            PoolEyeSubmission pending;
            pending.valid = true;
            pending.full_bounds = submit_bounds_cover_full_texture(
                diagnostic.bounds);
            pending.handle = reinterpret_cast<uintptr_t>(
                diagnostic.texture ? diagnostic.texture->handle : nullptr);
            pending.sequence = diagnostic.sequence;
            pending.submit_timestamp_ns = submit_timestamp_ns;
            pending.description = desc;
            pending.texture = texture;
            pending.pose = pool_pose_for_submit(desc, submit_timestamp_ns);
            std::lock_guard<std::mutex> lock(m_pool_pair_mutex);
            if (m_pool_pending_left.valid) {
                uint64_t dropped = m_pool_pair_drops.fetch_add(
                    1, std::memory_order_relaxed) + 1;
                if (dropped <= 5 || dropped % 120 == 0) {
                    log_line(
                        "iosurface pool pair drop reason=left-overwrite "
                        "previous_sequence=%llu new_sequence=%llu dropped=%llu",
                        static_cast<unsigned long long>(
                            m_pool_pending_left.sequence),
                        static_cast<unsigned long long>(diagnostic.sequence),
                        static_cast<unsigned long long>(dropped)
                    );
                }
            }
            m_pool_pending_left = pending;
            return;
        }
        if (diagnostic.eye != vr::Eye_Right) {
            return;
        }

        PoolEyeSubmission left;
        {
            std::lock_guard<std::mutex> lock(m_pool_pair_mutex);
            left = m_pool_pending_left;
            m_pool_pending_left = {};
        }
        const bool adjacent = left.valid
            && diagnostic.sequence == left.sequence + 1;
        const uintptr_t right_handle = reinterpret_cast<uintptr_t>(
            diagnostic.texture ? diagnostic.texture->handle : nullptr);
        const bool same_texture = left.handle != 0
            && left.handle == right_handle;
        const bool compatible_pair = left.valid
            && left.description.Width == desc.Width
            && left.description.Height == desc.Height
            && left.description.Format == desc.Format
            && left.full_bounds
            && submit_bounds_cover_full_texture(diagnostic.bounds);
        if (!adjacent || (!same_texture && !compatible_pair)
            || (same_texture && left.full_bounds)) {
            uint64_t dropped = m_pool_pair_drops.fetch_add(
                1, std::memory_order_relaxed) + 1;
            if (dropped <= 5 || dropped % 120 == 0) {
                log_line(
                    "iosurface pool pair drop reason=%s left_sequence=%llu "
                    "right_sequence=%llu left_handle=%p right_handle=%p "
                    "left=%ux%u right=%ux%u dropped=%llu",
                    !adjacent
                        ? "nonadjacent"
                        : (same_texture ? "full-bounds-alias" : "incompatible"),
                    static_cast<unsigned long long>(left.sequence),
                    static_cast<unsigned long long>(diagnostic.sequence),
                    reinterpret_cast<void*>(left.handle),
                    reinterpret_cast<void*>(right_handle),
                    left.description.Width,
                    left.description.Height,
                    desc.Width,
                    desc.Height,
                    static_cast<unsigned long long>(dropped)
                );
            }
            return;
        }

        if (same_texture) {
            if (!is_bgra_format(desc.Format)) {
                return;
            }
            m_iosurface_proof.capturePoolFrame(
                left.texture.Get(),
                diagnostic.sequence,
                static_cast<uint32_t>(vr::Eye_Left),
                submit_timestamp_ns,
                left.pose
            );
            return;
        }

        m_iosurface_proof.capturePoolFramePair(
            left.texture.Get(),
            texture,
            diagnostic.sequence,
            submit_timestamp_ns,
            left.pose
        );
    }

    void maybe_capture_iosurface_proof(
        ID3D11Texture2D* texture,
        const SubmitDiagnostic& diagnostic,
        const EyeFrame& frame,
        uint64_t submit_timestamp_ns
    ) {
        if (diagnostic.eye != vr::Eye_Left
            || diagnostic.real_result != vr::VRCompositorError_None
            || !frame.valid || frame.needs_synthetic_fill
            || !m_iosurface_proof.pending()) {
            return;
        }

        D3D11_TEXTURE2D_DESC desc = {};
        texture->GetDesc(&desc);
        if (!is_bgra_format(desc.Format)) {
            return;
        }

        CropResult crop_result = texture_crop(desc, diagnostic.eye, diagnostic.bounds);
        const TextureCrop& crop = crop_result.crop;
        if (crop_result.used_fallback || crop.width != frame.width || crop.height != frame.height) {
            return;
        }

        uint32_t sample_x = 0;
        uint32_t sample_y = 0;
        uint32_t sample_brightness = 0;
        std::array<uint8_t, ALVR_BYTES_PER_PIXEL> sample_bgra {};
        bool found_sample = false;

        auto consider_sample = [&](uint32_t x, uint32_t y) {
            size_t offset = (static_cast<size_t>(y) * frame.width + x) * ALVR_BYTES_PER_PIXEL;
            const uint8_t* pixel = frame.bgra.data() + offset;
            uint32_t brightness = static_cast<uint32_t>(pixel[0])
                + static_cast<uint32_t>(pixel[1]) + static_cast<uint32_t>(pixel[2]);
            if (brightness < 96 || (found_sample && brightness <= sample_brightness)) {
                return;
            }

            found_sample = true;
            sample_x = x;
            sample_y = y;
            sample_brightness = brightness;
            std::copy_n(pixel, sample_bgra.size(), sample_bgra.begin());
        };

        constexpr uint32_t sample_step = 16;
        for (uint32_t y = sample_step / 2; y < frame.height; y += sample_step) {
            for (uint32_t x = sample_step / 2; x < frame.width; x += sample_step) {
                consider_sample(x, y);
            }
        }

        uint64_t eye_sequence = (diagnostic.sequence + 1) / 2;
        bool used_full_scan = !found_sample
            && (eye_sequence <= 5 || eye_sequence % 120 == 0);
        if (used_full_scan) {
            for (uint32_t y = 0; y < frame.height; ++y) {
                for (uint32_t x = 0; x < frame.width; ++x) {
                    consider_sample(x, y);
                }
            }
        }
        if (!found_sample) {
            return;
        }

        alvr_probe::SubmitProofSample sample;
        sample.x = crop.x + sample_x;
        sample.y = crop.y + sample_y;
        sample.bgra = sample_bgra;
        log_line(
            "iosurface proof selected sample sequence=%llu local=%u,%u full=%u,%u "
            "bgra=%u,%u,%u,%u brightness=%u scan=%s",
            static_cast<unsigned long long>(diagnostic.sequence),
            sample_x,
            sample_y,
            sample.x,
            sample.y,
            sample.bgra[0],
            sample.bgra[1],
            sample.bgra[2],
            sample.bgra[3],
            sample_brightness,
            used_full_scan ? "full" : "coarse"
        );
        m_iosurface_proof.captureOnce(
            texture,
            diagnostic.sequence,
            static_cast<uint32_t>(diagnostic.eye),
            sample,
            submit_timestamp_ns
        );
    }

    bool read_eye_texture(
        ID3D11Texture2D* texture,
        const SubmitDiagnostic& diagnostic,
        EyeFrame* frame
    ) {
        auto capture_start = std::chrono::steady_clock::now();
        D3D11_TEXTURE2D_DESC desc = {};
        texture->GetDesc(&desc);
        vr::EVREye eye = diagnostic.eye;
        const vr::VRTextureBounds_t* bounds = diagnostic.bounds;
        size_t eye_index = eye == vr::Eye_Right ? 1 : 0;
        uint64_t texture_logs_seen = m_submit_texture_logs_seen[eye_index].fetch_add(1, std::memory_order_relaxed) + 1;

        if (should_log_submit_desc(diagnostic, desc)) {
            log_submit_diagnostic(diagnostic, "d3d11-desc", &desc);
        }

        if (desc.SampleDesc.Count != 1 || desc.ArraySize != 1 || desc.MipLevels < 1) {
            if (should_log_rejection(diagnostic, RejectionKind::UnsupportedShape, &desc)) {
                log_submit_diagnostic(diagnostic, "unsupported-shape", &desc);
            }
            return false;
        }
        if (!is_bgra_format(desc.Format) && !is_rgba_format(desc.Format)) {
            if (should_log_rejection(diagnostic, RejectionKind::UnsupportedFormat, &desc)) {
                log_submit_diagnostic(diagnostic, "unsupported-format", &desc);
            }
            return false;
        }

        CropResult crop_result = texture_crop(desc, eye, bounds);
        TextureCrop crop = crop_result.crop;
        if (texture_logs_seen <= 8 || texture_logs_seen % 180 == 0) {
            log_line(
                "Submit crop eye=%d texture=%p size=%ux%u format=%u bounds=%s raw=[%.4f %.4f %.4f %.4f] crop=%u,%u %ux%u fallback=%u",
                eye,
                texture,
                desc.Width,
                desc.Height,
                desc.Format,
                bounds ? "provided" : "default",
                bounds ? bounds->uMin : 0.0f,
                bounds ? bounds->vMin : 0.0f,
                bounds ? bounds->uMax : 1.0f,
                bounds ? bounds->vMax : 1.0f,
                crop.x,
                crop.y,
                crop.width,
                crop.height,
                crop_result.used_fallback ? 1 : 0
            );
        }
        if (crop.width == 0 || crop.height == 0) {
            log_line(
                "unsupported Submit texture eye=%d invalid bounds size=%ux%u bounds=%s raw=[%.4f %.4f %.4f %.4f]",
                eye,
                desc.Width,
                desc.Height,
                bounds ? "provided" : "default",
                bounds ? bounds->uMin : 0.0f,
                bounds ? bounds->vMin : 0.0f,
                bounds ? bounds->uMax : 1.0f,
                bounds ? bounds->vMax : 1.0f
            );
            return false;
        }
        if (crop_result.used_fallback) {
            log_line(
                "using fallback Submit crop eye=%d raw=[%.4f %.4f %.4f %.4f] crop=%u,%u %ux%u texture=%ux%u",
                eye,
                bounds ? bounds->uMin : 0.0f,
                bounds ? bounds->vMin : 0.0f,
                bounds ? bounds->uMax : 1.0f,
                bounds ? bounds->vMax : 1.0f,
                crop.x,
                crop.y,
                crop.width,
                crop.height,
                desc.Width,
                desc.Height
            );
        }

        if (m_synthetic_frame) {
            frame->valid = true;
            frame->width = crop.width;
            frame->height = crop.height;
            frame->bgra.resize(static_cast<size_t>(crop.width) * crop.height * ALVR_BYTES_PER_PIXEL);
            frame->copy_resource_us = 0;
            frame->map_wait_us = 0;
            frame->copy_pixels_us = 0;
            frame->capture_total_us = elapsed_us(capture_start, std::chrono::steady_clock::now());
            frame->needs_synthetic_fill = true;
            log_frame_stats(eye, *frame);
            return true;
        }

        StagingCache& cache = eye == vr::Eye_Left ? m_left_staging : m_right_staging;
        ComPtr<ID3D11Device> device;
        texture->GetDevice(&device);
        if (!device) {
            return false;
        }

        bool needs_staging = !cache.texture || cache.desc.Width != desc.Width
            || cache.desc.Height != desc.Height || cache.desc.Format != desc.Format
            || cache.device.Get() != device.Get();
        if (needs_staging) {
            cache = StagingCache {};
            cache.device = device;
            device->GetImmediateContext(&cache.context);
            cache.desc = desc;
            cache.desc.Usage = D3D11_USAGE_STAGING;
            cache.desc.BindFlags = 0;
            cache.desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            cache.desc.MiscFlags = 0;

            HRESULT hr = device->CreateTexture2D(&cache.desc, nullptr, &cache.texture);
            if (FAILED(hr)) {
                log_submit_diagnostic(diagnostic, "staging-create-failed", &desc, hr);
                cache = StagingCache {};
                return false;
            }
            log_line("staging ready eye=%d format=%u size=%ux%u", eye, desc.Format, desc.Width, desc.Height);
        }

        auto copy_resource_start = std::chrono::steady_clock::now();
        cache.context->CopyResource(cache.texture.Get(), texture);
        auto copy_resource_done = std::chrono::steady_clock::now();
        D3D11_MAPPED_SUBRESOURCE mapped = {};
        auto map_start = std::chrono::steady_clock::now();
        HRESULT hr = cache.context->Map(cache.texture.Get(), 0, D3D11_MAP_READ, 0, &mapped);
        auto map_done = std::chrono::steady_clock::now();
        if (FAILED(hr)) {
            log_submit_diagnostic(diagnostic, "staging-map-failed", &desc, hr);
            return false;
        }

        frame->valid = true;
        frame->width = crop.width;
        frame->height = crop.height;
        frame->bgra.resize(static_cast<size_t>(crop.width) * crop.height * ALVR_BYTES_PER_PIXEL);
        auto copy_pixels_start = std::chrono::steady_clock::now();
        copy_to_bgra(frame->bgra.data(), static_cast<const uint8_t*>(mapped.pData), mapped.RowPitch, desc, crop);
        auto copy_pixels_done = std::chrono::steady_clock::now();
        frame->copy_resource_us = elapsed_us(copy_resource_start, copy_resource_done);
        frame->map_wait_us = elapsed_us(map_start, map_done);
        frame->copy_pixels_us = elapsed_us(copy_pixels_start, copy_pixels_done);
        frame->capture_total_us = elapsed_us(capture_start, copy_pixels_done);
        log_frame_stats(eye, *frame);
        cache.context->Unmap(cache.texture.Get(), 0);
        return true;
    }

    bool should_log_submit_metadata(const SubmitDiagnostic& diagnostic) {
        SubmitSignature signature = submit_signature(diagnostic, nullptr, false);
        size_t eye_index = diagnostic.eye == vr::Eye_Right ? 1 : 0;
        std::lock_guard<std::mutex> lock(m_diagnostic_mutex);
        return should_log_signature(
            diagnostic.sequence,
            m_submit_metadata_signatures[eye_index],
            m_submit_metadata_counts[eye_index],
            signature
        );
    }

    bool should_log_submit_desc(const SubmitDiagnostic& diagnostic, const D3D11_TEXTURE2D_DESC& desc) {
        SubmitSignature signature = submit_signature(diagnostic, &desc, false);
        size_t eye_index = diagnostic.eye == vr::Eye_Right ? 1 : 0;
        std::lock_guard<std::mutex> lock(m_diagnostic_mutex);
        return should_log_signature(
            diagnostic.sequence,
            m_submit_desc_signatures[eye_index],
            m_submit_desc_counts[eye_index],
            signature
        );
    }

    SubmitSignature submit_signature(
        const SubmitDiagnostic& diagnostic,
        const D3D11_TEXTURE2D_DESC* desc = nullptr,
        bool include_handle = true
    ) {
        SubmitSignature signature;
        signature.flags = static_cast<uint32_t>(diagnostic.flags);
        signature.real_result = static_cast<int>(diagnostic.real_result);
        if (diagnostic.texture) {
            signature.texture_type = static_cast<int>(diagnostic.texture->eType);
            signature.color_space = static_cast<uint32_t>(diagnostic.texture->eColorSpace);
            if (include_handle) {
                signature.handle = reinterpret_cast<uintptr_t>(diagnostic.texture->handle);
            }
        }
        if (diagnostic.bounds) {
            signature.has_bounds = true;
            signature.u_min = diagnostic.bounds->uMin;
            signature.v_min = diagnostic.bounds->vMin;
            signature.u_max = diagnostic.bounds->uMax;
            signature.v_max = diagnostic.bounds->vMax;
        }
        if (desc) {
            signature.width = desc->Width;
            signature.height = desc->Height;
            signature.format = static_cast<uint32_t>(desc->Format);
            signature.samples = desc->SampleDesc.Count;
            signature.array_size = desc->ArraySize;
            signature.mip_levels = desc->MipLevels;
            signature.bind_flags = desc->BindFlags;
            signature.usage = desc->Usage;
            signature.misc_flags = desc->MiscFlags;
        }
        return signature;
    }

    bool same_signature(const SubmitSignature& left, const SubmitSignature& right) {
        return left.flags == right.flags && left.real_result == right.real_result
            && left.texture_type == right.texture_type && left.color_space == right.color_space
            && left.handle == right.handle && left.width == right.width && left.height == right.height
            && left.format == right.format && left.samples == right.samples
            && left.array_size == right.array_size && left.mip_levels == right.mip_levels
            && left.bind_flags == right.bind_flags && left.usage == right.usage
            && left.misc_flags == right.misc_flags && left.has_bounds == right.has_bounds
            && left.u_min == right.u_min && left.v_min == right.v_min
            && left.u_max == right.u_max && left.v_max == right.v_max;
    }

    bool should_log_signature(
        uint64_t sequence,
        SubmitSignature& last_signature,
        uint64_t& signature_count,
        const SubmitSignature& signature
    ) {
        bool changed = !same_signature(last_signature, signature);
        if (changed) {
            last_signature = signature;
            signature_count = 0;
        }
        ++signature_count;
        return sequence <= 12 || changed || signature_count <= 8 || signature_count % 240 == 0;
    }

    bool should_log_rejection(
        const SubmitDiagnostic& diagnostic,
        RejectionKind kind,
        const D3D11_TEXTURE2D_DESC* desc = nullptr,
        HRESULT hr = S_OK
    ) {
        RejectionSignature signature;
        signature.submit = submit_signature(diagnostic, desc, false);
        signature.hr = static_cast<long>(hr);

        size_t eye_index = diagnostic.eye == vr::Eye_Right ? 1 : 0;
        size_t kind_index = static_cast<size_t>(kind);
        std::lock_guard<std::mutex> lock(m_diagnostic_mutex);
        RejectionSignature& last_signature = m_rejection_signatures[eye_index][kind_index];
        uint64_t& count = m_rejection_counts[eye_index][kind_index];
        bool changed = !same_rejection_signature(last_signature, signature);
        if (changed) {
            last_signature = signature;
            count = 0;
        }
        ++count;
        return changed || count <= 8 || count % 120 == 0;
    }

    bool same_rejection_signature(const RejectionSignature& left, const RejectionSignature& right) {
        return left.hr == right.hr && same_signature(left.submit, right.submit);
    }

    CropResult texture_crop(const D3D11_TEXTURE2D_DESC& desc, vr::EVREye eye, const vr::VRTextureBounds_t* bounds) {
        if (!bounds) {
            return CropResult { TextureCrop { 0, 0, desc.Width, desc.Height }, false };
        }

        float u_min = std::max(0.0f, std::min(bounds->uMin, bounds->uMax));
        float u_max = std::min(1.0f, std::max(bounds->uMin, bounds->uMax));
        float v_min = std::max(0.0f, std::min(bounds->vMin, bounds->vMax));
        float v_max = std::min(1.0f, std::max(bounds->vMin, bounds->vMax));
        if (u_max <= u_min || v_max <= v_min) {
            return fallback_double_wide_crop(desc, eye, bounds);
        }

        UINT x0 = static_cast<UINT>(std::floor(u_min * desc.Width));
        UINT x1 = static_cast<UINT>(std::ceil(u_max * desc.Width));
        UINT y0 = static_cast<UINT>(std::floor(v_min * desc.Height));
        UINT y1 = static_cast<UINT>(std::ceil(v_max * desc.Height));
        x0 = std::min(x0, desc.Width);
        x1 = std::min(x1, desc.Width);
        y0 = std::min(y0, desc.Height);
        y1 = std::min(y1, desc.Height);
        if (x1 <= x0 || y1 <= y0) {
            return fallback_double_wide_crop(desc, eye, bounds);
        }
        UINT width = x1 - x0;
        UINT height = y1 - y0;
        if (width < 16 || height < 16) {
            return fallback_double_wide_crop(desc, eye, bounds);
        }
        return CropResult { TextureCrop { x0, y0, width, height }, false };
    }

    CropResult fallback_double_wide_crop(
        const D3D11_TEXTURE2D_DESC& desc,
        vr::EVREye eye,
        const vr::VRTextureBounds_t* bounds
    ) {
        if (!bounds || desc.Width < 2) {
            return CropResult {};
        }
        UINT half_width = desc.Width / 2;
        UINT x = eye == vr::Eye_Right ? half_width : 0;
        return CropResult { TextureCrop { x, 0, half_width, desc.Height }, true };
    }

    void copy_to_bgra(
        uint8_t* dst,
        const uint8_t* src,
        UINT src_pitch,
        const D3D11_TEXTURE2D_DESC& desc,
        const TextureCrop& crop
    ) {
        const uint32_t dst_pitch = crop.width * ALVR_BYTES_PER_PIXEL;
        if (is_bgra_format(desc.Format)) {
            for (UINT y = 0; y < crop.height; ++y) {
                std::memcpy(dst + static_cast<size_t>(y) * dst_pitch,
                            src + static_cast<size_t>(crop.y + y) * src_pitch
                                + static_cast<size_t>(crop.x) * ALVR_BYTES_PER_PIXEL,
                            dst_pitch);
            }
        } else {
            for (UINT y = 0; y < crop.height; ++y) {
                const uint8_t* row = src + static_cast<size_t>(crop.y + y) * src_pitch
                    + static_cast<size_t>(crop.x) * ALVR_BYTES_PER_PIXEL;
                uint8_t* out = dst + static_cast<size_t>(y) * dst_pitch;
                for (UINT x = 0; x < crop.width; ++x) {
                    const uint8_t* pixel = row + static_cast<size_t>(x) * ALVR_BYTES_PER_PIXEL;
                    uint8_t* converted = out + static_cast<size_t>(x) * ALVR_BYTES_PER_PIXEL;
                    converted[0] = pixel[2];
                    converted[1] = pixel[1];
                    converted[2] = pixel[0];
                    converted[3] = pixel[3];
                }
            }
        }
    }

    void log_frame_stats(vr::EVREye eye, const EyeFrame& frame) {
        size_t color_nonzero = 0;
        size_t blue_nonzero = 0;
        size_t green_nonzero = 0;
        size_t red_nonzero = 0;
        size_t alpha_nonzero = 0;
        uint64_t blue_sum = 0;
        uint64_t green_sum = 0;
        uint64_t red_sum = 0;
        uint8_t max_blue = 0;
        uint8_t max_green = 0;
        uint8_t max_red = 0;
        uint8_t max_alpha = 0;
        for (size_t index = 0; index < frame.bgra.size(); index += ALVR_BYTES_PER_PIXEL) {
            uint8_t blue = frame.bgra[index + 0];
            uint8_t green = frame.bgra[index + 1];
            uint8_t red = frame.bgra[index + 2];
            uint8_t alpha = frame.bgra[index + 3];

            color_nonzero += blue || green || red;
            blue_nonzero += blue != 0;
            green_nonzero += green != 0;
            red_nonzero += red != 0;
            alpha_nonzero += alpha != 0;
            blue_sum += blue;
            green_sum += green;
            red_sum += red;
            max_blue = std::max(max_blue, blue);
            max_green = std::max(max_green, green);
            max_red = std::max(max_red, red);
            max_alpha = std::max(max_alpha, alpha);
        }

        const uint64_t pixel_count = frame.bgra.size() / ALVR_BYTES_PER_PIXEL;
        const uint64_t mean_blue_milli = pixel_count ? blue_sum * 1000 / pixel_count : 0;
        const uint64_t mean_green_milli = pixel_count ? green_sum * 1000 / pixel_count : 0;
        const uint64_t mean_red_milli = pixel_count ? red_sum * 1000 / pixel_count : 0;
        size_t eye_index = eye == vr::Eye_Right ? 1 : 0;
        uint64_t stats_seen = m_source_stats_seen[eye_index].fetch_add(1, std::memory_order_relaxed) + 1;
        if (stats_seen <= 5 || stats_seen % 120 == 0) {
            log_line(
                "source frame stats eye=%d seen=%llu size=%ux%u color_nonzero=%llu "
                "nonzero_bgr=%llu,%llu,%llu max_bgr=%u,%u,%u "
                "mean_bgr_milli=%llu,%llu,%llu alpha_nonzero=%llu max_alpha=%u",
                eye,
                static_cast<unsigned long long>(stats_seen),
                frame.width,
                frame.height,
                static_cast<unsigned long long>(color_nonzero),
                static_cast<unsigned long long>(blue_nonzero),
                static_cast<unsigned long long>(green_nonzero),
                static_cast<unsigned long long>(red_nonzero),
                max_blue,
                max_green,
                max_red,
                static_cast<unsigned long long>(mean_blue_milli),
                static_cast<unsigned long long>(mean_green_milli),
                static_cast<unsigned long long>(mean_red_milli),
                static_cast<unsigned long long>(alpha_nonzero),
                max_alpha
            );
        }
    }

    bool ensure_mapped_locked(uint32_t width, uint32_t height, size_t required_size) {
        if (m_shm) {
            if (!bridge_mapping_live(m_shm)) {
                log_line("shared-memory mapping is no longer live; closing cached mapping");
                close();
                return false;
            }
            if (m_mapping_size >= required_size) {
                return true;
            }
            close();
        }

        std::string path = wine_shared_memory_path();
        m_file = CreateFileA(
            path.c_str(),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            nullptr
        );
        if (m_file == INVALID_HANDLE_VALUE) {
            if (!m_logged_missing_shm) {
                log_line("shared-memory file unavailable at %s: %lu", path.c_str(), GetLastError());
                m_logged_missing_shm = true;
            }
            return false;
        }
        m_mapping = CreateFileMappingA(m_file, nullptr, PAGE_READWRITE, 0, 0, nullptr);
        if (!m_mapping) {
            log_line("CreateFileMapping failed: %lu", GetLastError());
            close();
            return false;
        }

        void* ptr = MapViewOfFile(m_mapping, FILE_MAP_ALL_ACCESS, 0, 0, required_size);
        if (!ptr) {
            log_line("MapViewOfFile failed: %lu", GetLastError());
            close();
            return false;
        }

        m_shm = static_cast<AlvrSharedMemory*>(ptr);
        m_mapping_size = required_size;
        m_frame_data = required_size >= alvr_shm_total_size()
            ? static_cast<uint8_t*>(ptr) + alvr_shm_frame_offset(0)
            : nullptr;
        if (!wait_for_bridge_ready(m_shm, 0)) {
            uint64_t now = unix_time_ns();
            uint64_t heartbeat_ns = m_shm->bridge_heartbeat_ns;
            uint64_t heartbeat_age_ns = heartbeat_ns <= now ? now - heartbeat_ns : heartbeat_ns - now;
            log_line(
                "bridge shared memory is not ready magic=0x%08x version=%u initialized=%u shutdown=%u session=%llu heartbeat=%llu now=%llu heartbeat_delta_ns=%llu",
                m_shm->magic,
                m_shm->version,
                m_shm->initialized,
                m_shm->shutdown,
                static_cast<unsigned long long>(m_shm->bridge_session_id),
                static_cast<unsigned long long>(heartbeat_ns),
                static_cast<unsigned long long>(now),
                static_cast<unsigned long long>(heartbeat_age_ns)
            );
            close();
            return false;
        }

        if (m_frame_data) {
            m_width = width;
            m_height = height;
            m_shm->config_width = width;
            m_shm->config_height = height;
            m_shm->config_format = DXGI_FORMAT_B8G8R8A8_UNORM;
            _InterlockedExchange(reinterpret_cast<volatile long*>(&m_shm->config_set), 1);
            log_line("mapped shared memory and published config %ux%u", width, height);
        } else {
            log_line("mapped shared-memory metadata header");
        }
        return true;
    }

    void close() {
        if (m_shm) {
            UnmapViewOfFile(m_shm);
            m_shm = nullptr;
            m_frame_data = nullptr;
            m_mapping_size = 0;
        }
        if (m_mapping) {
            CloseHandle(m_mapping);
            m_mapping = nullptr;
        }
        if (m_file != INVALID_HANDLE_VALUE) {
            CloseHandle(m_file);
            m_file = INVALID_HANDLE_VALUE;
        }
    }

    int acquire_write_buffer_locked() {
        uint64_t sequence = m_shm->write_sequence;
        for (int attempt = 0; attempt < ALVR_NUM_BUFFERS; ++attempt) {
            int idx = alvr_shm_next_buffer(sequence + attempt);
            AlvrFrameHeader* header = &m_shm->frame_headers[idx];
            uint32_t expected = ALVR_FRAME_EMPTY;
            if (_InterlockedCompareExchange(
                    reinterpret_cast<volatile long*>(&header->state),
                    ALVR_FRAME_WRITING,
                    expected
                )
                == expected) {
                return idx;
            }
        }
        return -1;
    }

    void publish_pair_if_ready_locked() {
        if (!m_left.valid || !m_right.valid) {
            return;
        }

        uint32_t source_eye_width = std::min({ m_left.width, m_right.width, static_cast<uint32_t>(ALVR_MAX_WIDTH / 2) });
        uint32_t height = std::min({ m_left.height, m_right.height, static_cast<uint32_t>(ALVR_MAX_HEIGHT) });
        uint32_t inner_crop_px = 0;
        if (source_eye_width > kMinPackedEyeWidth) {
            inner_crop_px = std::min(m_inner_crop_px, source_eye_width - kMinPackedEyeWidth);
        }
        uint32_t eye_width = source_eye_width - inner_crop_px;
        uint32_t output_eye_width = (eye_width / m_scale_divisor) & ~1U;
        uint32_t output_height = (height / m_scale_divisor) & ~1U;
        uint32_t output_width = output_eye_width * 2;
        if (output_width == 0 || output_height == 0) {
            return;
        }

        if (!ensure_mapped_locked(output_width, output_height, alvr_shm_total_size())) {
            return;
        }
        if (m_width != output_width || m_height != output_height) {
            log_line("dropping changed output shape %ux%u configured=%ux%u", output_width, output_height, m_width, m_height);
            return;
        }

        int buffer = acquire_write_buffer_locked();
        if (buffer < 0) {
            _InterlockedIncrement64(reinterpret_cast<volatile LONG64*>(&m_shm->frames_dropped));
            return;
        }

        auto pair_copy_start = std::chrono::steady_clock::now();
        if (m_synthetic_frame) {
            fill_pending_synthetic_frame_locked(m_left, vr::Eye_Left, m_frames_published);
            fill_pending_synthetic_frame_locked(m_right, vr::Eye_Right, m_frames_published);
        }

        uint8_t* dst_base = m_frame_data + static_cast<size_t>(buffer) * ALVR_MAX_FRAME_SIZE;
        uint32_t dst_pitch = output_width * ALVR_BYTES_PER_PIXEL;
        uint32_t eye_bytes = output_eye_width * ALVR_BYTES_PER_PIXEL;
        for (uint32_t y = 0; y < output_height; ++y) {
            uint32_t source_y = y * m_scale_divisor;
            const uint8_t* left = m_left.bgra.data() + static_cast<size_t>(source_y) * m_left.width * ALVR_BYTES_PER_PIXEL;
            const uint8_t* right =
                m_right.bgra.data()
                + (static_cast<size_t>(source_y) * m_right.width + inner_crop_px) * ALVR_BYTES_PER_PIXEL;
            uint8_t* dst = dst_base + static_cast<size_t>(y) * dst_pitch;
            if (m_scale_divisor == 1) {
                std::memcpy(dst, left, eye_bytes);
                std::memcpy(dst + eye_bytes, right, eye_bytes);
            } else {
                copy_scaled_row(dst, left, output_eye_width, m_scale_divisor);
                copy_scaled_row(dst + eye_bytes, right, output_eye_width, m_scale_divisor);
            }
        }
        auto pair_copy_done = std::chrono::steady_clock::now();

        AlvrFrameHeader* header = &m_shm->frame_headers[buffer];
        header->width = output_width;
        header->height = output_height;
        header->stride = dst_pitch;
        PoseSnapshot frame_pose;
        const bool has_frame_pose = read_frame_pose_snapshot(m_shm, &frame_pose);
        header->timestamp_ns = now_ns();
        header->frame_number = m_frames_published;
        header->is_idr = (m_frames_published % 90 == 0) ? 1 : 0;
        if (has_frame_pose) {
            std::memcpy(header->pose, frame_pose.matrix, sizeof(header->pose));
        } else {
            std::memset(header->pose, 0, sizeof(header->pose));
        }
        header->producer_publish_wall_ns = unix_time_ns();
        header->producer_capture_total_us = m_left.capture_total_us + m_right.capture_total_us;
        header->producer_copy_resource_us = m_left.copy_resource_us + m_right.copy_resource_us;
        header->producer_map_wait_us = m_left.map_wait_us + m_right.map_wait_us;
        header->producer_copy_pixels_us = m_left.copy_pixels_us + m_right.copy_pixels_us;
        header->producer_pair_copy_us = elapsed_us(pair_copy_start, pair_copy_done);
        header->producer_left_capture_us = m_left.capture_total_us;
        header->producer_right_capture_us = m_right.capture_total_us;
        header->producer_real_submit_us = m_left.real_submit_us + m_right.real_submit_us;

        _InterlockedExchange(reinterpret_cast<volatile long*>(&header->state), ALVR_FRAME_READY);
        _InterlockedIncrement64(reinterpret_cast<volatile LONG64*>(&m_shm->write_sequence));
        _InterlockedIncrement64(reinterpret_cast<volatile LONG64*>(&m_shm->frames_written));

        ++m_frames_published;
        bool first_pose_contract_sample = has_frame_pose && !m_logged_pose_contract_sample;
        if (has_frame_pose) {
            m_logged_pose_contract_sample = true;
        }
        if (m_frames_published == 1 || m_frames_published % 90 == 0 || first_pose_contract_sample) {
            log_line(
                "published Submit pair frame=%llu output=%ux%u source_eye_width=%u inner_crop=%u scale_divisor=%u left=%ux%u right=%ux%u timing_us real_submit=%u capture=%u copy_resource=%u map_wait=%u copy_pixels=%u pair_copy=%u",
                static_cast<unsigned long long>(m_frames_published - 1),
                output_width,
                output_height,
                source_eye_width,
                inner_crop_px,
                m_scale_divisor,
                m_left.width,
                m_left.height,
                m_right.width,
                m_right.height,
                header->producer_real_submit_us,
                header->producer_capture_total_us,
                header->producer_copy_resource_us,
                header->producer_map_wait_us,
                header->producer_copy_pixels_us,
                header->producer_pair_copy_us
            );
            log_line(
                "Submit pair contract frame=%llu pairing=latest-left-right left_submit_ordinal=%llu right_submit_ordinal=%llu left_submit_timestamp_ns=%llu right_submit_timestamp_ns=%llu submit_clock=wine-steady pose_source=%s pose_generation=%llu pose_sequence=%u pose_timestamp_ns=%llu pose_clock=shared-hmd-pose-timestamp video_timestamp_ns=%llu video_clock=wine-steady idr=%u output=%ux%u view_params_source=%s sync=synchronous-submit-readback unknown_fields=[%s]",
                static_cast<unsigned long long>(m_frames_published - 1),
                static_cast<unsigned long long>(m_left.frame_number),
                static_cast<unsigned long long>(m_right.frame_number),
                static_cast<unsigned long long>(m_left.submit_timestamp_ns),
                static_cast<unsigned long long>(m_right.submit_timestamp_ns),
                has_frame_pose ? "fake-runtime-frame-pose-from-pose-api" : "missing",
                static_cast<unsigned long long>(has_frame_pose ? frame_pose.generation : 0),
                has_frame_pose ? frame_pose.sequence : 0,
                static_cast<unsigned long long>(has_frame_pose ? frame_pose.timestamp_ns : 0),
                static_cast<unsigned long long>(header->timestamp_ns),
                header->is_idr,
                output_width,
                output_height,
                view_params_source(m_shm, has_frame_pose),
                contract_unknown_fields(m_shm, has_frame_pose)
            );
        }

        m_left.valid = false;
        m_right.valid = false;
    }

    std::mutex m_capture_mutex;
    std::mutex m_mutex;
    std::mutex m_diagnostic_mutex;
    std::mutex m_pool_pair_mutex;
    HANDLE m_file = INVALID_HANDLE_VALUE;
    HANDLE m_mapping = nullptr;
    AlvrSharedMemory* m_shm = nullptr;
    uint8_t* m_frame_data = nullptr;
    size_t m_mapping_size = 0;
    bool m_logged_missing_shm = false;
    uint32_t m_width = 0;
    uint32_t m_height = 0;
    uint32_t m_inner_crop_px = 0;
    uint32_t m_scale_divisor = 1;
    bool m_synthetic_frame = false;
    bool m_logged_pose_contract_sample = false;
    uint64_t m_submit_counter = 0;
    std::atomic<uint64_t> m_submit_diagnostic_counter { 0 };
    std::atomic<uint64_t> m_missing_pool_pose_count { 0 };
    std::atomic<uint64_t> m_pool_pair_drops { 0 };
    uint64_t m_pool_pose_session_id = 0;
    uint64_t m_last_pool_source_pose_generation = 0;
    uint64_t m_pool_pose_generation = 0;
    uint64_t m_frames_published = 0;
    std::atomic<uint64_t> m_source_stats_seen[2] = {};
    std::atomic<uint64_t> m_submit_texture_logs_seen[2] = {};
    SubmitSignature m_submit_metadata_signatures[2];
    uint64_t m_submit_metadata_counts[2] = {};
    SubmitSignature m_submit_desc_signatures[2];
    uint64_t m_submit_desc_counts[2] = {};
    RejectionSignature m_rejection_signatures[2][static_cast<size_t>(RejectionKind::Count)] = {};
    uint64_t m_rejection_counts[2][static_cast<size_t>(RejectionKind::Count)] = {};
    EyeFrame m_left;
    EyeFrame m_right;
    PoolEyeSubmission m_pool_pending_left;
    StagingCache m_left_staging;
    StagingCache m_right_staging;
    alvr_probe::DxvkIosurfaceSubmitProof m_iosurface_proof { log_line };

    static void copy_scaled_row(uint8_t* dst, const uint8_t* src, uint32_t output_width, uint32_t divisor) {
        for (uint32_t x = 0; x < output_width; ++x) {
            std::memcpy(
                dst + static_cast<size_t>(x) * ALVR_BYTES_PER_PIXEL,
                src + static_cast<size_t>(x) * divisor * ALVR_BYTES_PER_PIXEL,
                ALVR_BYTES_PER_PIXEL
            );
        }
    }

    static void fill_pending_synthetic_frame_locked(EyeFrame& frame, vr::EVREye eye, uint64_t frame_number) {
        if (!frame.needs_synthetic_fill || frame.synthetic_frame_number == frame_number) {
            return;
        }

        auto fill_start = std::chrono::steady_clock::now();
        fill_synthetic_frame(frame.bgra.data(), frame.width, frame.height, eye, frame_number);
        auto fill_done = std::chrono::steady_clock::now();
        frame.copy_pixels_us += elapsed_us(fill_start, fill_done);
        frame.capture_total_us += elapsed_us(fill_start, fill_done);
        frame.synthetic_frame_number = frame_number;
        frame.needs_synthetic_fill = false;
    }

    static void fill_synthetic_frame(uint8_t* dst, uint32_t width, uint32_t height, vr::EVREye eye, uint64_t frame_number) {
        uint32_t base = eye == vr::Eye_Left ? 0xFF302018U : 0xFF183020U;
        uint32_t stripe = eye == vr::Eye_Left ? 0xFFE0E0E0U : 0xFFC0E0FFU;
        uint32_t marker = 0xFFFF4040U;
        uint32_t drift = width == 0 ? 0 : static_cast<uint32_t>((frame_number * 8) % width);
        uint32_t* pixels = reinterpret_cast<uint32_t*>(dst);

        for (uint32_t y = 0; y < height; ++y) {
            uint32_t color = (y % 120) < 4 ? stripe : base;
            std::fill_n(pixels + static_cast<size_t>(y) * width, width, color);
            if (drift < width) {
                pixels[static_cast<size_t>(y) * width + drift] = marker;
            }
        }
    }
};

SharedMemorySubmitWriter g_writer;

vr::EVRCompositorError __thiscall hooked_cpp_submit(
    void* self,
    vr::EVREye eye,
    const vr::Texture_t* texture,
    const vr::VRTextureBounds_t* bounds,
    vr::EVRSubmitFlags flags
) {
    CppSubmitFn real_submit = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_hook_mutex);
        auto it = g_cpp_submit_by_object.find(self);
        if (it != g_cpp_submit_by_object.end()) {
            real_submit = it->second;
        }
    }
    if (!real_submit) {
        log_line("missing real C++ Submit for object %p", self);
        return vr::VRCompositorError_InvalidTexture;
    }
    uint64_t submit_timestamp_ns = now_ns();
    auto submit_start = std::chrono::steady_clock::now();
    vr::EVRCompositorError result = real_submit(self, eye, texture, bounds, flags);
    auto submit_done = std::chrono::steady_clock::now();
    g_writer.capture_submit(
        eye, texture, bounds, flags, result, submit_timestamp_ns, elapsed_us(submit_start, submit_done)
    );
    return result;
}

vr::EVRCompositorError OPENVR_FNTABLE_CALLTYPE hooked_c_submit(
    vr::EVREye eye,
    vr::Texture_t* texture,
    vr::VRTextureBounds_t* bounds,
    vr::EVRSubmitFlags flags
) {
    CSubmitFn real_submit = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_hook_mutex);
        real_submit = g_real_c_submit;
    }
    if (!real_submit) {
        log_line("missing real C Submit");
        return vr::VRCompositorError_InvalidTexture;
    }
    uint64_t submit_timestamp_ns = now_ns();
    auto submit_start = std::chrono::steady_clock::now();
    vr::EVRCompositorError result = real_submit(eye, texture, bounds, flags);
    auto submit_done = std::chrono::steady_clock::now();
    g_writer.capture_submit(
        eye, texture, bounds, flags, result, submit_timestamp_ns, elapsed_us(submit_start, submit_done)
    );
    return result;
}

const char* compositor_version_name(const char* version) {
    constexpr const char* kFnTablePrefix = "FnTable:";
    if (version && std::strncmp(version, kFnTablePrefix, std::strlen(kFnTablePrefix)) == 0) {
        return version + std::strlen(kFnTablePrefix);
    }
    return version;
}

bool is_compositor_interface(const char* version) {
    const char* compositor_version = compositor_version_name(version);
    return compositor_version
        && (std::strcmp(compositor_version, vr::IVRCompositor_Version) == 0
            || std::strcmp(compositor_version, kLegacyCompositor013) == 0
            || std::strcmp(compositor_version, kLegacyCompositor014) == 0
            || std::strcmp(compositor_version, kLegacyCompositor016) == 0
            || std::strcmp(compositor_version, kLegacyCompositor022) == 0);
}

size_t compositor_slot_count(const char* version) {
    const char* compositor_version = compositor_version_name(version);
    if (compositor_version && std::strcmp(compositor_version, kLegacyCompositor013) == 0) {
        return kLegacyCompositor013Slots;
    }
    if (compositor_version && std::strcmp(compositor_version, kLegacyCompositor014) == 0) {
        return kLegacyCompositor014Slots;
    }
    if (compositor_version && std::strcmp(compositor_version, kLegacyCompositor016) == 0) {
        return kLegacyCompositor016Slots;
    }
    if (compositor_version && std::strcmp(compositor_version, kLegacyCompositor022) == 0) {
        return kLegacyCompositor022Slots;
    }
    return kCompositor027Slots;
}

bool is_c_interface(const char* version) {
    return version
        && std::strncmp(version, "FnTable:", 8) == 0;
}

void* wrap_cpp_compositor(void* compositor, size_t slot_count) {
    if (!compositor) {
        return nullptr;
    }

    std::lock_guard<std::mutex> lock(g_hook_mutex);
    void*** object = reinterpret_cast<void***>(compositor);
    void** original_vtable = *object;
    CppSubmitFn real_submit = reinterpret_cast<CppSubmitFn>(original_vtable[kCompositorSubmitSlot]);
    if (real_submit == &hooked_cpp_submit) {
        return compositor;
    }

    void** wrapped_vtable = new void*[slot_count];
    std::memcpy(wrapped_vtable, original_vtable, sizeof(void*) * slot_count);
    wrapped_vtable[kCompositorSubmitSlot] = reinterpret_cast<void*>(&hooked_cpp_submit);
    *object = wrapped_vtable;
    g_cpp_submit_by_object[compositor] = real_submit;
    log_line(
        "wrapped C++ IVRCompositor object=%p slots=%zu real_submit=%p",
        compositor,
        slot_count,
        reinterpret_cast<void*>(real_submit)
    );
    return compositor;
}

void* wrap_c_compositor_table(void* table, size_t slot_count) {
    if (!table) {
        return nullptr;
    }

    std::lock_guard<std::mutex> lock(g_hook_mutex);
    auto existing = g_c_table_by_original.find(table);
    if (existing != g_c_table_by_original.end()) {
        return existing->second;
    }

    auto* original = static_cast<FlatCompositorTable*>(table);
    auto* wrapped = new FlatCompositorTable{};
    std::memcpy(wrapped->slots, original->slots, sizeof(void*) * slot_count);
    g_real_c_submit = reinterpret_cast<CSubmitFn>(original->slots[kCompositorSubmitSlot]);
    wrapped->slots[kCompositorSubmitSlot] = reinterpret_cast<void*>(&hooked_c_submit);
    g_c_table_by_original[table] = wrapped;
    log_line(
        "wrapped C IVRCompositor table=%p slots=%zu real_submit=%p",
        table,
        slot_count,
        reinterpret_cast<void*>(g_real_c_submit)
    );
    return wrapped;
}

} // namespace

extern "C" __declspec(dllexport) uint32_t VR_InitInternal(
    vr::EVRInitError* error,
    vr::EVRApplicationType application_type
) {
    auto fn = real_proc<VR_InitInternalFn>("VR_InitInternal");
    return fn ? fn(error, application_type) : 0;
}

extern "C" __declspec(dllexport) uint32_t VR_InitInternal2(
    vr::EVRInitError* error,
    vr::EVRApplicationType application_type,
    const char* startup_info
) {
    auto fn = real_proc<VR_InitInternal2Fn>("VR_InitInternal2");
    return fn ? fn(error, application_type, startup_info) : 0;
}

extern "C" __declspec(dllexport) void VR_ShutdownInternal() {
    g_writer.shutdown();
    auto fn = real_proc<VR_ShutdownInternalFn>("VR_ShutdownInternal");
    if (fn) {
        fn();
    }
}

extern "C" __declspec(dllexport) bool VR_IsHmdPresent() {
    auto fn = real_proc<VR_IsHmdPresentFn>("VR_IsHmdPresent");
    return fn ? fn() : false;
}

extern "C" __declspec(dllexport) bool VR_IsRuntimeInstalled() {
    auto fn = real_proc<VR_IsRuntimeInstalledFn>("VR_IsRuntimeInstalled");
    return fn ? fn() : false;
}

extern "C" __declspec(dllexport) bool VR_GetRuntimePath(
    char* path_buffer,
    uint32_t buffer_size,
    uint32_t* required_size
) {
    auto fn = real_proc<VR_GetRuntimePathFn>("VR_GetRuntimePath");
    return fn ? fn(path_buffer, buffer_size, required_size) : false;
}

extern "C" __declspec(dllexport) const char* VR_RuntimePath() {
    auto fn = real_proc<VR_RuntimePathFn>("VR_RuntimePath");
    return fn ? fn() : nullptr;
}

extern "C" __declspec(dllexport) void* VR_GetGenericInterface(
    const char* interface_version,
    vr::EVRInitError* error
) {
    auto fn = real_proc<VR_GetGenericInterfaceFn>("VR_GetGenericInterface");
    if (!fn) {
        return nullptr;
    }

    void* interface_ptr = fn(interface_version, error);
    if (!interface_ptr || !is_compositor_interface(interface_version)) {
        return interface_ptr;
    }

    log_line("VR_GetGenericInterface %s -> %p", interface_version, interface_ptr);
    size_t slot_count = compositor_slot_count(interface_version);
    if (is_c_interface(interface_version)) {
        return wrap_c_compositor_table(interface_ptr, slot_count);
    }
    return wrap_cpp_compositor(interface_ptr, slot_count);
}

extern "C" __declspec(dllexport) bool VR_IsInterfaceVersionValid(const char* interface_version) {
    auto fn = real_proc<VR_IsInterfaceVersionValidFn>("VR_IsInterfaceVersionValid");
    return fn ? fn(interface_version) : false;
}

extern "C" __declspec(dllexport) uint32_t VR_GetInitToken() {
    auto fn = real_proc<VR_GetInitTokenFn>("VR_GetInitToken");
    return fn ? fn() : 0;
}

extern "C" __declspec(dllexport) const char* VR_GetVRInitErrorAsSymbol(vr::EVRInitError error) {
    auto fn = real_proc<VR_GetErrorStringFn>("VR_GetVRInitErrorAsSymbol");
    return fn ? fn(error) : "VRInitError_ALVRSubmitShimForwardFailed";
}

extern "C" __declspec(dllexport) const char* VR_GetVRInitErrorAsEnglishDescription(
    vr::EVRInitError error
) {
    auto fn = real_proc<VR_GetErrorStringFn>("VR_GetVRInitErrorAsEnglishDescription");
    return fn ? fn(error) : "ALVR OpenVR submit shim could not forward to the real OpenVR DLL";
}

extern "C" __declspec(dllexport) const char* VR_GetStringForHmdError(vr::EVRInitError error) {
    auto fn = real_proc<VR_GetErrorStringFn>("VR_GetStringForHmdError");
    return fn ? fn(error) : VR_GetVRInitErrorAsEnglishDescription(error);
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_this_module = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}
