#define COBJMACROS
#define CINTERFACE

#include <d3d11.h>
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <windows.h>
#include <string.h>
#include <stdio.h>

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

static int run_open_mode(const char *token_hex)
{
    ID3D11Device *device = NULL;
    ID3D11Texture2D *opened = NULL;
    HRESULT hr;
    unsigned long long token = 0;

    if (sscanf(token_hex, "%llx", &token) != 1)
    {
        printf("parse token failed: %s\n", token_hex);
        return 3;
    }

    hr = create_device(&device, NULL);
    print_hr("[child] D3D11CreateDevice", hr);
    if (FAILED(hr))
        return 4;

    hr = ID3D11Device_OpenSharedResource(device, (HANDLE)(ULONG_PTR)token,
        &IID_ID3D11Texture2D, (void **)&opened);
    print_hr("[child] ID3D11Device::OpenSharedResource", hr);

    if (opened)
        ID3D11Texture2D_Release(opened);
    if (device)
        ID3D11Device_Release(device);

    return SUCCEEDED(hr) ? 0 : 2;
}

int main(int argc, char **argv)
{
    if (argc == 3 && strcmp(argv[1], "open") == 0)
        return run_open_mode(argv[2]);

    ID3D11Device *device_a = NULL;
    ID3D11Device *device_b = NULL;
    ID3D11DeviceContext *ctx = NULL;
    ID3D11Texture2D *texture = NULL;
    IDXGIResource *dxgi_resource = NULL;
    IDXGIResource1 *dxgi_resource1 = NULL;
    ID3D11Texture2D *opened = NULL;
    ID3D11Texture2D *opened1 = NULL;
    HRESULT hr;
    HANDLE shared = NULL;
    HANDLE shared_nt = NULL;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    char exe_path[MAX_PATH];
    char cmdline[2048];
    DWORD child_exit = 0;

    hr = create_device(&device_a, &ctx);
    print_hr("D3D11CreateDevice(A)", hr);
    if (FAILED(hr))
        goto done;

    hr = create_device(&device_b, NULL);
    print_hr("D3D11CreateDevice(B)", hr);
    if (FAILED(hr))
        goto done;

    D3D11_TEXTURE2D_DESC desc;
    desc.Width = 64;
    desc.Height = 64;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.SampleDesc.Quality = 0;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
    desc.CPUAccessFlags = 0;
    desc.MiscFlags = D3D11_RESOURCE_MISC_SHARED;

    hr = ID3D11Device_CreateTexture2D(device_a, &desc, NULL, &texture);
    print_hr("CreateTexture2D", hr);
    if (FAILED(hr))
        goto done;

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
            hr = ID3D11Device1_OpenSharedResource1((ID3D11Device1 *)device_b, shared_nt,
                &IID_ID3D11Texture2D, (void **)&opened1);
            print_hr("ID3D11Device1::OpenSharedResource1", hr);
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
        snprintf(cmdline, sizeof(cmdline), "\"%s\" open %llx", exe_path,
            (unsigned long long)(ULONG_PTR)shared);

        if (!CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        {
            printf("CreateProcessA failed: %lu\n", GetLastError());
            goto done;
        }

        WaitForSingleObject(pi.hProcess, INFINITE);
        GetExitCodeProcess(pi.hProcess, &child_exit);
        printf("child_open_exit=%lu\n", child_exit);
    }

    if (shared_nt)
    {
        snprintf(cmdline, sizeof(cmdline), "\"%s\" open %llx", exe_path,
            (unsigned long long)(ULONG_PTR)shared_nt);
        if (CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        {
            WaitForSingleObject(pi.hProcess, INFINITE);
            GetExitCodeProcess(pi.hProcess, &child_exit);
            printf("child_open_nt_exit=%lu\n", child_exit);
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
        }
    }
    if (pi.hThread)
        CloseHandle(pi.hThread);
    if (pi.hProcess)
        CloseHandle(pi.hProcess);

done:
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
    if (ctx)
        ID3D11DeviceContext_Release(ctx);
    if (device_b)
        ID3D11Device_Release(device_b);
    if (device_a)
        ID3D11Device_Release(device_a);
    return 0;
}
