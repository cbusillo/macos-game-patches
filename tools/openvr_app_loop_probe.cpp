// Real-ish D3D11 OpenVR app loop for the app-local openvr_api.dll shim.
//
// Build from this repo root on macOS with:
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
//     -static-libstdc++ tools/openvr_app_loop_probe.cpp \
//     -I$HOME/Developer/alvr/openvr/headers \
//     -L$HOME/Developer/alvr/openvr/lib/win64 -lopenvr_api \
//     -ld3d11 -ldxgi -lole32 \
//     -o $PROBE_OUT/openvr_app_loop_probe.exe

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <dxgi.h>
#include <openvr.h>
#include <wrl/client.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

namespace {

using Microsoft::WRL::ComPtr;

enum class EyePatternMode {
    Stereo,
    Mono,
    LeftOnly,
    RightOnly,
};

struct Options {
    UINT width = 0;
    UINT height = 0;
    int32_t adapter_index = -1;
    int frames = 120;
    int fps = 30;
    UINT sample_count = 1;
    bool use_bounds = false;
    bool use_rgba = false;
    bool query_properties = true;
    bool submit_msaa = false;
    bool static_pattern = false;
    EyePatternMode eye_mode = EyePatternMode::Stereo;
    int right_shift_x = 0;
    int right_shift_y = 0;
};

bool parse_uint(const char* text, UINT* out) {
    char* end = nullptr;
    unsigned long value = std::strtoul(text, &end, 10);
    if (!text[0] || (end && *end) || value == 0 || value > 8192) {
        return false;
    }
    *out = static_cast<UINT>(value);
    return true;
}

bool parse_int(const char* text, int* out) {
    char* end = nullptr;
    long value = std::strtol(text, &end, 10);
    if (!text[0] || (end && *end) || value <= 0 || value > 100000) {
        return false;
    }
    *out = static_cast<int>(value);
    return true;
}

bool parse_shift_int(const char* text, int* out) {
    char* end = nullptr;
    long value = std::strtol(text, &end, 10);
    if (!text[0] || (end && *end) || value < -1024 || value > 1024) {
        return false;
    }
    *out = static_cast<int>(value);
    return true;
}

bool is_supported_sample_count(UINT sample_count) {
    return sample_count == 1 || sample_count == 2 || sample_count == 4 || sample_count == 8;
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
        } else if (std::strcmp(arg, "--msaa") == 0) {
            if (!parse_uint(next(), &options.sample_count) || !is_supported_sample_count(options.sample_count)) {
                std::fprintf(stderr, "invalid --msaa; expected 1, 2, 4, or 8\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--bounds") == 0) {
            options.use_bounds = true;
        } else if (std::strcmp(arg, "--rgba") == 0) {
            options.use_rgba = true;
        } else if (std::strcmp(arg, "--submit-msaa") == 0) {
            options.submit_msaa = true;
        } else if (std::strcmp(arg, "--static-pattern") == 0) {
            options.static_pattern = true;
        } else if (std::strcmp(arg, "--mono") == 0) {
            options.eye_mode = EyePatternMode::Mono;
        } else if (std::strcmp(arg, "--left-only") == 0) {
            options.eye_mode = EyePatternMode::LeftOnly;
        } else if (std::strcmp(arg, "--right-only") == 0) {
            options.eye_mode = EyePatternMode::RightOnly;
        } else if (std::strcmp(arg, "--right-shift-x") == 0) {
            if (!parse_shift_int(next(), &options.right_shift_x)) {
                std::fprintf(stderr, "invalid --right-shift-x; expected -1024..1024\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--right-shift-y") == 0) {
            if (!parse_shift_int(next(), &options.right_shift_y)) {
                std::fprintf(stderr, "invalid --right-shift-y; expected -1024..1024\n");
                std::exit(2);
            }
        } else if (std::strcmp(arg, "--no-properties") == 0) {
            options.query_properties = false;
        } else if (std::strcmp(arg, "--help") == 0) {
            std::printf(
                "usage: openvr_app_loop_probe.exe [--width N] [--height N] [--frames N] "
                "[--fps N] [--msaa 1|2|4|8] [--submit-msaa] [--bounds] [--rgba] "
                "[--static-pattern] [--mono] [--left-only] [--right-only] [--right-shift-x N] "
                "[--right-shift-y N] [--no-properties]\n"
            );
            std::exit(0);
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", arg);
            std::exit(2);
        }
    }
    return options;
}

const char* eye_mode_name(EyePatternMode mode) {
    switch (mode) {
    case EyePatternMode::Stereo:
        return "stereo";
    case EyePatternMode::Mono:
        return "mono";
    case EyePatternMode::LeftOnly:
        return "left-only";
    case EyePatternMode::RightOnly:
        return "right-only";
    }
    return "unknown";
}

void print_hr(const char* label, HRESULT hr) {
    std::printf("%-30s %s hr=0x%08lx\n", label, SUCCEEDED(hr) ? "OK" : "FAIL", (unsigned long)hr);
}

void print_matrix44(const char* label, const vr::HmdMatrix44_t& m) {
    std::printf("%s [%.2f %.2f %.2f %.2f]\n", label, m.m[0][0], m.m[1][1], m.m[2][2], m.m[3][3]);
}

void print_matrix34(const char* label, const vr::HmdMatrix34_t& m) {
    std::printf("%s x=%.3f diag=[%.2f %.2f %.2f]\n", label, m.m[0][3], m.m[0][0], m.m[1][1], m.m[2][2]);
}

void query_system(vr::IVRSystem* system, Options* options) {
    uint32_t recommended_width = 0;
    uint32_t recommended_height = 0;
    system->GetRecommendedRenderTargetSize(&recommended_width, &recommended_height);
    if (options->width == 0) {
        options->width = std::max<uint32_t>(1, recommended_width);
    }
    if (options->height == 0) {
        options->height = std::max<uint32_t>(1, recommended_height);
    }
    std::printf("recommended_render_target=%ux%u selected=%ux%u\n", recommended_width, recommended_height, options->width, options->height);

    int32_t adapter_index = -2;
    system->GetDXGIOutputInfo(&adapter_index);
    options->adapter_index = adapter_index;
    std::printf("dxgi_adapter_index=%d runtime=%s\n", adapter_index, system->GetRuntimeVersion());

    if (!options->query_properties) {
        return;
    }

    vr::ETrackedPropertyError property_error = vr::TrackedProp_Success;
    char model[vr::k_unMaxPropertyStringSize] = {};
    uint32_t model_len = system->GetStringTrackedDeviceProperty(
        vr::k_unTrackedDeviceIndex_Hmd,
        vr::Prop_ModelNumber_String,
        model,
        sizeof(model),
        &property_error
    );
    float display_hz = system->GetFloatTrackedDeviceProperty(
        vr::k_unTrackedDeviceIndex_Hmd,
        vr::Prop_DisplayFrequency_Float,
        &property_error
    );
    std::printf("hmd_class=%d connected=%d model_len=%u model=%s display_hz=%.1f\n",
                system->GetTrackedDeviceClass(vr::k_unTrackedDeviceIndex_Hmd),
                system->IsTrackedDeviceConnected(vr::k_unTrackedDeviceIndex_Hmd) ? 1 : 0,
                model_len,
                model,
                display_hz);

    print_matrix44("projection_left", system->GetProjectionMatrix(vr::Eye_Left, 0.1f, 100.0f));
    print_matrix44("projection_right", system->GetProjectionMatrix(vr::Eye_Right, 0.1f, 100.0f));
    print_matrix34("eye_to_head_left", system->GetEyeToHeadTransform(vr::Eye_Left));
    print_matrix34("eye_to_head_right", system->GetEyeToHeadTransform(vr::Eye_Right));

    float left = 0.0f;
    float right = 0.0f;
    float top = 0.0f;
    float bottom = 0.0f;
    system->GetProjectionRaw(vr::Eye_Left, &left, &right, &top, &bottom);
    std::printf("projection_raw_left l=%.2f r=%.2f t=%.2f b=%.2f\n", left, right, top, bottom);

    vr::DistortionCoordinates_t distortion = {};
    bool distortion_ok = system->ComputeDistortion(vr::Eye_Left, 0.5f, 0.5f, &distortion);
    std::printf("distortion_ok=%d red=(%.2f,%.2f)\n", distortion_ok ? 1 : 0, distortion.rfRed[0], distortion.rfRed[1]);
}

bool create_texture(
    ID3D11Device* device,
    UINT width,
    UINT height,
    DXGI_FORMAT format,
    UINT sample_count,
    ID3D11Texture2D** texture,
    ID3D11RenderTargetView** rtv
) {
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = width;
    desc.Height = height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = format;
    desc.SampleDesc.Count = sample_count;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_RENDER_TARGET;
    if (sample_count == 1) {
        desc.BindFlags |= D3D11_BIND_SHADER_RESOURCE;
    }

    if (sample_count > 1) {
        UINT quality = 0;
        HRESULT hr = device->CheckMultisampleQualityLevels(format, sample_count, &quality);
        if (FAILED(hr) || quality == 0) {
            print_hr("CheckMultisample", hr);
            std::fprintf(stderr, "sample count %u is not supported for format %u\n", sample_count, format);
            return false;
        }
        desc.SampleDesc.Quality = quality - 1;
    }

    ComPtr<ID3D11Texture2D> tex;
    HRESULT hr = device->CreateTexture2D(&desc, nullptr, &tex);
    if (FAILED(hr)) {
        print_hr("CreateTexture2D", hr);
        return false;
    }

    ComPtr<ID3D11RenderTargetView> view;
    hr = device->CreateRenderTargetView(tex.Get(), nullptr, &view);
    if (FAILED(hr)) {
        print_hr("Create RTV", hr);
        return false;
    }

    *texture = tex.Detach();
    *rtv = view.Detach();
    return true;
}

bool create_resolve_texture(
    ID3D11Device* device,
    UINT width,
    UINT height,
    DXGI_FORMAT format,
    ID3D11Texture2D** texture
) {
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = width;
    desc.Height = height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = format;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;

    ComPtr<ID3D11Texture2D> tex;
    HRESULT hr = device->CreateTexture2D(&desc, nullptr, &tex);
    if (FAILED(hr)) {
        print_hr("Create resolve texture", hr);
        return false;
    }
    *texture = tex.Detach();
    return true;
}

void clear_eye(ID3D11DeviceContext* context, ID3D11RenderTargetView* rtv, vr::EVREye eye, int frame) {
    const float phase = static_cast<float>((frame % 120) + 1) / 120.0f;
    float color[4] = {
        eye == vr::Eye_Left ? 0.85f : 0.05f + 0.40f * phase,
        0.05f + 0.30f * (1.0f - phase),
        eye == vr::Eye_Right ? 0.85f : 0.05f + 0.40f * phase,
        1.0f,
    };
    context->ClearRenderTargetView(rtv, color);
}

class TextureWriter {
public:
    bool initialize(ID3D11Device* device, const D3D11_TEXTURE2D_DESC& source_desc) {
        desc_ = source_desc;
        desc_.Usage = D3D11_USAGE_STAGING;
        desc_.BindFlags = 0;
        desc_.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        desc_.MiscFlags = 0;

        HRESULT hr = device->CreateTexture2D(&desc_, nullptr, &staging_);
        if (FAILED(hr)) {
            print_hr("Create pattern staging", hr);
            return false;
        }
        return true;
    }

