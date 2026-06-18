// Local CrossOver/D3DMetal probe for D3D11 render-target readback cost.
// Build on macOS with:
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
//     -static-libstdc++ tools/d3d11_readback_probe.cpp \
//     -ld3d11 -ldxgi -lole32 \
//     -o $PROBE_OUT/d3d11_readback_probe.exe

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <dxgi.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <numeric>
#include <string>
#include <vector>

namespace {

struct Options {
    UINT width = 4288;
    UINT height = 2048;
    int frames = 240;
    int warmup = 30;
    bool copy_to_heap = true;
    std::string out_path;
};

struct Timings {
    double clear_ms = 0.0;
    double copy_ms = 0.0;
    double map_ms = 0.0;
    double read_ms = 0.0;
    double total_ms = 0.0;
};

double now_ms() {
    using clock = std::chrono::high_resolution_clock;
    static const auto start = clock::now();
    return std::chrono::duration<double, std::milli>(clock::now() - start).count();
}

const char* hr_name(HRESULT hr) {
    switch (hr) {
    case S_OK:
        return "S_OK";
    case E_NOTIMPL:
        return "E_NOTIMPL";
    case E_NOINTERFACE:
        return "E_NOINTERFACE";
    case E_INVALIDARG:
        return "E_INVALIDARG";
    default:
        return "UNKNOWN";
    }
}

void print_hr(const char* label, HRESULT hr) {
    std::printf("%-36s %s hr=0x%08lx\n", label, SUCCEEDED(hr) ? "OK" : "FAIL", (unsigned long)hr);
    if (FAILED(hr)) {
        std::printf("  name=%s\n", hr_name(hr));
    }
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
    if (!text[0] || (end && *end) || value < 0 || value > 100000) {
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
            if (!parse_int(next(), &options.frames) || options.frames <= 0) {
                std::fprintf(stderr, "invalid --frames\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--warmup") == 0) {
            if (!parse_int(next(), &options.warmup)) {
                std::fprintf(stderr, "invalid --warmup\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--no-heap-copy") == 0) {
            options.copy_to_heap = false;
        } else if (std::strcmp(arg, "--help") == 0) {
            std::printf(
                "usage: d3d11_readback_probe.exe [--width N] [--height N] [--frames N] "
                "[--warmup N] [--no-heap-copy] [--out PATH]\n"
            );
            std::exit(0);
        } else if (std::strcmp(arg, "--out") == 0) {
            options.out_path = next();
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", arg);
            std::exit(2);
        }
    }
    return options;
}

template <typename T>
void release_if(T*& ptr) {
    if (ptr) {
        ptr->Release();
        ptr = nullptr;
    }
}

double percentile(std::vector<double> values, double p) {
    if (values.empty()) {
        return 0.0;
    }
    std::sort(values.begin(), values.end());
    double index = (values.size() - 1) * p;
    size_t low = static_cast<size_t>(index);
    size_t high = std::min(low + 1, values.size() - 1);
    double frac = index - low;
    return values[low] * (1.0 - frac) + values[high] * frac;
}

void print_stats(const char* label, const std::vector<double>& values) {
    double sum = std::accumulate(values.begin(), values.end(), 0.0);
    double mean = values.empty() ? 0.0 : sum / values.size();
    std::printf(
        "%8s mean=%7.3fms p50=%7.3fms p90=%7.3fms p99=%7.3fms max=%7.3fms\n",
        label,
        mean,
        percentile(values, 0.50),
        percentile(values, 0.90),
        percentile(values, 0.99),
        values.empty() ? 0.0 : *std::max_element(values.begin(), values.end())
    );
}

void print_adapter(IDXGIAdapter* adapter) {
    DXGI_ADAPTER_DESC desc = {};
    if (FAILED(adapter->GetDesc(&desc))) {
        return;
    }

    char name[256] = {};
    WideCharToMultiByte(CP_UTF8, 0, desc.Description, -1, name, sizeof(name), nullptr, nullptr);
    std::printf("Adapter: %s\n", name);
    std::printf(
        "VendorId=0x%04x DeviceId=0x%04x DedicatedVideoMemory=%llu SharedSystemMemory=%llu\n",
        desc.VendorId,
        desc.DeviceId,
        static_cast<unsigned long long>(desc.DedicatedVideoMemory),
        static_cast<unsigned long long>(desc.SharedSystemMemory)
    );
}

HRESULT create_d3d11_device(
    ID3D11Device** device,
    D3D_FEATURE_LEVEL* feature_level,
    ID3D11DeviceContext** context
) {
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
        device,
        feature_level,
        context
    );

    if (hr == E_INVALIDARG) {
        D3D_FEATURE_LEVEL fallback_levels[] = {
            D3D_FEATURE_LEVEL_11_0,
            D3D_FEATURE_LEVEL_10_1,
            D3D_FEATURE_LEVEL_10_0,
        };
        hr = D3D11CreateDevice(
            nullptr,
            D3D_DRIVER_TYPE_HARDWARE,
            nullptr,
            0,
            fallback_levels,
            ARRAYSIZE(fallback_levels),
            D3D11_SDK_VERSION,
            device,
            feature_level,
            context
        );
    }

    return hr;
}

void copy_rows(
    uint8_t* dst,
    const uint8_t* src,
    UINT width,
    UINT height,
    UINT src_pitch
) {
    const size_t dst_pitch = static_cast<size_t>(width) * 4;
    for (UINT y = 0; y < height; ++y) {
        std::memcpy(dst + static_cast<size_t>(y) * dst_pitch,
                    src + static_cast<size_t>(y) * src_pitch,
                    dst_pitch);
    }
}

} // namespace

