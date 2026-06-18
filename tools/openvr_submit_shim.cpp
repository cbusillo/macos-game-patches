// OpenVR IVRCompositor::Submit capture shim for the macOS/CrossOver ALVR bridge.
//
// Build from this repo root on macOS with:
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
//     -static-libstdc++ -shared tools/openvr_submit_shim.cpp \
//     -I$HOME/Developer/alvr/openvr/headers \
//     -I$HOME/Developer/alvr/alvr/server_openvr/cpp \
//     -ld3d11 -ldxgi -lole32 \
//     -Wl,--out-implib,$PROBE_OUT/openvr_api_shim.lib \
//     -o $PROBE_OUT/openvr_api.dll
//
// Stage beside an OpenVR app as openvr_api.dll. Put the real OpenVR DLL beside
// it as openvr_api.real.dll, or set ALVR_OPENVR_REAL_DLL to the real DLL path.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <dxgi.h>
#include <openvr.h>
#include <wrl/client.h>

#include "shared/alvr_shm_protocol.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

using Microsoft::WRL::ComPtr;

#ifndef OPENVR_FNTABLE_CALLTYPE
#define OPENVR_FNTABLE_CALLTYPE __stdcall
#endif

constexpr size_t kCompositorSubmitSlot = 5;
// IVRCompositor_027 exposes 51 methods in this OpenVR header; the legacy
// IVRCompositor_022 table used by Unity 2019 has 43 slots. Copy only the
// requested interface surface so legacy callers do not read past their table.
constexpr size_t kCompositor027Slots = 51;
constexpr size_t kLegacyCompositor022Slots = 43;
constexpr size_t kMaxCompositorSlots = kCompositor027Slots;
constexpr const char* kLegacyCompositor022 = "IVRCompositor_022";
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
static_assert(kCompositorSubmitSlot < kCompositor027Slots, "Submit slot must fit current IVRCompositor vtable");

struct EyeFrame {
    bool valid = false;
    uint32_t width = 0;
    uint32_t height = 0;
    uint64_t frame_number = 0;
    uint32_t real_submit_us = 0;
    uint32_t capture_total_us = 0;
    uint32_t copy_resource_us = 0;
    uint32_t map_wait_us = 0;
    uint32_t copy_pixels_us = 0;
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
    return format == DXGI_FORMAT_B8G8R8A8_UNORM || format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
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

class SharedMemorySubmitWriter {
public:
    ~SharedMemorySubmitWriter() { close(); }