    bool copy_to(
        ID3D11DeviceContext* context,
        ID3D11Texture2D* texture,
        void (*fill)(D3D11_MAPPED_SUBRESOURCE*, const D3D11_TEXTURE2D_DESC&, void*),
        void* fill_context
    ) {
        D3D11_TEXTURE2D_DESC texture_desc = {};
        texture->GetDesc(&texture_desc);
        if (!matches(texture_desc)) {
            std::fprintf(stderr, "pattern staging does not match target texture\n");
            return false;
        }

        D3D11_MAPPED_SUBRESOURCE mapped = {};
        HRESULT hr = context->Map(staging_.Get(), 0, D3D11_MAP_WRITE, 0, &mapped);
        if (FAILED(hr)) {
            print_hr("Map pattern staging", hr);
            return false;
        }

        fill(&mapped, desc_, fill_context);
        context->Unmap(staging_.Get(), 0);
        context->CopyResource(texture, staging_.Get());
        return true;
    }

private:
    bool matches(const D3D11_TEXTURE2D_DESC& other) const {
        return staging_
            && other.Width == desc_.Width
            && other.Height == desc_.Height
            && other.MipLevels == desc_.MipLevels
            && other.ArraySize == desc_.ArraySize
            && other.Format == desc_.Format
            && other.SampleDesc.Count == 1
            && other.SampleDesc.Count == desc_.SampleDesc.Count;
    }

