#define COBJMACROS
#define CINTERFACE

#include <d3d11.h>
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <windows.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct ProbeScenario
{
    const char *name;
    unsigned int misc_flags;
    int use_keyed_mutex;
} ProbeScenario;

static const ProbeScenario SCENARIOS[] = {
    {"shared", D3D11_RESOURCE_MISC_SHARED, 0},
    {"shared_keyed", D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX, 1},
    {
        "shared_keyed_nthandle",
        D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX | D3D11_RESOURCE_MISC_SHARED_NTHANDLE,
        1,
    },
    {"shared_nthandle", D3D11_RESOURCE_MISC_SHARED_NTHANDLE, 0},
};

#ifdef _WIN64
#define WINE_DXGI_SHARED_MAP_NAME L"Local\\WineDxgiSharedHandleMapV3_64"
#else
#define WINE_DXGI_SHARED_MAP_NAME L"Local\\WineDxgiSharedHandleMapV3_32"
#endif

#define WINE_DXGI_SHARED_MAP_MAGIC 0x5744534bu
#define WINE_DXGI_SHARED_MAP_CAPACITY 4096

struct wine_dxgi_shared_texture2d_desc
{
    UINT width;
    UINT height;
    UINT mip_levels;
    UINT array_size;
    UINT format;
    UINT sample_count;
    UINT sample_quality;
    UINT usage;
    UINT bind_flags;
    UINT cpu_access_flags;
    UINT misc_flags;
};

struct wine_dxgi_shared_entry
{
    unsigned long long token;
    unsigned long long object_ptr;
    DWORD owner_pid;
    unsigned long long owner_process_start_time;
    UINT resource_type;
    UINT shared_misc_flags;
    UINT shared_features;
    UINT backing_type;
    UINT reserved0;
    unsigned long long backing_id;
    unsigned long long reserved1;
    unsigned int buffer_desc_placeholder[6];
    struct wine_dxgi_shared_texture2d_desc texture2d;
};

struct wine_dxgi_shared_state
{
    DWORD magic;
    unsigned long long counter;
    struct wine_dxgi_shared_entry entries[WINE_DXGI_SHARED_MAP_CAPACITY];
};

static void dump_dxgi_map_entry(unsigned long long token)
{
    HANDLE map = OpenFileMappingW(FILE_MAP_READ, FALSE, WINE_DXGI_SHARED_MAP_NAME);
    struct wine_dxgi_shared_state *state;
    unsigned int i;

    if (!map)
    {
        printf("[child] map_open_failed error=%lu\n", GetLastError());
        return;
    }

    state = (struct wine_dxgi_shared_state *)MapViewOfFile(map, FILE_MAP_READ, 0, 0, sizeof(*state));
    if (!state)
    {
        printf("[child] map_view_failed error=%lu\n", GetLastError());
        CloseHandle(map);
        return;
    }

    printf("[child] map_magic=0x%08lx counter=0x%llx\n", (unsigned long)state->magic, state->counter);
    if (state->magic != WINE_DXGI_SHARED_MAP_MAGIC)
    {
        printf("[child] map_magic_mismatch expected=0x%08x\n", (unsigned int)WINE_DXGI_SHARED_MAP_MAGIC);
    }

    for (i = 0; i < WINE_DXGI_SHARED_MAP_CAPACITY; ++i)
    {
        const struct wine_dxgi_shared_entry *entry = &state->entries[i];
        if (entry->token != token)
            continue;

        printf(
            "[child] map_entry token=0x%llx owner_pid=%lu type=%u backing=%u shared_misc=0x%x "
            "features=0x%x tex_fmt=%u tex_size=%ux%u tex_bind=0x%x tex_misc=0x%x\n",
            entry->token,
            (unsigned long)entry->owner_pid,
            (unsigned int)entry->resource_type,
            (unsigned int)entry->backing_type,
            (unsigned int)entry->shared_misc_flags,
            (unsigned int)entry->shared_features,
            (unsigned int)entry->texture2d.format,
            (unsigned int)entry->texture2d.width,
            (unsigned int)entry->texture2d.height,
            (unsigned int)entry->texture2d.bind_flags,
            (unsigned int)entry->texture2d.misc_flags);
        break;
    }

    UnmapViewOfFile(state);
    CloseHandle(map);
}

