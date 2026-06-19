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
#include <cmath>
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
    bool alignment_grid = false;
    bool stereo_scene = false;
    float scene_ipd_scale = 1.0f;
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

bool parse_float_range(const char* text, float min_value, float max_value, float* out) {
    char* end = nullptr;
    float value = std::strtof(text, &end);
    if (!text[0] || (end && *end) || !std::isfinite(value) || value < min_value || value > max_value) {
        return false;
    }
    *out = value;
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
        } else if (std::strcmp(arg, "--alignment-grid") == 0) {
            options.alignment_grid = true;
            options.static_pattern = true;
        } else if (std::strcmp(arg, "--stereo-scene") == 0) {
            options.stereo_scene = true;
            options.static_pattern = true;
        } else if (std::strcmp(arg, "--scene-ipd-scale") == 0) {
            if (!parse_float_range(next(), 0.0f, 2.0f, &options.scene_ipd_scale)) {
                std::fprintf(stderr, "invalid --scene-ipd-scale; expected 0.0..2.0\n");
                std::exit(2);
            }
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
                "[--static-pattern] [--alignment-grid] [--stereo-scene] [--scene-ipd-scale N] "
                "[--mono] [--left-only] [--right-only] [--right-shift-x N] "
                "[--right-shift-y N] [--no-properties]\n"
            );
            std::exit(0);
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", arg);
            std::exit(2);
        }
    }
    if (options.stereo_scene && (options.right_shift_x != 0 || options.right_shift_y != 0)) {
        std::fprintf(stderr, "--stereo-scene cannot be combined with --right-shift-x or --right-shift-y\n");
        std::exit(2);
    }
    if (options.stereo_scene && options.alignment_grid) {
        std::fprintf(stderr, "--stereo-scene cannot be combined with --alignment-grid\n");
        std::exit(2);
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
    bool alignment_grid;
    bool stereo_scene;
    float scene_ipd_scale;
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

void write_gray(uint8_t* pixel, uint8_t value, bool rgba) {
    write_pixel(pixel, value, value, value, rgba);
}

void draw_rect(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int left,
    int top,
    int width,
    int height,
    uint8_t value,
    bool rgba
) {
    int right = std::min<int>(static_cast<int>(desc.Width), left + width);
    int bottom = std::min<int>(static_cast<int>(desc.Height), top + height);
    left = std::max(0, left);
    top = std::max(0, top);

    for (int y = top; y < bottom; ++y) {
        auto* row = static_cast<uint8_t*>(mapped->pData) + static_cast<size_t>(y) * mapped->RowPitch;
        for (int x = left; x < right; ++x) {
            write_gray(row + x * 4, value, rgba);
        }
    }
}

void draw_segment_digit(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int x,
    int y,
    int digit,
    int scale,
    uint8_t value,
    bool rgba
) {
    static const uint8_t kSegments[10] = {
        0b0111111,
        0b0000110,
        0b1011011,
        0b1001111,
        0b1100110,
        0b1101101,
        0b1111101,
        0b0000111,
        0b1111111,
        0b1101111,
    };
    if (digit < 0 || digit > 9) {
        return;
    }

    int t = std::max(1, scale);
    int w = 4 * scale;
    int h = 7 * scale;
    uint8_t segments = kSegments[digit];
    if (segments & (1 << 0)) draw_rect(mapped, desc, x + t, y, w, t, value, rgba);
    if (segments & (1 << 1)) draw_rect(mapped, desc, x + w, y + t, t, h / 2 - t, value, rgba);
    if (segments & (1 << 2)) draw_rect(mapped, desc, x + w, y + h / 2 + t, t, h / 2 - t, value, rgba);
    if (segments & (1 << 3)) draw_rect(mapped, desc, x + t, y + h, w, t, value, rgba);
    if (segments & (1 << 4)) draw_rect(mapped, desc, x, y + h / 2 + t, t, h / 2 - t, value, rgba);
    if (segments & (1 << 5)) draw_rect(mapped, desc, x, y + t, t, h / 2 - t, value, rgba);
    if (segments & (1 << 6)) draw_rect(mapped, desc, x + t, y + h / 2, w, t, value, rgba);
}

void draw_plus_or_minus(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int x,
    int y,
    bool plus,
    int scale,
    uint8_t value,
    bool rgba
) {
    int t = std::max(1, scale);
    int h = 7 * scale;
    draw_rect(mapped, desc, x, y + h / 2, 4 * scale + t, t, value, rgba);
    if (plus) {
        draw_rect(mapped, desc, x + 2 * scale, y + 2 * scale, t, 3 * scale, value, rgba);
    }
}

void draw_signed_number(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int x,
    int y,
    int value,
    int scale,
    uint8_t color,
    bool rgba
) {
    bool plus = value >= 0;
    int magnitude = std::abs(value);
    draw_plus_or_minus(mapped, desc, x, y, plus, scale, color, rgba);
    x += 6 * scale;
    if (magnitude >= 10) {
        draw_segment_digit(mapped, desc, x, y, (magnitude / 10) % 10, scale, color, rgba);
        x += 6 * scale;
    }
    draw_segment_digit(mapped, desc, x, y, magnitude % 10, scale, color, rgba);
}

void draw_letter_l(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int x,
    int y,
    int scale,
    uint8_t value,
    bool rgba
) {
    draw_rect(mapped, desc, x, y, scale, 7 * scale, value, rgba);
    draw_rect(mapped, desc, x, y + 6 * scale, 5 * scale, scale, value, rgba);
}

void draw_letter_r(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int x,
    int y,
    int scale,
    uint8_t value,
    bool rgba
) {
    draw_rect(mapped, desc, x, y, scale, 7 * scale, value, rgba);
    draw_rect(mapped, desc, x, y, 4 * scale, scale, value, rgba);
    draw_rect(mapped, desc, x, y + 3 * scale, 4 * scale, scale, value, rgba);
    draw_rect(mapped, desc, x + 4 * scale, y + scale, scale, 2 * scale, value, rgba);
    draw_rect(mapped, desc, x + 2 * scale, y + 4 * scale, scale, scale, value, rgba);
    draw_rect(mapped, desc, x + 3 * scale, y + 5 * scale, scale, scale, value, rgba);
    draw_rect(mapped, desc, x + 4 * scale, y + 6 * scale, scale, scale, value, rgba);
}

void draw_letter_n(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int x,
    int y,
    int scale,
    uint8_t value,
    bool rgba
) {
    draw_rect(mapped, desc, x, y, scale, 7 * scale, value, rgba);
    draw_rect(mapped, desc, x + 4 * scale, y, scale, 7 * scale, value, rgba);
    for (int i = 0; i < 5; ++i) {
        draw_rect(mapped, desc, x + (i + 1) * scale, y + (i + 1) * scale, scale, scale, value, rgba);
    }
}

void draw_letter_m(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int x,
    int y,
    int scale,
    uint8_t value,
    bool rgba
) {
    draw_rect(mapped, desc, x, y, scale, 7 * scale, value, rgba);
    draw_rect(mapped, desc, x + 6 * scale, y, scale, 7 * scale, value, rgba);
    draw_rect(mapped, desc, x + 2 * scale, y + scale, scale, 2 * scale, value, rgba);
    draw_rect(mapped, desc, x + 4 * scale, y + scale, scale, 2 * scale, value, rgba);
    draw_rect(mapped, desc, x + 3 * scale, y + 3 * scale, scale, scale, value, rgba);
}

void draw_letter_f(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int x,
    int y,
    int scale,
    uint8_t value,
    bool rgba
) {
    draw_rect(mapped, desc, x, y, scale, 7 * scale, value, rgba);
    draw_rect(mapped, desc, x, y, 5 * scale, scale, value, rgba);
    draw_rect(mapped, desc, x, y + 3 * scale, 4 * scale, scale, value, rgba);
}

void draw_colored_rect(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    int left,
    int top,
    int width,
    int height,
    uint8_t b,
    uint8_t g,
    uint8_t r,
    bool rgba
) {
    int right = std::min<int>(static_cast<int>(desc.Width), left + width);
    int bottom = std::min<int>(static_cast<int>(desc.Height), top + height);
    left = std::max(0, left);
    top = std::max(0, top);

    for (int y = top; y < bottom; ++y) {
        auto* row = static_cast<uint8_t*>(mapped->pData) + static_cast<size_t>(y) * mapped->RowPitch;
        for (int x = left; x < right; ++x) {
            write_pixel(row + x * 4, b, g, r, rgba);
        }
    }
}

void draw_depth_label(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    char label,
    int x,
    int y,
    int scale,
    uint8_t value,
    bool rgba
) {
    switch (label) {
    case 'N':
        draw_letter_n(mapped, desc, x, y, scale, value, rgba);
        break;
    case 'M':
        draw_letter_m(mapped, desc, x, y, scale, value, rgba);
        break;
    case 'F':
        draw_letter_f(mapped, desc, x, y, scale, value, rgba);
        break;
    default:
        break;
    }
}

struct SceneObject {
    float world_x;
    float world_y;
    float depth;
    float size;
    uint8_t b;
    uint8_t g;
    uint8_t r;
    char label;
};

void draw_scene_object(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    const SceneObject& object,
    float eye_offset,
    bool rgba
) {
    const float focal = static_cast<float>(desc.Width) * 0.64f;
    const float center_x = static_cast<float>(desc.Width) * 0.5f;
    const float center_y = static_cast<float>(desc.Height) * 0.48f;
    int x = static_cast<int>(std::lround(center_x + (object.world_x - eye_offset) * focal / object.depth));
    int y = static_cast<int>(std::lround(center_y - object.world_y * focal / object.depth));
    int half = std::max(10, static_cast<int>(std::lround(object.size * focal / object.depth)));

    draw_colored_rect(mapped, desc, x - half, y - half, half * 2, half * 2, object.b, object.g, object.r, rgba);
    draw_colored_rect(mapped, desc, x - half, y - half, half * 2, 3, 250, 250, 250, rgba);
    draw_colored_rect(mapped, desc, x - half, y + half - 3, half * 2, 3, 250, 250, 250, rgba);
    draw_colored_rect(mapped, desc, x - half, y - half, 3, half * 2, 250, 250, 250, rgba);
    draw_colored_rect(mapped, desc, x + half - 3, y - half, 3, half * 2, 250, 250, 250, rgba);
    draw_depth_label(mapped, desc, object.label, x - half / 2, y - half / 2, std::max(3, half / 10), 255, rgba);
}

void fill_stereo_scene_mapped(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    PatternFillContext* fill
) {
    const UINT width = desc.Width;
    const UINT height = desc.Height;
    const int horizon = static_cast<int>(height * 0.52f);

    for (UINT y = 0; y < height; ++y) {
        auto* row = static_cast<uint8_t*>(mapped->pData) + static_cast<size_t>(y) * mapped->RowPitch;
        for (UINT x = 0; x < width; ++x) {
            uint8_t shade = y < static_cast<UINT>(horizon) ? 34 : static_cast<uint8_t>(24 + std::min<UINT>(80, (y - horizon) / 3));
            uint8_t b = static_cast<uint8_t>(shade + (fill->eye == vr::Eye_Right ? 10 : 0));
            uint8_t g = shade;
            uint8_t r = static_cast<uint8_t>(shade + (fill->eye == vr::Eye_Left ? 10 : 0));
            write_pixel(row + x * 4, b, g, r, fill->rgba);
        }
    }

    draw_colored_rect(mapped, desc, 0, horizon - 2, static_cast<int>(width), 4, 150, 150, 150, fill->rgba);
    for (int y = horizon + 34; y < static_cast<int>(height); y += 44) {
        draw_colored_rect(mapped, desc, 0, y, static_cast<int>(width), 2, 70, 70, 70, fill->rgba);
    }
    for (int x = static_cast<int>(width / 2) % 96; x < static_cast<int>(width); x += 96) {
        draw_colored_rect(mapped, desc, x, horizon, 2, static_cast<int>(height) - horizon, 62, 62, 62, fill->rgba);
    }

    float eye_offset = (fill->eye == vr::Eye_Left ? -0.032f : 0.032f) * fill->scene_ipd_scale;
    SceneObject objects[] = {
        { -0.20f, -0.08f, 0.85f, 0.075f, 50, 80, 230, 'N' },
        { 0.18f, 0.02f, 1.55f, 0.105f, 55, 200, 80, 'M' },
        { 0.00f, 0.12f, 2.85f, 0.150f, 220, 150, 50, 'F' },
    };
    for (const auto& object : objects) {
        draw_scene_object(mapped, desc, object, eye_offset, fill->rgba);
    }

    draw_colored_rect(mapped, desc, static_cast<int>(width / 2) - 3, 0, 6, static_cast<int>(height), 120, 120, 120, fill->rgba);
    draw_colored_rect(mapped, desc, 0, static_cast<int>(height / 2) - 3, static_cast<int>(width), 6, 120, 120, 120, fill->rgba);
    if (fill->eye == vr::Eye_Left) {
        draw_letter_l(mapped, desc, 28, 24, 8, 230, fill->rgba);
    } else {
        draw_letter_r(mapped, desc, 28, 24, 8, 230, fill->rgba);
    }
}

void fill_alignment_grid_mapped(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    PatternFillContext* fill
) {
    const UINT width = desc.Width;
    const UINT height = desc.Height;
    const int center_x = static_cast<int>(width / 2);
    const int center_y = static_cast<int>(height / 2);
    const int major = 128;
    const int minor = 32;
    const int label_scale = 4;

    for (UINT y = 0; y < height; ++y) {
        auto* row = static_cast<uint8_t*>(mapped->pData) + static_cast<size_t>(y) * mapped->RowPitch;
        for (UINT x = 0; x < width; ++x) {
            int sx = static_cast<int>(x) - fill->shift_x;
            int sy = static_cast<int>(y) - fill->shift_y;
            bool in_bounds = sx >= 0 && sy >= 0 && sx < static_cast<int>(width) && sy < static_cast<int>(height);
            uint8_t value = 24;
            if (!in_bounds) {
                value = 0;
            } else if (std::abs(sx - center_x) <= 3 || std::abs(sy - center_y) <= 3) {
                value = 245;
            } else if (std::abs(sx - center_x) <= 40 && std::abs(sy - center_y) <= 40) {
                value = 92;
            } else if ((sx - center_x) % major == 0 || (sy - center_y) % major == 0) {
                value = 150;
            } else if ((sx - center_x) % minor == 0 || (sy - center_y) % minor == 0) {
                value = 72;
            }
            write_gray(row + x * 4, value, fill->rgba);
        }
    }

    for (int grid_x = center_x % major; grid_x < static_cast<int>(width); grid_x += major) {
        int block = (grid_x - center_x) / major;
        if (block != 0) {
            draw_signed_number(
                mapped,
                desc,
                grid_x + fill->shift_x - 22,
                center_y + fill->shift_y + 14,
                block,
                label_scale,
                235,
                fill->rgba
            );
        }
    }

    for (int grid_y = center_y % major; grid_y < static_cast<int>(height); grid_y += major) {
        int block = (grid_y - center_y) / major;
        if (block != 0) {
            draw_signed_number(
                mapped,
                desc,
                center_x + fill->shift_x + 14,
                grid_y + fill->shift_y - 14,
                block,
                label_scale,
                235,
                fill->rgba
            );
        }
    }

    int shifted_center_x = center_x + fill->shift_x;
    int shifted_center_y = center_y + fill->shift_y;
    draw_rect(mapped, desc, shifted_center_x - 36, shifted_center_y - 36, 72, 4, 255, fill->rgba);
    draw_rect(mapped, desc, shifted_center_x - 36, shifted_center_y + 32, 72, 4, 255, fill->rgba);
    draw_rect(mapped, desc, shifted_center_x - 36, shifted_center_y - 36, 4, 72, 255, fill->rgba);
    draw_rect(mapped, desc, shifted_center_x + 32, shifted_center_y - 36, 4, 72, 255, fill->rgba);

    if (fill->eye == vr::Eye_Left) {
        draw_letter_l(mapped, desc, 28, 24, 8, 220, fill->rgba);
    } else {
        draw_letter_r(mapped, desc, 28, 24, 8, 220, fill->rgba);
    }
}

void fill_pattern_mapped(
    D3D11_MAPPED_SUBRESOURCE* mapped,
    const D3D11_TEXTURE2D_DESC& desc,
    void* context_ptr
) {
    auto* fill = static_cast<PatternFillContext*>(context_ptr);
    if (fill->stereo_scene) {
        fill_stereo_scene_mapped(mapped, desc, fill);
        return;
    }
    if (fill->alignment_grid) {
        fill_alignment_grid_mapped(mapped, desc, fill);
        return;
    }

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
    bool static_pattern,
    bool alignment_grid,
    bool stereo_scene,
    float scene_ipd_scale
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
        alignment_grid,
        stereo_scene,
        scene_ipd_scale,
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
        "OpenVR app loop width=%u height=%u frames=%d fps=%d format=%s samples=%u submit_msaa=%d bounds=%d static_pattern=%d alignment_grid=%d stereo_scene=%d scene_ipd_scale=%.2f eye_mode=%s right_shift=%d,%d\n",
        options.width,
        options.height,
        options.frames,
        options.fps,
        options.use_rgba ? "rgba" : "bgra",
        options.sample_count,
        options.submit_msaa ? 1 : 0,
        options.use_bounds ? 1 : 0,
        options.static_pattern ? 1 : 0,
        options.alignment_grid ? 1 : 0,
        options.stereo_scene ? 1 : 0,
        static_cast<double>(options.scene_ipd_scale),
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
                options.static_pattern,
                options.alignment_grid,
                options.stereo_scene,
                options.scene_ipd_scale
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
                    options.static_pattern,
                    options.alignment_grid,
                    options.stereo_scene,
                    options.scene_ipd_scale
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
                    options.static_pattern,
                    options.alignment_grid,
                    options.stereo_scene,
                    options.scene_ipd_scale
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