    D3D11_TEXTURE2D_DESC desc_ = {};
    ComPtr<ID3D11Texture2D> staging_;
};

struct PatternFillContext {
    vr::EVREye eye;
    int frame;
    int shift_x;
    int shift_y;
    bool rgba;
    bool static_pattern;
};

struct SolidFillContext {
    uint8_t b;
    uint8_t g;
    uint8_t r;
    bool rgba;
};

void write_pixel(uint8_t* pixel, uint8_t b, uint8_t g, uint8_t r, bool rgba) {
    if (rgba) {
        pixel[0] = r;
        pixel[1] = g;
        pixel[2] = b;
        pixel[3] = 255;
    } else {
        pixel[0] = b;
        pixel[1] = g;
        pixel[2] = r;
        pixel[3] = 255;
    }
}

void fill_pattern_mapped(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    void* context_ptr
) {
    auto* fill = static_cast<PatternFillContext*>(context_ptr);
    const UINT width = desc.Width;
    const UINT height = desc.Height;
    const int frame = fill->static_pattern ? 0 : fill->frame;
    const int bar_x = static_cast<int>((static_cast<uint64_t>(frame) * 17) % std::max<UINT>(1, width));
    const UINT bar_width = std::max<UINT>(16, width / 48);
    const int center_x = static_cast<int>(width / 2);
    const int center_y = static_cast<int>(height / 2);

    for (UINT y = 0; y < height; ++y) {
        auto* row = static_cast<uint8_t*>(mapped->pData) + static_cast<size_t>(y) * mapped->RowPitch;
        for (UINT x = 0; x < width; ++x) {
            int sx = static_cast<int>(x) - fill->shift_x;
            int sy = static_cast<int>(y) - fill->shift_y;
            bool in_bounds = sx >= 0 && sy >= 0 && sx < static_cast<int>(width) && sy < static_cast<int>(height);
            bool moving_bar = !fill->static_pattern
                && in_bounds
                && sx >= bar_x
                && sx < std::min<int>(static_cast<int>(width), bar_x + static_cast<int>(bar_width));
            bool center_cross = in_bounds
                && ((sx >= center_x - 3 && sx <= center_x + 3) || (sy >= center_y - 3 && sy <= center_y + 3));
            bool heavy_grid = in_bounds && (sx % 128 == 0 || sy % 128 == 0);
            bool light_grid = in_bounds && (sx % 32 == 0 || sy % 32 == 0);
            bool center_box = in_bounds
                && sx >= center_x - 96
                && sx <= center_x + 96
                && sy >= center_y - 64
                && sy <= center_y + 64;
            bool corner_marker = in_bounds && sx < static_cast<int>(width / 6) && sy < static_cast<int>(height / 6);
            bool frame_tick = !fill->static_pattern
                && in_bounds
                && sy > static_cast<int>(height) - 28
                && sx < static_cast<int>((static_cast<uint64_t>(frame % 120) * width) / 120);

            uint8_t b = fill->eye == vr::Eye_Right ? 128 : 48;
            uint8_t g = in_bounds ? 64 : 0;
            uint8_t r = fill->eye == vr::Eye_Left ? 128 : 48;
            if (!in_bounds) {
                r = 0;
                g = 0;
                b = 0;
            } else if (moving_bar) {
                r = 255;
                g = 255;
                b = 255;
            } else if (center_cross) {
                r = 255;
                g = 255;
                b = 255;
            } else if (center_box) {
                r = 30;
                g = 30;
                b = 30;
            } else if (corner_marker) {
                r = fill->eye == vr::Eye_Left ? 255 : 0;
                g = 255;
                b = fill->eye == vr::Eye_Right ? 255 : 0;
            } else if (frame_tick) {
                r = 255;
                g = 255;
                b = 0;
            } else if (heavy_grid) {
                r = 20;
                g = 20;
                b = 20;
            } else if (light_grid) {
                r = 88;
                g = 88;
                b = 88;
            }

            write_pixel(row + x * 4, b, g, r, fill->rgba);
        }
    }
}

void fill_solid_mapped(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    void* context_ptr
) {
    auto* fill = static_cast<SolidFillContext*>(context_ptr);
    for (UINT y = 0; y < desc.Height; ++y) {
        auto* row = static_cast<uint8_t*>(mapped->pData) + static_cast<size_t>(y) * mapped->RowPitch;
        for (UINT x = 0; x < desc.Width; ++x) {
            write_pixel(row + x * 4, fill->b, fill->g, fill->r, fill->rgba);
        }
    }
}

bool fill_eye_pattern(
    ID3D11DeviceContext* context,
    TextureWriter* writer,
    ID3D11Texture2D* texture,
    vr::EVREye eye,
    int frame,
    int shift_x,
    int shift_y,
    bool static_pattern
) {
    D3D11_TEXTURE2D_DESC desc = {};
    texture->GetDesc(&desc);
    if (desc.SampleDesc.Count != 1
        || (desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM && desc.Format != DXGI_FORMAT_R8G8B8A8_UNORM)) {
        return false;
    }

    PatternFillContext fill = {
        eye,
        frame,
        shift_x,
        shift_y,
        desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM,
        static_pattern,
    };
    return writer->copy_to(context, texture, fill_pattern_mapped, &fill);
}

bool fill_solid_bgra(
    ID3D11DeviceContext* context,
    TextureWriter* writer,
    ID3D11Texture2D* texture,
    uint8_t b,
    uint8_t g,
    uint8_t r
) {
    D3D11_TEXTURE2D_DESC desc = {};
    texture->GetDesc(&desc);
    if (desc.SampleDesc.Count != 1
        || (desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM && desc.Format != DXGI_FORMAT_R8G8B8A8_UNORM)) {
        return false;
    }

    SolidFillContext fill = { b, g, r, desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM };
    return writer->copy_to(context, texture, fill_solid_mapped, &fill);
}

HRESULT create_openvr_d3d_device(
    int32_t adapter_index,
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

    ComPtr<IDXGIAdapter1> adapter;
    if (adapter_index >= 0) {
        ComPtr<IDXGIFactory1> factory;
        HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(factory.GetAddressOf()));
        if (SUCCEEDED(hr)) {
            hr = factory->EnumAdapters1(static_cast<UINT>(adapter_index), &adapter);
        }
        if (FAILED(hr) || !adapter) {
            print_hr("Enum OpenVR adapter", hr);
            return hr;
        }
        std::printf("creating D3D11 device on OpenVR adapter %d\n", adapter_index);
        return D3D11CreateDevice(
            adapter.Get(),
            D3D_DRIVER_TYPE_UNKNOWN,
            nullptr,
            0,
            feature_levels,
            ARRAYSIZE(feature_levels),
            D3D11_SDK_VERSION,
            device,
            feature_level,
            context
        );
    }