static const ProbeScenario *find_scenario(const char *name)
{
    unsigned int i;
    for (i = 0; i < sizeof(SCENARIOS) / sizeof(SCENARIOS[0]); i++)
    {
        if (strcmp(SCENARIOS[i].name, name) == 0)
            return &SCENARIOS[i];
    }
    return NULL;
}

static void print_scenario_names(void)
{
    unsigned int i;
    for (i = 0; i < sizeof(SCENARIOS) / sizeof(SCENARIOS[0]); i++)
        printf("%s\n", SCENARIOS[i].name);
}

static void print_hr(const char *label, HRESULT hr)
{
    printf("%s hr=0x%08lx\n", label, (unsigned long)hr);
}

static HRESULT create_device(ID3D11Device **device, ID3D11DeviceContext **ctx)
{
    D3D_FEATURE_LEVEL fl = D3D_FEATURE_LEVEL_11_0;
    D3D_FEATURE_LEVEL fls[] = {
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    };

    HRESULT hr = D3D11CreateDevice(
        NULL,
        D3D_DRIVER_TYPE_HARDWARE,
        NULL,
        0,
        fls,
        sizeof(fls) / sizeof(fls[0]),
        D3D11_SDK_VERSION,
        device,
        &fl,
        ctx
    );
    if (FAILED(hr))
    {
        hr = D3D11CreateDevice(
            NULL,
            D3D_DRIVER_TYPE_WARP,
            NULL,
            0,
            fls,
            sizeof(fls) / sizeof(fls[0]),
            D3D11_SDK_VERSION,
            device,
            &fl,
            ctx
        );
    }
    return hr;
}

static HRESULT read_first_pixel(
    ID3D11Device *device,
    ID3D11DeviceContext *ctx,
    ID3D11Texture2D *texture,
    unsigned int *bgra_out
)
{
    D3D11_TEXTURE2D_DESC desc;
    D3D11_TEXTURE2D_DESC staging_desc;
    ID3D11Texture2D *staging = NULL;
    D3D11_MAPPED_SUBRESOURCE mapped;
    HRESULT hr;

    ID3D11Texture2D_GetDesc(texture, &desc);
    staging_desc = desc;
    staging_desc.BindFlags = 0;
    staging_desc.MiscFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    staging_desc.Usage = D3D11_USAGE_STAGING;

    hr = ID3D11Device_CreateTexture2D(device, &staging_desc, NULL, &staging);
    if (FAILED(hr))
        return hr;

    ID3D11DeviceContext_CopyResource(ctx, (ID3D11Resource *)staging, (ID3D11Resource *)texture);
    ID3D11DeviceContext_Flush(ctx);

    hr = ID3D11DeviceContext_Map(ctx, (ID3D11Resource *)staging, 0, D3D11_MAP_READ, 0, &mapped);
    if (SUCCEEDED(hr))
    {
        const unsigned char *px = (const unsigned char *)mapped.pData;
        *bgra_out = ((unsigned int)px[3] << 24)
            | ((unsigned int)px[2] << 16)
            | ((unsigned int)px[1] << 8)
            | (unsigned int)px[0];
        ID3D11DeviceContext_Unmap(ctx, (ID3D11Resource *)staging, 0);
    }

    ID3D11Texture2D_Release(staging);
    return hr;
}