    void capture_submit(
        vr::EVREye eye,
        const vr::Texture_t* texture,
        const vr::VRTextureBounds_t* bounds,
        uint32_t real_submit_us
    ) {
        if (!texture || texture->eType != vr::TextureType_DirectX || !texture->handle) {
            return;
        }

        ComPtr<ID3D11Texture2D> submitted;
        HRESULT hr = static_cast<IUnknown*>(texture->handle)->QueryInterface(
            __uuidof(ID3D11Texture2D),
            reinterpret_cast<void**>(submitted.GetAddressOf())
        );
        if (FAILED(hr) || !submitted) {
            log_line("Submit texture is not ID3D11Texture2D hr=0x%08lx", static_cast<unsigned long>(hr));
            return;
        }

        EyeFrame frame;
        if (!read_eye_texture(submitted.Get(), eye, bounds, &frame)) {
            return;
        }

        std::lock_guard<std::mutex> lock(m_mutex);
        frame.frame_number = ++m_submit_counter;
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

private:
    bool read_eye_texture(
        ID3D11Texture2D* texture,
        vr::EVREye eye,
        const vr::VRTextureBounds_t* bounds,
        EyeFrame* frame
    ) {
        auto capture_start = std::chrono::steady_clock::now();
        D3D11_TEXTURE2D_DESC desc = {};
        texture->GetDesc(&desc);

        if (desc.SampleDesc.Count != 1 || desc.ArraySize != 1 || desc.MipLevels < 1) {
            log_line(
                "unsupported Submit texture eye=%d shape=%ux%u samples=%u array=%u mips=%u",
                eye,
                desc.Width,
                desc.Height,
                desc.SampleDesc.Count,
                desc.ArraySize,
                desc.MipLevels
            );
            return false;
        }
        if (!is_bgra_format(desc.Format) && !is_rgba_format(desc.Format)) {
            log_line("unsupported Submit texture eye=%d format=%u size=%ux%u", eye, desc.Format, desc.Width, desc.Height);
            return false;
        }

        CropResult crop_result = texture_crop(desc, eye, bounds);
        TextureCrop crop = crop_result.crop;
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
                log_line("CreateTexture2D staging failed eye=%d hr=0x%08lx", eye, static_cast<unsigned long>(hr));
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
            log_line("Map staging failed eye=%d hr=0x%08lx", eye, static_cast<unsigned long>(hr));
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
        size_t nonzero = 0;
        uint8_t max_alpha = 0;
        uint8_t max_color = 0;
        for (size_t index = 0; index < frame.bgra.size(); index += ALVR_BYTES_PER_PIXEL) {
            uint8_t blue = frame.bgra[index + 0];
            uint8_t green = frame.bgra[index + 1];
            uint8_t red = frame.bgra[index + 2];
            uint8_t alpha = frame.bgra[index + 3];
            if (blue || green || red || alpha) {
                ++nonzero;
            }
            max_color = std::max(max_color, std::max(red, std::max(green, blue)));
            max_alpha = std::max(max_alpha, alpha);
        }

        size_t eye_index = eye == vr::Eye_Right ? 1 : 0;
        ++m_source_stats_seen[eye_index];
        if (m_source_stats_seen[eye_index] <= 5 || m_source_stats_seen[eye_index] % 120 == 0) {
            log_line(
                "source frame stats eye=%d seen=%llu size=%ux%u nonzero_pixels=%llu max_color=%u max_alpha=%u",
                eye,
                static_cast<unsigned long long>(m_source_stats_seen[eye_index]),
                frame.width,
                frame.height,
                static_cast<unsigned long long>(nonzero),
                max_color,
                max_alpha
            );
        }
    }

    bool ensure_mapped_locked(uint32_t width, uint32_t height) {
        if (m_shm) {
            if (!bridge_mapping_live(m_shm)) {
                log_line("shared-memory mapping is no longer live; closing cached mapping");
                close();
                return false;
            }
            return true;
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

        size_t total_size = alvr_shm_total_size();
        void* ptr = MapViewOfFile(m_mapping, FILE_MAP_ALL_ACCESS, 0, 0, total_size);
        if (!ptr) {
            log_line("MapViewOfFile failed: %lu", GetLastError());
            close();
            return false;
        }

        m_shm = static_cast<AlvrSharedMemory*>(ptr);
        m_frame_data = static_cast<uint8_t*>(ptr) + alvr_shm_frame_offset(0);
        if (!wait_for_bridge_ready(m_shm, 0)) {
            log_line(
                "bridge shared memory is not ready magic=0x%08x version=%u initialized=%u shutdown=%u",
                m_shm->magic,
                m_shm->version,
                m_shm->initialized,
                m_shm->shutdown
            );
            close();
            return false;
        }

        m_width = width;
        m_height = height;
        m_shm->config_width = width;
        m_shm->config_height = height;
        m_shm->config_format = DXGI_FORMAT_B8G8R8A8_UNORM;
        _InterlockedExchange(reinterpret_cast<volatile long*>(&m_shm->config_set), 1);
        log_line("mapped shared memory and published config %ux%u", width, height);
        return true;
    }

    void close() {
        if (m_shm) {
            UnmapViewOfFile(m_shm);
            m_shm = nullptr;
            m_frame_data = nullptr;
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

        uint32_t eye_width = std::min({ m_left.width, m_right.width, static_cast<uint32_t>(ALVR_MAX_WIDTH / 2) });
        uint32_t height = std::min({ m_left.height, m_right.height, static_cast<uint32_t>(ALVR_MAX_HEIGHT) });
        uint32_t output_width = eye_width * 2;
        if (output_width == 0 || height == 0) {
            return;
        }

        if (!ensure_mapped_locked(output_width, height)) {
            return;
        }
        if (m_width != output_width || m_height != height) {
            log_line("dropping changed output shape %ux%u configured=%ux%u", output_width, height, m_width, m_height);
            return;
        }

        int buffer = acquire_write_buffer_locked();
        if (buffer < 0) {
            _InterlockedIncrement64(reinterpret_cast<volatile LONG64*>(&m_shm->frames_dropped));
            return;
        }

        auto pair_copy_start = std::chrono::steady_clock::now();
        uint8_t* dst_base = m_frame_data + static_cast<size_t>(buffer) * ALVR_MAX_FRAME_SIZE;
        uint32_t dst_pitch = output_width * ALVR_BYTES_PER_PIXEL;
        uint32_t eye_bytes = eye_width * ALVR_BYTES_PER_PIXEL;
        for (uint32_t y = 0; y < height; ++y) {
            const uint8_t* left = m_left.bgra.data() + static_cast<size_t>(y) * m_left.width * ALVR_BYTES_PER_PIXEL;
            const uint8_t* right = m_right.bgra.data() + static_cast<size_t>(y) * m_right.width * ALVR_BYTES_PER_PIXEL;
            uint8_t* dst = dst_base + static_cast<size_t>(y) * dst_pitch;
            std::memcpy(dst, left, eye_bytes);
            std::memcpy(dst + eye_bytes, right, eye_bytes);
        }
        auto pair_copy_done = std::chrono::steady_clock::now();

        AlvrFrameHeader* header = &m_shm->frame_headers[buffer];
        header->width = output_width;
        header->height = height;
        header->stride = dst_pitch;
        header->timestamp_ns = now_ns();
        header->frame_number = m_frames_published;
        header->is_idr = (m_frames_published % 90 == 0) ? 1 : 0;
        std::memset(header->pose, 0, sizeof(header->pose));
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
        if (m_frames_published == 1 || m_frames_published % 90 == 0) {
            log_line(
                "published Submit pair frame=%llu output=%ux%u left=%ux%u right=%ux%u timing_us real_submit=%u capture=%u copy_resource=%u map_wait=%u copy_pixels=%u pair_copy=%u",
                static_cast<unsigned long long>(m_frames_published - 1),
                output_width,
                height,
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
        }

        m_left.valid = false;
        m_right.valid = false;
    }

    std::mutex m_mutex;
    HANDLE m_file = INVALID_HANDLE_VALUE;
    HANDLE m_mapping = nullptr;
    AlvrSharedMemory* m_shm = nullptr;
    uint8_t* m_frame_data = nullptr;
    bool m_logged_missing_shm = false;
    uint32_t m_width = 0;
    uint32_t m_height = 0;
    uint64_t m_submit_counter = 0;
    uint64_t m_frames_published = 0;
    uint64_t m_source_stats_seen[2] = {};
    EyeFrame m_left;
    EyeFrame m_right;
    StagingCache m_left_staging;
    StagingCache m_right_staging;
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
    auto submit_start = std::chrono::steady_clock::now();
    vr::EVRCompositorError result = real_submit(self, eye, texture, bounds, flags);
    auto submit_done = std::chrono::steady_clock::now();
    g_writer.capture_submit(eye, texture, bounds, elapsed_us(submit_start, submit_done));
    return result;
}

vr::EVRCompositorError OPENVR_FNTABLE_CALLTYPE hooked_c_submit(
    vr::EVREye eye,
    vr::Texture_t* texture,
    vr::VRTextureBounds_t* bounds,
    vr::EVRSubmitFlags flags
) {
    if (!g_real_c_submit) {
        log_line("missing real C Submit");
        return vr::VRCompositorError_InvalidTexture;
    }
    auto submit_start = std::chrono::steady_clock::now();
    vr::EVRCompositorError result = g_real_c_submit(eye, texture, bounds, flags);
    auto submit_done = std::chrono::steady_clock::now();
    g_writer.capture_submit(eye, texture, bounds, elapsed_us(submit_start, submit_done));
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
            || std::strcmp(compositor_version, kLegacyCompositor022) == 0);
}

size_t compositor_slot_count(const char* version) {
    const char* compositor_version = compositor_version_name(version);
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