    std::printf("creating D3D11 device on default adapter\n");
    return D3D11CreateDevice(
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
}

} // namespace

int main(int argc, char** argv) {
    Options options = parse_args(argc, argv);

    vr::EVRInitError init_error = vr::VRInitError_None;
    vr::IVRSystem* system = vr::VR_Init(&init_error, vr::VRApplication_Scene);
    if (init_error != vr::VRInitError_None || !system) {
        std::fprintf(stderr, "VR_Init failed: %s\n", vr::VR_GetVRInitErrorAsEnglishDescription(init_error));
        return 1;
    }

    vr::IVRCompositor* compositor = vr::VRCompositor();
    if (!compositor) {
        std::fprintf(stderr, "VRCompositor returned null\n");
        vr::VR_Shutdown();
        return 1;
    }
    compositor->SetTrackingSpace(vr::TrackingUniverseStanding);

    query_system(system, &options);
    std::printf(
        "OpenVR app loop width=%u height=%u frames=%d fps=%d format=%s samples=%u submit_msaa=%d bounds=%d static_pattern=%d eye_mode=%s right_shift=%d,%d\n",
        options.width,
        options.height,
        options.frames,
        options.fps,
        options.use_rgba ? "rgba" : "bgra",
        options.sample_count,
        options.submit_msaa ? 1 : 0,
        options.use_bounds ? 1 : 0,
        options.static_pattern ? 1 : 0,
        eye_mode_name(options.eye_mode),
        options.right_shift_x,
        options.right_shift_y
    );
    if (options.sample_count > 1 && options.submit_msaa) {
        std::fprintf(stderr, "warning: --submit-msaa intentionally submits raw MSAA textures; real runtimes may reject this boundary case\n");
    }

    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    D3D_FEATURE_LEVEL feature_level = D3D_FEATURE_LEVEL_11_0;
    HRESULT hr = create_openvr_d3d_device(options.adapter_index, &device, &feature_level, &context);
    print_hr("D3D11CreateDevice", hr);
    if (FAILED(hr)) {
        vr::VR_Shutdown();
        return 1;
    }

    DXGI_FORMAT format = options.use_rgba ? DXGI_FORMAT_R8G8B8A8_UNORM : DXGI_FORMAT_B8G8R8A8_UNORM;
    ComPtr<ID3D11Texture2D> left;
    ComPtr<ID3D11Texture2D> right;
    ComPtr<ID3D11RenderTargetView> left_rtv;
    ComPtr<ID3D11RenderTargetView> right_rtv;
    if (!create_texture(device.Get(), options.width, options.height, format, options.sample_count, &left, &left_rtv)
        || !create_texture(device.Get(), options.width, options.height, format, options.sample_count, &right, &right_rtv)) {
        vr::VR_Shutdown();
        return 1;
    }

    TextureWriter texture_writer;
    if (options.sample_count == 1) {
        D3D11_TEXTURE2D_DESC left_desc = {};
        left->GetDesc(&left_desc);
        if (!texture_writer.initialize(device.Get(), left_desc)) {
            vr::VR_Shutdown();
            return 1;
        }
    }

    ComPtr<ID3D11Texture2D> left_submit;
    ComPtr<ID3D11Texture2D> right_submit;
    if (options.sample_count > 1 && !options.submit_msaa) {
        if (!create_resolve_texture(device.Get(), options.width, options.height, format, &left_submit)
            || !create_resolve_texture(device.Get(), options.width, options.height, format, &right_submit)) {
            vr::VR_Shutdown();
            return 1;
        }
    } else {
        left_submit = left;
        right_submit = right;
    }

    vr::Texture_t left_texture = { left_submit.Get(), vr::TextureType_DirectX, vr::ColorSpace_Auto };
    bool shifted_right_eye = options.right_shift_x != 0 || options.right_shift_y != 0;
    vr::Texture_t right_texture = {
        (options.eye_mode == EyePatternMode::Mono && !shifted_right_eye ? left_submit.Get() : right_submit.Get()),
        vr::TextureType_DirectX,
        vr::ColorSpace_Auto,
    };
    vr::VRTextureBounds_t bounds = { 0.125f, 0.0f, 0.875f, 1.0f };
    vr::VRTextureBounds_t* bounds_ptr = options.use_bounds ? &bounds : nullptr;
    const auto frame_interval = std::chrono::microseconds(1000000 / options.fps);

    vr::TrackedDevicePose_t render_poses[vr::k_unMaxTrackedDeviceCount] = {};
    vr::TrackedDevicePose_t game_poses[vr::k_unMaxTrackedDeviceCount] = {};
    bool unexpected_error = false;
    for (int frame = 0; frame < options.frames; ++frame) {
        auto target_time = std::chrono::steady_clock::now() + frame_interval;

        vr::EVRCompositorError pose_result = compositor->WaitGetPoses(
            render_poses,
            vr::k_unMaxTrackedDeviceCount,
            game_poses,
            vr::k_unMaxTrackedDeviceCount
        );
        clear_eye(context.Get(), left_rtv.Get(), vr::Eye_Left, frame);
        clear_eye(context.Get(), right_rtv.Get(), vr::Eye_Right, frame);
        if (options.sample_count == 1) {
            fill_eye_pattern(
                context.Get(),
                &texture_writer,
                left.Get(),
                vr::Eye_Left,
                frame,
                0,
                0,
                options.static_pattern
            );
            if (options.eye_mode == EyePatternMode::LeftOnly) {
                fill_solid_bgra(context.Get(), &texture_writer, right.Get(), 0, 0, 0);
            } else if (options.eye_mode == EyePatternMode::RightOnly) {
                fill_solid_bgra(context.Get(), &texture_writer, left.Get(), 0, 0, 0);
                fill_eye_pattern(
                    context.Get(),
                    &texture_writer,
                    right.Get(),
                    vr::Eye_Right,
                    frame,
                    options.right_shift_x,
                    options.right_shift_y,
                    options.static_pattern
                );
            } else {
                fill_eye_pattern(
                    context.Get(),
                    &texture_writer,
                    right.Get(),
                    options.eye_mode == EyePatternMode::Mono ? vr::Eye_Left : vr::Eye_Right,
                    frame,
                    options.right_shift_x,
                    options.right_shift_y,
                    options.static_pattern
                );
            }
        }
        if (options.sample_count > 1 && !options.submit_msaa) {
            context->ResolveSubresource(left_submit.Get(), 0, left.Get(), 0, format);
            context->ResolveSubresource(right_submit.Get(), 0, right.Get(), 0, format);
        }
        context->Flush();

        vr::EVRCompositorError left_result = compositor->Submit(vr::Eye_Left, &left_texture, bounds_ptr, vr::Submit_Default);
        vr::EVRCompositorError right_result = compositor->Submit(vr::Eye_Right, &right_texture, bounds_ptr, vr::Submit_Default);
        compositor->PostPresentHandoff();

        bool submit_msaa_boundary = options.sample_count > 1 && options.submit_msaa;
        if (pose_result != vr::VRCompositorError_None
            || (!submit_msaa_boundary
                && (left_result != vr::VRCompositorError_None || right_result != vr::VRCompositorError_None))) {
            unexpected_error = true;
        }

        if (frame == 0 || frame % 30 == 0) {
            std::printf(
                "frame=%d pose=%d hmd_valid=%d left_submit=%d right_submit=%d\n",
                frame,
                pose_result,
                render_poses[vr::k_unTrackedDeviceIndex_Hmd].bPoseIsValid ? 1 : 0,
                left_result,
                right_result
            );
        }
        std::this_thread::sleep_until(target_time);
    }

    std::printf("done\n");
    vr::VR_Shutdown();
    return unexpected_error ? 1 : 0;
}
