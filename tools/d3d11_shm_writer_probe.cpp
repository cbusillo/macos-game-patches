// CrossOver/D3D11 producer for the ALVR macOS shared-memory bridge.
//
// Build from this repo root on macOS with:
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
//     -static-libstdc++ tools/d3d11_shm_writer_probe.cpp \
//     -I$HOME/Developer/alvr/alvr/server_openvr/cpp \
//     -ld3d11 -ldxgi -lole32 \
//     -o $PROBE_OUT/d3d11_shm_writer_probe.exe
//
// Run order:
//   1. Start the native bridge with ALVR_BRIDGE_INPUT=shared-memory.
//   2. Run this probe inside the CrossOver Steam bottle with cxstart.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <dxgi.h>
#include <wrl/client.h>

#include "shared/alvr_shm_protocol.h"

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>

namespace {

using Microsoft::WRL::ComPtr;

struct Options {
    UINT width = 2560;
    UINT height = 720;
    int frames = 900;
    int fps = 90;
};

struct SharedMemoryMapping {
    HANDLE file = INVALID_HANDLE_VALUE;
    HANDLE mapping = nullptr;
    AlvrSharedMemory* shm = nullptr;
    uint8_t* frame_data = nullptr;
};

constexpr uint64_t kMaxBridgeHeartbeatAgeNs = 5'000'000'000ULL;
constexpr uint64_t kBridgeHeartbeatFutureToleranceNs = 250'000'000ULL;

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

void print_hr(const char* label, HRESULT hr) {
    std::printf("%-32s %s hr=0x%08lx\n", label, SUCCEEDED(hr) ? "OK" : "FAIL", (unsigned long)hr);
}

bool parse_uint(const char* text, UINT* out) {
    char* end = nullptr;
    unsigned long value = std::strtoul(text, &end, 10);
    if (!text[0] || (end && *end) || value == 0 || value > 16384) {
        return false;
    }
    *out = static_cast<UINT>(value);
    return true;
}

bool parse_int(const char* text, int* out) {
    char* end = nullptr;
    long value = std::strtol(text, &end, 10);
    if (!text[0] || (end && *end) || value <= 0 || value > 1000000) {
        return false;
    }
    *out = static_cast<int>(value);
    return true;
}

Options parse_args(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const char* arg = argv[i];
        auto next = [&]() -> const char* {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "missing value for %s\n", arg);
                std::exit(2);
            }
            return argv[++i];
        };

        if (std::strcmp(arg, "--width") == 0) {
            if (!parse_uint(next(), &options.width)) {
                std::fprintf(stderr, "invalid --width\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--height") == 0) {
            if (!parse_uint(next(), &options.height)) {
                std::fprintf(stderr, "invalid --height\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--frames") == 0) {
            if (!parse_int(next(), &options.frames)) {
                std::fprintf(stderr, "invalid --frames\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--fps") == 0) {
            if (!parse_int(next(), &options.fps)) {
                std::fprintf(stderr, "invalid --fps\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--help") == 0) {
            std::printf(
                "usage: d3d11_shm_writer_probe.exe [--width N] [--height N] "
                "[--frames N] [--fps N]\n"
            );
            std::exit(0);
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", arg);
            std::exit(2);
        }
    }
    return options;
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

void close_mapping(SharedMemoryMapping* mapping) {
    if (mapping->shm) {
        UnmapViewOfFile(mapping->shm);
        mapping->shm = nullptr;
        mapping->frame_data = nullptr;
    }
    if (mapping->mapping) {
        CloseHandle(mapping->mapping);
        mapping->mapping = nullptr;
    }
    if (mapping->file != INVALID_HANDLE_VALUE) {
        CloseHandle(mapping->file);
        mapping->file = INVALID_HANDLE_VALUE;
    }
}

bool bridge_mapping_live(AlvrSharedMemory* shm);

bool wait_for_bridge_ready(AlvrSharedMemory* shm, int timeout_ms) {
    double start = now_ms();
    while (now_ms() - start < timeout_ms) {
        if (bridge_mapping_live(shm)) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
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

bool map_shared_memory(SharedMemoryMapping* mapping) {
    std::string path = wine_shared_memory_path();
    double start = now_ms();
    while (now_ms() - start < 10000.0) {
        mapping->file = CreateFileA(
            path.c_str(),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            nullptr
        );
        if (mapping->file != INVALID_HANDLE_VALUE) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    if (mapping->file == INVALID_HANDLE_VALUE) {
        std::fprintf(stderr, "failed to open %s: %lu\n", path.c_str(), GetLastError());
        return false;
    }
    mapping->mapping = CreateFileMappingA(mapping->file, nullptr, PAGE_READWRITE, 0, 0, nullptr);
    if (!mapping->mapping) {
        std::fprintf(stderr, "CreateFileMapping failed: %lu\n", GetLastError());
        close_mapping(mapping);
        return false;
    }

    size_t total_size = alvr_shm_total_size();
    void* ptr = MapViewOfFile(mapping->mapping, FILE_MAP_ALL_ACCESS, 0, 0, total_size);
    if (!ptr) {
        std::fprintf(stderr, "MapViewOfFile failed: %lu\n", GetLastError());
        close_mapping(mapping);
        return false;
    }

    mapping->shm = static_cast<AlvrSharedMemory*>(ptr);
    mapping->frame_data = static_cast<uint8_t*>(ptr) + alvr_shm_frame_offset(0);

    if (!wait_for_bridge_ready(mapping->shm, 10000)) {
        std::fprintf(
            stderr,
            "timed out waiting for bridge header/initialized flag "
            "magic=0x%08x version=%u initialized=%u shutdown=%u\n",
            mapping->shm->magic,
            mapping->shm->version,
            mapping->shm->initialized,
            mapping->shm->shutdown
        );
        close_mapping(mapping);
        return false;
    }

    return true;
}

int acquire_write_buffer(AlvrSharedMemory* shm) {
    uint64_t sequence = shm->write_sequence;
    for (int attempt = 0; attempt < ALVR_NUM_BUFFERS; ++attempt) {
        int idx = alvr_shm_next_buffer(sequence + attempt);
        AlvrFrameHeader* header = &shm->frame_headers[idx];
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

void publish_config(AlvrSharedMemory* shm, UINT width, UINT height) {
    shm->config_width = width;
    shm->config_height = height;
    shm->config_format = DXGI_FORMAT_B8G8R8A8_UNORM;
    _InterlockedExchange(reinterpret_cast<volatile long*>(&shm->config_set), 1);
}

void release_write_buffer(AlvrSharedMemory* shm, int buffer_index) {
    AlvrFrameHeader* header = &shm->frame_headers[buffer_index];
    _InterlockedExchange(reinterpret_cast<volatile long*>(&header->state), ALVR_FRAME_READY);
    _InterlockedIncrement64(reinterpret_cast<volatile LONG64*>(&shm->write_sequence));
    _InterlockedIncrement64(reinterpret_cast<volatile LONG64*>(&shm->frames_written));
}

void paint_debug_overlay(uint8_t* dst_base, UINT width, UINT height, uint32_t stride, int frame) {
    const UINT half_width = width / 2;
    const bool invert = ((frame / 30) % 2) != 0;
    const UINT band_width = std::max<UINT>(32, width / 12);
    const UINT band_x = static_cast<UINT>((frame * 18) % std::max<UINT>(1, width + band_width));

    for (UINT y = 0; y < height; ++y) {
        uint8_t* row = dst_base + static_cast<size_t>(y) * stride;
        for (UINT x = 0; x < width; ++x) {
            const bool left_eye = x < half_width;
            const bool in_band = x + band_width >= band_x && x < band_x;
            const bool stripe = ((y / 48) % 2) != 0;
            uint8_t* pixel = row + static_cast<size_t>(x) * ALVR_BYTES_PER_PIXEL;

            if (in_band) {
                pixel[0] = 255;
                pixel[1] = 255;
                pixel[2] = 255;
            } else if (left_eye ^ invert) {
                pixel[0] = stripe ? 16 : 0;
                pixel[1] = stripe ? 32 : 0;
                pixel[2] = 255;
            } else {
                pixel[0] = 255;
                pixel[1] = stripe ? 32 : 0;
                pixel[2] = stripe ? 16 : 0;
            }
            pixel[3] = 255;
        }
    }

    const UINT marker = std::min<UINT>(height / 2, 320);
    const UINT y0 = height / 2 - marker / 2;
    for (UINT y = y0; y < std::min<UINT>(y0 + marker, height); ++y) {
        uint8_t* row = dst_base + static_cast<size_t>(y) * stride;
        for (UINT x = half_width / 2 - marker / 2; x < std::min<UINT>(half_width / 2 + marker / 2, half_width); ++x) {
            uint8_t* pixel = row + static_cast<size_t>(x) * ALVR_BYTES_PER_PIXEL;
            pixel[0] = 255;
            pixel[1] = 255;
            pixel[2] = 255;
            pixel[3] = 255;
        }
        for (UINT x = half_width + half_width / 2 - marker / 2;
             x < std::min<UINT>(half_width + half_width / 2 + marker / 2, width);
             ++x) {
            uint8_t* pixel = row + static_cast<size_t>(x) * ALVR_BYTES_PER_PIXEL;
            pixel[0] = 255;
            pixel[1] = 255;
            pixel[2] = 255;
            pixel[3] = 255;
        }
    }
}

void print_adapter(ID3D11Device* device) {
    ComPtr<IDXGIDevice> dxgi_device;
    if (FAILED(device->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void**>(dxgi_device.GetAddressOf())))) {
        return;
    }

    ComPtr<IDXGIAdapter> adapter;
    if (FAILED(dxgi_device->GetAdapter(&adapter))) {
        return;
    }

    DXGI_ADAPTER_DESC desc = {};
    if (FAILED(adapter->GetDesc(&desc))) {
        return;
    }

    char name[256] = {};
    WideCharToMultiByte(CP_UTF8, 0, desc.Description, -1, name, sizeof(name), nullptr, nullptr);
    std::printf("Adapter: %s\n", name);
}

} // namespace

int main(int argc, char** argv) {
    Options options = parse_args(argc, argv);
    if (options.width > ALVR_MAX_WIDTH || options.height > ALVR_MAX_HEIGHT) {
        std::fprintf(
            stderr,
            "size %ux%u exceeds shared-memory ABI limit %dx%d\n",
            options.width,
            options.height,
            ALVR_MAX_WIDTH,
            ALVR_MAX_HEIGHT
        );
        return 2;
    }

    std::printf(
        "D3D11 shared-memory writer probe size=%ux%u frames=%d fps=%d\n",
        options.width,
        options.height,
        options.frames,
        options.fps
    );

    SharedMemoryMapping shm;
    if (!map_shared_memory(&shm)) {
        return 1;
    }
    std::printf("mapped shared memory\n");

    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    D3D_FEATURE_LEVEL feature_level = D3D_FEATURE_LEVEL_11_0;
    D3D_FEATURE_LEVEL feature_levels[] = {
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    };

    HRESULT hr = D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        0,
        feature_levels,
        ARRAYSIZE(feature_levels),
        D3D11_SDK_VERSION,
        &device,
        &feature_level,
        &context
    );
    print_hr("D3D11CreateDevice", hr);
    if (FAILED(hr)) {
        close_mapping(&shm);
        return 1;
    }
    print_adapter(device.Get());

    D3D11_TEXTURE2D_DESC render_desc = {};
    render_desc.Width = options.width;
    render_desc.Height = options.height;
    render_desc.MipLevels = 1;
    render_desc.ArraySize = 1;
    render_desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    render_desc.SampleDesc.Count = 1;
    render_desc.Usage = D3D11_USAGE_DEFAULT;
    render_desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;

    ComPtr<ID3D11Texture2D> render_texture;
    hr = device->CreateTexture2D(&render_desc, nullptr, &render_texture);
    print_hr("Create render texture", hr);
    if (FAILED(hr)) {
        close_mapping(&shm);
        return 1;
    }

    ComPtr<ID3D11RenderTargetView> rtv;
    hr = device->CreateRenderTargetView(render_texture.Get(), nullptr, &rtv);
    print_hr("Create RTV", hr);
    if (FAILED(hr)) {
        close_mapping(&shm);
        return 1;
    }

    D3D11_TEXTURE2D_DESC staging_desc = render_desc;
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.BindFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;

    ComPtr<ID3D11Texture2D> staging_texture;
    hr = device->CreateTexture2D(&staging_desc, nullptr, &staging_texture);
    print_hr("Create staging texture", hr);
    if (FAILED(hr)) {
        close_mapping(&shm);
        return 1;
    }

    publish_config(shm.shm, options.width, options.height);
    std::printf("published shared-memory config\n");

    const uint32_t dst_pitch = options.width * ALVR_BYTES_PER_PIXEL;
    const auto frame_interval = std::chrono::microseconds(1000000 / options.fps);
    uint64_t dropped = 0;
    bool bridge_lost = false;
    double start_ms = now_ms();

    for (int frame = 0; frame < options.frames; ++frame) {
        if (!bridge_mapping_live(shm.shm)) {
            std::fprintf(stderr, "shared-memory bridge stopped or mapping became stale\n");
            bridge_lost = true;
            break;
        }

        float phase = static_cast<float>(frame % 180) / 180.0f;
        float color[4] = {
            0.05f + 0.35f * phase,
            0.10f + 0.30f * (1.0f - phase),
            0.85f,
            1.0f,
        };
        if ((frame / 45) % 2 == 0) {
            color[0] = 0.85f;
            color[2] = 0.10f + 0.35f * phase;
        }

        auto target_time = std::chrono::steady_clock::now() + frame_interval;
        context->ClearRenderTargetView(rtv.Get(), color);
        context->CopyResource(staging_texture.Get(), render_texture.Get());

        int buffer = acquire_write_buffer(shm.shm);
        if (buffer < 0) {
            ++dropped;
            _InterlockedIncrement64(reinterpret_cast<volatile LONG64*>(&shm.shm->frames_dropped));
            std::this_thread::sleep_until(target_time);
            continue;
        }

        D3D11_MAPPED_SUBRESOURCE mapped = {};
        hr = context->Map(staging_texture.Get(), 0, D3D11_MAP_READ, 0, &mapped);
        if (FAILED(hr)) {
            std::fprintf(stderr, "Map staging failed: 0x%08lx\n", (unsigned long)hr);
            _InterlockedExchange(
                reinterpret_cast<volatile long*>(&shm.shm->frame_headers[buffer].state),
                ALVR_FRAME_EMPTY
            );
            close_mapping(&shm);
            return 1;
        }

        uint8_t* dst_base = shm.frame_data + static_cast<size_t>(buffer) * ALVR_MAX_FRAME_SIZE;
        const uint8_t* src = static_cast<const uint8_t*>(mapped.pData);
        for (UINT y = 0; y < options.height; ++y) {
            std::memcpy(
                dst_base + static_cast<size_t>(y) * dst_pitch,
                src + static_cast<size_t>(y) * mapped.RowPitch,
                dst_pitch
            );
        }
        context->Unmap(staging_texture.Get(), 0);

        paint_debug_overlay(dst_base, options.width, options.height, dst_pitch, frame);

        AlvrFrameHeader* header = &shm.shm->frame_headers[buffer];
        header->width = options.width;
        header->height = options.height;
        header->stride = dst_pitch;
        header->timestamp_ns = now_ns();
        header->frame_number = static_cast<uint64_t>(frame);
        header->is_idr = (frame % 90 == 0) ? 1 : 0;
        std::memset(header->pose, 0, sizeof(header->pose));

        release_write_buffer(shm.shm, buffer);
        if (frame % 90 == 0) {
            std::printf("published frame=%d dropped=%llu\n", frame, (unsigned long long)dropped);
        }

        std::this_thread::sleep_until(target_time);
    }

    double elapsed_ms = now_ms() - start_ms;
    std::printf(
        "done frames=%d dropped=%llu elapsed_ms=%.1f frames_written=%llu frames_encoded=%llu shm_dropped=%llu\n",
        options.frames,
        (unsigned long long)dropped,
        elapsed_ms,
        (unsigned long long)shm.shm->frames_written,
        (unsigned long long)shm.shm->frames_encoded,
        (unsigned long long)shm.shm->frames_dropped
    );

    close_mapping(&shm);
    return bridge_lost ? 1 : 0;
}