static int run_open_read_mode(
    const char *mode,
    const char *token_hex,
    const char *expected_hex,
    const char *acquire_key_hex
)
{
    ID3D11Device *device = NULL;
    ID3D11Device1 *device1 = NULL;
    ID3D11DeviceContext *ctx = NULL;
    ID3D11Texture2D *opened = NULL;
    IDXGIKeyedMutex *keyed = NULL;
    HRESULT hr;
    unsigned long long token = 0;
    unsigned long expected = 0;
    unsigned int pixel = 0;
    unsigned long acquire_key = 0;
    int use_open1 = strcmp(mode, "openread1") == 0;
    int use_keyed = acquire_key_hex != NULL && acquire_key_hex[0] != '\0';

    if (sscanf(token_hex, "%llx", &token) != 1)
    {
        printf("parse token failed: %s\n", token_hex);
        return 3;
    }

    dump_dxgi_map_entry(token);

    if (use_keyed)
        acquire_key = strtoul(acquire_key_hex, NULL, 10);

    hr = create_device(&device, &ctx);
    print_hr("[child] D3D11CreateDevice", hr);
    if (FAILED(hr))
        return 4;

    if (use_open1)
    {
        hr = ID3D11Device_QueryInterface(device, &IID_ID3D11Device1, (void **)&device1);
        print_hr("[child] QI(ID3D11Device1)", hr);
        if (FAILED(hr))
            goto done;

        hr = ID3D11Device1_OpenSharedResource1(device1, (HANDLE)(ULONG_PTR)token,
            &IID_ID3D11Texture2D, (void **)&opened);
        print_hr("[child] ID3D11Device1::OpenSharedResource1", hr);
    }
    else
    {
        hr = ID3D11Device_OpenSharedResource(device, (HANDLE)(ULONG_PTR)token,
            &IID_ID3D11Texture2D, (void **)&opened);
        print_hr("[child] ID3D11Device::OpenSharedResource", hr);
    }
    if (FAILED(hr))
        goto done;

    if (use_keyed)
    {
        hr = ID3D11Texture2D_QueryInterface(opened, &IID_IDXGIKeyedMutex, (void **)&keyed);
        print_hr("[child] QI(IDXGIKeyedMutex)", hr);
        if (FAILED(hr))
            goto done;

        printf("[child] keyed_acquire_key=%lu\n", acquire_key);
        hr = IDXGIKeyedMutex_AcquireSync(keyed, acquire_key, 5000);
        print_hr("[child] IDXGIKeyedMutex::AcquireSync", hr);
        if (FAILED(hr))
            goto done;
    }

    hr = read_first_pixel(device, ctx, opened, &pixel);
    print_hr("[child] read_first_pixel", hr);
    if (SUCCEEDED(hr))
        printf("[child] first_pixel_bgra=0x%08x\n", pixel);

    if (expected_hex && expected_hex[0])
    {
        expected = strtoul(expected_hex, NULL, 16);
        printf("[child] expected_bgra=0x%08lx\n", expected);
        if (pixel != (unsigned int)expected)
            hr = E_FAIL;
    }

    if (keyed)
    {
        HRESULT release_hr = IDXGIKeyedMutex_ReleaseSync(keyed, acquire_key + 1);
        print_hr("[child] IDXGIKeyedMutex::ReleaseSync", release_hr);
        if (FAILED(release_hr))
            hr = release_hr;
    }

done:
    if (keyed)
        IDXGIKeyedMutex_Release(keyed);
    if (opened)
        ID3D11Texture2D_Release(opened);
    if (ctx)
        ID3D11DeviceContext_Release(ctx);
    if (device1)
        ID3D11Device1_Release(device1);
    if (device)
        ID3D11Device_Release(device);
    return SUCCEEDED(hr) ? 0 : 2;
}

