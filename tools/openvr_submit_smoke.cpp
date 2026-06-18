// Minimal OpenVR submitter for the app-local openvr_api.dll shim smoke.
//
// Build from this repo root on macOS with:
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
//     -static-libstdc++ tools/openvr_submit_smoke.cpp \
//     -I$HOME/Developer/alvr/openvr/headers \
//     -L$HOME/Developer/alvr/openvr/lib/win64 -lopenvr_api \
//     -ld3d11 -ldxgi -lole32 \
//     -o $PROBE_OUT/openvr_submit_smoke.exe

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <openvr.h>
#include <wrl/client.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

namespace {

using Microsoft::WRL::ComPtr;

struct Options {
    UINT width = 640;
    UINT height = 360;
    int frames = 120;
    int fps = 20;
    UINT sample_count = 1;
    bool direct_interface = false;
    bool c_fntable = false;
    bool use_bounds = false;
    bool use_rgba = false;
};

using CppSubmitFn = vr::EVRCompositorError(__thiscall*)(
    void*, vr::EVREye, const vr::Texture_t*, const vr::VRTextureBounds_t*, vr::EVRSubmitFlags
);
using PostPresentHandoffFn = void(__thiscall*)(void*);
using CSubmitFn = vr::EVRCompositorError(__stdcall*)(
    vr::EVREye, vr::Texture_t*, vr::VRTextureBounds_t*, vr::EVRSubmitFlags
);
using CPostPresentHandoffFn = void(__stdcall*)();

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
        } else if (std::strcmp(arg, "--direct-interface") == 0) {
            options.direct_interface = true;
        } else if (std::strcmp(arg, "--c-fntable") == 0) {
            options.c_fntable = true;
        } else if (std::strcmp(arg, "--bounds") == 0) {
            options.use_bounds = true;
        } else if (std::strcmp(arg, "--rgba") == 0) {
            options.use_rgba = true;
        } else if (std::strcmp(arg, "--help") == 0) {
            std::printf(
                "usage: openvr_submit_smoke.exe [--width N] [--height N] [--frames N] "
                "[--fps N] [--msaa 1|2|4|8] [--bounds] [--rgba] "
                "[--direct-interface] [--c-fntable]\n"
            );
            std::exit(0);
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", arg);
            std::exit(2);
        }
    }
    return options;
}

void print_hr(const char* label, HRESULT hr) {
    std::printf("%-28s %s hr=0x%08lx\n", label, SUCCEEDED(hr) ? "OK" : "FAIL", (unsigned long)hr);
}

bool create_texture(
    ID3D11Device* device,
    UINT width,
    UINT height,
    DXGI_FORMAT format,
    UINT sample_count,
    const float color[4],
    ID3D11Texture2D** texture
) {
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = width;
    desc.Height = height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = format;
    desc.SampleDesc.Count = sample_count;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;

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

    ComPtr<ID3D11RenderTargetView> rtv;
    hr = device->CreateRenderTargetView(tex.Get(), nullptr, &rtv);
    if (FAILED(hr)) {
        print_hr("Create RTV", hr);
        return false;
    }

    ComPtr<ID3D11DeviceContext> context;
    device->GetImmediateContext(&context);
    context->ClearRenderTargetView(rtv.Get(), color);
    *texture = tex.Detach();
    return true;
}

} // namespace