int main(int argc, char** argv) {
    Options options = parse_args(argc, argv);
    const size_t bytes_per_frame = static_cast<size_t>(options.width) * options.height * 4;

    std::printf("D3D11 readback probe\n");
    std::printf(
        "size=%ux%u bytes_per_frame=%zu frames=%d warmup=%d heap_copy=%s\n",
        options.width,
        options.height,
        bytes_per_frame,
        options.frames,
        options.warmup,
        options.copy_to_heap ? "yes" : "no"
    );

    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    D3D_FEATURE_LEVEL feature_level = D3D_FEATURE_LEVEL_11_0;

    HRESULT hr = create_d3d11_device(&device, &feature_level, &context);
    print_hr("D3D11CreateDevice", hr);
    if (FAILED(hr)) {
        return 1;
    }
    std::printf("FeatureLevel=0x%x\n", feature_level);

    IDXGIDevice* dxgi_device = nullptr;
    hr = device->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void**>(&dxgi_device));
    if (SUCCEEDED(hr)) {
        IDXGIAdapter* adapter = nullptr;
        if (SUCCEEDED(dxgi_device->GetAdapter(&adapter))) {
            print_adapter(adapter);
            release_if(adapter);
        }
    }
    release_if(dxgi_device);

    D3D11_TEXTURE2D_DESC render_desc = {};
    render_desc.Width = options.width;
    render_desc.Height = options.height;
    render_desc.MipLevels = 1;
    render_desc.ArraySize = 1;
    render_desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    render_desc.SampleDesc.Count = 1;
    render_desc.Usage = D3D11_USAGE_DEFAULT;
    render_desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;

    ID3D11Texture2D* render_texture = nullptr;
    hr = device->CreateTexture2D(&render_desc, nullptr, &render_texture);
    print_hr("CreateTexture2D render target", hr);
    if (FAILED(hr)) {
        release_if(context);
        release_if(device);
        return 1;
    }

    ID3D11RenderTargetView* rtv = nullptr;
    hr = device->CreateRenderTargetView(render_texture, nullptr, &rtv);
    print_hr("CreateRenderTargetView", hr);
    if (FAILED(hr)) {
        release_if(render_texture);
        release_if(context);
        release_if(device);
        return 1;
    }

    D3D11_TEXTURE2D_DESC staging_desc = render_desc;
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.BindFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    staging_desc.MiscFlags = 0;

    ID3D11Texture2D* staging_texture = nullptr;
    hr = device->CreateTexture2D(&staging_desc, nullptr, &staging_texture);
    print_hr("CreateTexture2D staging", hr);
    if (FAILED(hr)) {
        release_if(rtv);
        release_if(render_texture);
        release_if(context);
        release_if(device);
        return 1;
    }

    std::vector<uint8_t> heap_copy;
    std::vector<uint8_t> direct_copy_scratch;
    if (options.copy_to_heap) {
        heap_copy.resize(bytes_per_frame);
    } else {
        direct_copy_scratch.resize(bytes_per_frame);
    }

    std::vector<Timings> samples;
    samples.reserve(options.frames);
    uint64_t checksum = 0;

    for (int frame = 0; frame < options.frames + options.warmup; ++frame) {
        float color[4] = {
            static_cast<float>((frame * 13) % 255) / 255.0f,
            static_cast<float>((frame * 7) % 255) / 255.0f,
            static_cast<float>((frame * 3) % 255) / 255.0f,
            1.0f,
        };

        double t0 = now_ms();
        context->ClearRenderTargetView(rtv, color);
        double t1 = now_ms();
        context->CopyResource(staging_texture, render_texture);
        double t2 = now_ms();

        D3D11_MAPPED_SUBRESOURCE mapped = {};
        hr = context->Map(staging_texture, 0, D3D11_MAP_READ, 0, &mapped);
        double t3 = now_ms();
        if (FAILED(hr)) {
            print_hr("Map staging", hr);
            release_if(staging_texture);
            release_if(rtv);
            release_if(render_texture);
            release_if(context);
            release_if(device);
            return 1;
        }

        const uint8_t* src = static_cast<const uint8_t*>(mapped.pData);
        if (options.copy_to_heap) {
            copy_rows(heap_copy.data(), src, options.width, options.height, mapped.RowPitch);
            checksum += heap_copy[(static_cast<size_t>(frame) * 9973) % heap_copy.size()];
        } else {
            copy_rows(direct_copy_scratch.data(), src, options.width, options.height, mapped.RowPitch);
            checksum += direct_copy_scratch
                [(static_cast<size_t>(frame) * 9973) % direct_copy_scratch.size()];
        }
        context->Unmap(staging_texture, 0);
        double t4 = now_ms();

        if (frame >= options.warmup) {
            samples.push_back(Timings { t1 - t0, t2 - t1, t3 - t2, t4 - t3, t4 - t0 });
        }
    }

    std::vector<double> clear_values;
    std::vector<double> copy_values;
    std::vector<double> map_values;
    std::vector<double> read_values;
    std::vector<double> total_values;
    clear_values.reserve(samples.size());
    copy_values.reserve(samples.size());
    map_values.reserve(samples.size());
    read_values.reserve(samples.size());
    total_values.reserve(samples.size());
    for (const auto& timing : samples) {
        clear_values.push_back(timing.clear_ms);
        copy_values.push_back(timing.copy_ms);
        map_values.push_back(timing.map_ms);
        read_values.push_back(timing.read_ms);
        total_values.push_back(timing.total_ms);
    }

    std::printf("checksum=%llu\n", static_cast<unsigned long long>(checksum));
    print_stats("clear", clear_values);
    print_stats("copy", copy_values);
    print_stats("map", map_values);
    print_stats("read", read_values);
    print_stats("total", total_values);

    double mean_total = std::accumulate(total_values.begin(), total_values.end(), 0.0) /
                        std::max<size_t>(1, total_values.size());
    double fps = mean_total > 0.0 ? 1000.0 / mean_total : 0.0;
    double gbps = mean_total > 0.0 ? (static_cast<double>(bytes_per_frame) / 1.0e9) / (mean_total / 1000.0) : 0.0;
    std::printf("effective_fps=%.2f effective_read_gbps=%.2f\n", fps, gbps);

    bool write_output_failed = false;

    if (!options.out_path.empty()) {
        std::ofstream out(options.out_path, std::ios::out | std::ios::trunc);
        if (!out) {
            std::fprintf(stderr, "failed to open --out path: %s\n", options.out_path.c_str());
            write_output_failed = true;
        } else {
            out << "width,height,bytes_per_frame,frames,warmup,heap_copy,checksum,"
                << "clear_mean_ms,copy_mean_ms,map_mean_ms,read_mean_ms,total_mean_ms,"
                << "total_p50_ms,total_p90_ms,total_p99_ms,total_max_ms,effective_fps,effective_read_gbps\n";
            auto mean = [](const std::vector<double>& values) {
                return values.empty() ? 0.0 : std::accumulate(values.begin(), values.end(), 0.0) / values.size();
            };
            out << options.width << ',' << options.height << ',' << bytes_per_frame << ','
                << options.frames << ',' << options.warmup << ','
                << (options.copy_to_heap ? "yes" : "no") << ',' << checksum << ','
                << mean(clear_values) << ',' << mean(copy_values) << ',' << mean(map_values) << ','
                << mean(read_values) << ',' << mean(total_values) << ','
                << percentile(total_values, 0.50) << ',' << percentile(total_values, 0.90) << ','
                << percentile(total_values, 0.99) << ','
                << (total_values.empty() ? 0.0 : *std::max_element(total_values.begin(), total_values.end()))
                << ',' << fps << ',' << gbps << '\n';
            if (!out) {
                std::fprintf(stderr, "failed to write --out path: %s\n", options.out_path.c_str());
                write_output_failed = true;
            }
        }
    }

    release_if(staging_texture);
    release_if(rtv);
    release_if(render_texture);
    release_if(context);
    release_if(device);
    return write_output_failed ? 1 : 0;
}