int main(int argc, char **argv)
{
    const ProbeScenario *scenario = &SCENARIOS[0];
    ID3D11Device *device_a = NULL;
    ID3D11Device *device_b = NULL;
    ID3D11Device1 *device1_b = NULL;
    ID3D11DeviceContext *ctx_a = NULL;
    ID3D11DeviceContext *ctx_b = NULL;
    ID3D11Texture2D *texture = NULL;
    ID3D11Texture2D *opened = NULL;
    ID3D11Texture2D *opened1 = NULL;
    ID3D11RenderTargetView *rtv = NULL;
    IDXGIResource *dxgi_resource = NULL;
    IDXGIResource1 *dxgi_resource1 = NULL;
    IDXGIKeyedMutex *parent_keyed = NULL;
    HRESULT hr;
    HANDLE shared = NULL;
    HANDLE shared_nt = NULL;
    unsigned int pixel_local = 0;
    unsigned int expected_bgra = 0xffff0000U;
    unsigned int child_key = 1;
    UINT probe_width = 64;
    UINT probe_height = 64;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    char exe_path[MAX_PATH];
    char cmdline[2048];
    DWORD child_exit = 0;
    const float clear_red[4] = {1.0f, 0.0f, 0.0f, 1.0f};

    if (argc == 2 && strcmp(argv[1], "--list-scenarios") == 0)
    {
        print_scenario_names();
        return 0;
    }

    if (argc >= 3 && (strcmp(argv[1], "openread") == 0 || strcmp(argv[1], "openread1") == 0))
        return run_open_read_mode(argv[1], argv[2], argc >= 4 ? argv[3] : NULL, argc >= 5 ? argv[4] : NULL);

    if (argc == 2)
    {
        scenario = find_scenario(argv[1]);
        if (!scenario)
        {
            printf("unknown scenario: %s\n", argv[1]);
            print_scenario_names();
            return 5;
        }
    }
    else if (argc == 3 && strcmp(argv[1], "scenario") == 0)
    {
        scenario = find_scenario(argv[2]);
        if (!scenario)
        {
            printf("unknown scenario: %s\n", argv[2]);
            print_scenario_names();
            return 5;
        }
    }
    else if (argc > 1)
    {
        printf("usage: %s [--list-scenarios|<scenario>|scenario <scenario>]\n", argv[0]);
        return 6;
    }

    printf("scenario=%s\n", scenario->name);
    printf("scenario_misc_flags=0x%08x\n", scenario->misc_flags);
    printf("scenario_use_keyed_mutex=%d\n", scenario->use_keyed_mutex);

    {
        const char *env_width = getenv("PROBE_WIDTH");
        const char *env_height = getenv("PROBE_HEIGHT");
        if (env_width && env_width[0])
        {
            probe_width = (UINT)strtoul(env_width, NULL, 10);
            if (!probe_width)
                probe_width = 64;
        }
        if (env_height && env_height[0])
        {
            probe_height = (UINT)strtoul(env_height, NULL, 10);
            if (!probe_height)
                probe_height = 64;
        }
    }
    printf("probe_size=%ux%u\n", probe_width, probe_height);

    hr = create_device(&device_a, &ctx_a);
    print_hr("D3D11CreateDevice(A)", hr);
    if (FAILED(hr))
        goto done;

    hr = create_device(&device_b, &ctx_b);
    print_hr("D3D11CreateDevice(B)", hr);
    if (FAILED(hr))
        goto done;

    D3D11_TEXTURE2D_DESC desc;
    desc.Width = probe_width;
    desc.Height = probe_height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.SampleDesc.Quality = 0;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
    desc.CPUAccessFlags = 0;
    desc.MiscFlags = scenario->misc_flags;

    hr = ID3D11Device_CreateTexture2D(device_a, &desc, NULL, &texture);
    print_hr("CreateTexture2D", hr);
    if (FAILED(hr))
        goto done;

    if (scenario->use_keyed_mutex)
    {
        hr = ID3D11Texture2D_QueryInterface(texture, &IID_IDXGIKeyedMutex, (void **)&parent_keyed);
        print_hr("QI(IDXGIKeyedMutex,parent_source)", hr);
        if (SUCCEEDED(hr))
        {
            hr = IDXGIKeyedMutex_AcquireSync(parent_keyed, 0, 5000);
            print_hr("IDXGIKeyedMutex::AcquireSync(parent_source,0)", hr);
        }
    }

    hr = ID3D11Device_CreateRenderTargetView(device_a, (ID3D11Resource *)texture, NULL, &rtv);
    print_hr("CreateRenderTargetView", hr);
    if (FAILED(hr))
        goto done;

    ID3D11DeviceContext_ClearRenderTargetView(ctx_a, rtv, clear_red);
    ID3D11DeviceContext_Flush(ctx_a);

    if (scenario->use_keyed_mutex && parent_keyed)
    {
        hr = IDXGIKeyedMutex_ReleaseSync(parent_keyed, child_key);
        print_hr("IDXGIKeyedMutex::ReleaseSync(parent_source,1)", hr);
    }

    hr = read_first_pixel(device_a, ctx_a, texture, &pixel_local);
    print_hr("same_process_read_first_pixel", hr);
    if (SUCCEEDED(hr))
        printf("same_process_first_pixel_bgra=0x%08x\n", pixel_local);

    hr = ID3D11Texture2D_QueryInterface(texture, &IID_IDXGIResource, (void **)&dxgi_resource);
    print_hr("QueryInterface(IID_IDXGIResource)", hr);
    if (FAILED(hr))
        goto done;

    hr = IDXGIResource_GetSharedHandle(dxgi_resource, &shared);
    print_hr("IDXGIResource::GetSharedHandle", hr);
    printf("shared_handle=%p\n", shared);
    if (SUCCEEDED(hr))
    {
        hr = ID3D11Device_OpenSharedResource(device_b, shared, &IID_ID3D11Texture2D, (void **)&opened);
        print_hr("ID3D11Device::OpenSharedResource", hr);
    }

    hr = ID3D11Texture2D_QueryInterface(texture, &IID_IDXGIResource1, (void **)&dxgi_resource1);
    print_hr("QueryInterface(IID_IDXGIResource1)", hr);
    if (SUCCEEDED(hr))
    {
        hr = IDXGIResource1_CreateSharedHandle(dxgi_resource1, NULL, GENERIC_ALL, NULL, &shared_nt);
        print_hr("IDXGIResource1::CreateSharedHandle", hr);
        printf("shared_nt_handle=%p\n", shared_nt);
        if (SUCCEEDED(hr))
        {
            hr = ID3D11Device_QueryInterface(device_b, &IID_ID3D11Device1, (void **)&device1_b);
            print_hr("QI(ID3D11Device1)", hr);
            if (SUCCEEDED(hr))
            {
                hr = ID3D11Device1_OpenSharedResource1(device1_b, shared_nt,
                    &IID_ID3D11Texture2D, (void **)&opened1);
                print_hr("ID3D11Device1::OpenSharedResource1", hr);
            }
        }
    }

    if (!GetModuleFileNameA(NULL, exe_path, sizeof(exe_path)))
    {
        printf("GetModuleFileNameA failed: %lu\n", GetLastError());
        goto done;
    }

    ZeroMemory(&si, sizeof(si));
    ZeroMemory(&pi, sizeof(pi));
    si.cb = sizeof(si);
    if (shared)
    {
        if (scenario->use_keyed_mutex)
        {
            snprintf(cmdline, sizeof(cmdline), "\"%s\" openread %llx %08x %u", exe_path,
                (unsigned long long)(ULONG_PTR)shared, expected_bgra, child_key);
        }
        else
        {
            snprintf(cmdline, sizeof(cmdline), "\"%s\" openread %llx %08x", exe_path,
                (unsigned long long)(ULONG_PTR)shared, expected_bgra);
        }

        if (CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        {
            WaitForSingleObject(pi.hProcess, INFINITE);
            GetExitCodeProcess(pi.hProcess, &child_exit);
            printf("child_openread_exit=%lu\n", child_exit);
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
        }
        else
        {
            printf("CreateProcessA(openread) failed: %lu\n", GetLastError());
        }
    }

    if (shared_nt)
    {
        unsigned int openread1_key = child_key;

        /*
         * The shared-handle child releases to key+1. Keep the nt-handle child
         * on the same sequence so both children can acquire deterministically.
         */
        if (scenario->use_keyed_mutex && shared && child_exit == 0)
            openread1_key = child_key + 1;

        ZeroMemory(&si, sizeof(si));
        ZeroMemory(&pi, sizeof(pi));
        si.cb = sizeof(si);
        if (scenario->use_keyed_mutex)
        {
            snprintf(cmdline, sizeof(cmdline), "\"%s\" openread1 %llx %08x %u", exe_path,
                (unsigned long long)(ULONG_PTR)shared_nt, expected_bgra, openread1_key);
        }
        else
        {
            snprintf(cmdline, sizeof(cmdline), "\"%s\" openread1 %llx %08x", exe_path,
                (unsigned long long)(ULONG_PTR)shared_nt, expected_bgra);
        }
        if (CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        {
            WaitForSingleObject(pi.hProcess, INFINITE);
            GetExitCodeProcess(pi.hProcess, &child_exit);
            printf("child_openread1_exit=%lu\n", child_exit);
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
        }
        else
        {
            printf("CreateProcessA(openread1) failed: %lu\n", GetLastError());
        }
    }

done:
    if (parent_keyed)
        IDXGIKeyedMutex_Release(parent_keyed);
    if (rtv)
        ID3D11RenderTargetView_Release(rtv);
    if (opened)
        ID3D11Texture2D_Release(opened);
    if (opened1)
        ID3D11Texture2D_Release(opened1);
    if (dxgi_resource)
        IDXGIResource_Release(dxgi_resource);
    if (dxgi_resource1)
        IDXGIResource1_Release(dxgi_resource1);
    if (shared_nt)
        CloseHandle(shared_nt);
    if (texture)
        ID3D11Texture2D_Release(texture);
    if (ctx_b)
        ID3D11DeviceContext_Release(ctx_b);
    if (ctx_a)
        ID3D11DeviceContext_Release(ctx_a);
    if (device1_b)
        ID3D11Device1_Release(device1_b);
    if (device_b)
        ID3D11Device_Release(device_b);
    if (device_a)
        ID3D11Device_Release(device_a);
    return 0;
}