int main(int argc, char** argv) {
    Options options = parse_args(argc, argv);
    if (options.direct_interface && options.c_fntable) {
        std::fprintf(stderr, "--direct-interface and --c-fntable are mutually exclusive\n");
        return 2;
    }
    std::printf(
        "OpenVR submit smoke width=%u height=%u frames=%d fps=%d format=%s samples=%u bounds=%d\n",
        options.width,
        options.height,
        options.frames,
        options.fps,
        options.use_rgba ? "rgba" : "bgra",
        options.sample_count,
        options.use_bounds ? 1 : 0
    );

    char runtime_path[4096] = {};
    uint32_t runtime_path_size = 0;
    bool runtime_installed = vr::VR_IsRuntimeInstalled();
    bool got_runtime_path = vr::VR_GetRuntimePath(
        runtime_path,
        sizeof(runtime_path),
        &runtime_path_size
    );
    std::printf(
        "runtime_installed=%d got_runtime_path=%d required=%u path=%s\n",
        runtime_installed ? 1 : 0,
        got_runtime_path ? 1 : 0,
        runtime_path_size,
        got_runtime_path ? runtime_path : "<none>"
    );
    if (got_runtime_path) {
        char runtime_bin[4096] = {};
        char runtime_bin_win64[4096] = {};
        std::snprintf(runtime_bin, sizeof(runtime_bin), "%s\\bin", runtime_path);
        std::snprintf(runtime_bin_win64, sizeof(runtime_bin_win64), "%s\\bin\\win64", runtime_path);
        SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_USER_DIRS);
        AddDllDirectory(L"C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin");
        AddDllDirectory(L"C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\win64");
        if (SetDllDirectoryA(runtime_bin)) {
            std::printf("SetDllDirectory=%s\n", runtime_bin);
        } else {
            std::printf("SetDllDirectory failed: %lu\n", GetLastError());
        }
        std::printf("AddDllDirectory=%s\n", runtime_bin_win64);
    }

    vr::EVRInitError init_error = vr::VRInitError_None;
    vr::VR_Init(&init_error, vr::VRApplication_Scene);
    if (init_error != vr::VRInitError_None) {
        std::fprintf(stderr, "VR_Init failed: %s\n", vr::VR_GetVRInitErrorAsEnglishDescription(init_error));
        return 1;
    }

    void* compositor = nullptr;
    CppSubmitFn submit = nullptr;
    PostPresentHandoffFn post_present_handoff = nullptr;
    CSubmitFn c_submit = nullptr;
    CPostPresentHandoffFn c_post_present_handoff = nullptr;
    if (options.c_fntable) {
        vr::EVRInitError interface_error = vr::VRInitError_None;
        compositor = VR_GetGenericInterface("FnTable:IVRCompositor_027", &interface_error);
        if (!compositor || interface_error != vr::VRInitError_None) {
            std::fprintf(stderr, "VR_GetGenericInterface compositor FnTable failed: %d\n", interface_error);
            vr::VR_Shutdown();
            return 1;
        }
        void** table = reinterpret_cast<void**>(compositor);
        c_submit = reinterpret_cast<CSubmitFn>(table[5]);
        c_post_present_handoff = reinterpret_cast<CPostPresentHandoffFn>(table[7]);
        std::printf("using compositor FnTable %p submit=%p\n", compositor, reinterpret_cast<void*>(c_submit));
    } else if (options.direct_interface) {
        vr::EVRInitError interface_error = vr::VRInitError_None;
        compositor = VR_GetGenericInterface(vr::IVRCompositor_Version, &interface_error);
        if (!compositor || interface_error != vr::VRInitError_None) {
            std::fprintf(stderr, "VR_GetGenericInterface compositor failed: %d\n", interface_error);
            vr::VR_Shutdown();
            return 1;
        }
        void** vtable = *reinterpret_cast<void***>(compositor);
        submit = reinterpret_cast<CppSubmitFn>(vtable[5]);
        post_present_handoff = reinterpret_cast<PostPresentHandoffFn>(vtable[7]);
        std::printf("using direct compositor interface %p submit=%p\n", compositor, reinterpret_cast<void*>(submit));
    } else {
        vr::IVRCompositor* typed_compositor = vr::VRCompositor();
        if (!typed_compositor) {
            std::fprintf(stderr, "VRCompositor returned null\n");
            vr::VR_Shutdown();
            return 1;
        }
        compositor = typed_compositor;
        void** vtable = *reinterpret_cast<void***>(compositor);
        submit = reinterpret_cast<CppSubmitFn>(vtable[5]);
        post_present_handoff = reinterpret_cast<PostPresentHandoffFn>(vtable[7]);
    }

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
        vr::VR_Shutdown();
        return 1;
    }

    float left_color[4] = { 0.05f, 0.05f, 0.95f, 1.0f };
    float right_color[4] = { 0.95f, 0.05f, 0.05f, 1.0f };
    DXGI_FORMAT texture_format = options.use_rgba ? DXGI_FORMAT_R8G8B8A8_UNORM : DXGI_FORMAT_B8G8R8A8_UNORM;
    ComPtr<ID3D11Texture2D> left;
    ComPtr<ID3D11Texture2D> right;
    if (!create_texture(device.Get(), options.width, options.height, texture_format, options.sample_count, left_color, &left)
        || !create_texture(device.Get(), options.width, options.height, texture_format, options.sample_count, right_color, &right)) {
        vr::VR_Shutdown();
        return 1;
    }

    vr::Texture_t left_texture = { left.Get(), vr::TextureType_DirectX, vr::ColorSpace_Auto };
    vr::Texture_t right_texture = { right.Get(), vr::TextureType_DirectX, vr::ColorSpace_Auto };
    vr::VRTextureBounds_t bounds = { 0.125f, 0.0f, 0.875f, 1.0f };
    vr::VRTextureBounds_t* bounds_ptr = options.use_bounds ? &bounds : nullptr;
    const auto frame_interval = std::chrono::microseconds(1000000 / options.fps);
    for (int frame = 0; frame < options.frames; ++frame) {
        auto target_time = std::chrono::steady_clock::now() + frame_interval;
        vr::EVRCompositorError left_result = options.c_fntable
            ? c_submit(vr::Eye_Left, &left_texture, bounds_ptr, vr::Submit_Default)
            : submit(compositor, vr::Eye_Left, &left_texture, bounds_ptr, vr::Submit_Default);
        vr::EVRCompositorError right_result = options.c_fntable
            ? c_submit(vr::Eye_Right, &right_texture, bounds_ptr, vr::Submit_Default)
            : submit(compositor, vr::Eye_Right, &right_texture, bounds_ptr, vr::Submit_Default);
        if (frame == 0 || frame % 30 == 0) {
            std::printf(
                "frame=%d left_submit=%d right_submit=%d\n",
                frame,
                left_result,
                right_result
            );
        }
        if (c_post_present_handoff) {
            c_post_present_handoff();
        } else if (post_present_handoff) {
            post_present_handoff(compositor);
        }
        std::this_thread::sleep_until(target_time);
    }

    std::printf("done\n");
    vr::VR_Shutdown();
    return 0;
}
