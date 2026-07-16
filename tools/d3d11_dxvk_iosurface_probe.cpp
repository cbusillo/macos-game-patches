#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <dxgi.h>
#include <vulkan/vulkan.h>

#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

#include "alvr_iosurface_bridge/iosurface_handoff_protocol.h"

namespace dxvk_probe {

const GUID kIidDxgiDevice = {
    0x54ec77fa,
    0x1377,
    0x44e6,
    {0x8c, 0x32, 0x88, 0xfd, 0x5f, 0x44, 0xc8, 0x4c},
};
const GUID kIidDxgiVkInteropSurface = {
    0x5546cf8c,
    0x77e7,
    0x4341,
    {0xb0, 0x5d, 0x8d, 0x4d, 0x50, 0x00, 0xe7, 0x7d},
};
const GUID kIidDxgiVkInteropDevice = {
    0xe2ef5fa5,
    0xdc21,
    0x4af7,
    {0x90, 0xc4, 0xf6, 0x7e, 0xf6, 0xa0, 0x93, 0x23},
};
const GUID kIidDxgiVkInteropDevice1 = {
    0xe2ef5fa5,
    0xdc21,
    0x4af7,
    {0x90, 0xc4, 0xf6, 0x7e, 0xf6, 0xa0, 0x93, 0x24},
};

struct IDXGIVkInteropDevice;

struct IDXGIVkInteropSurface : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE GetDevice(
        IDXGIVkInteropDevice **device) = 0;

    virtual HRESULT STDMETHODCALLTYPE GetVulkanImageInfo(
        VkImage *image,
        VkImageLayout *layout,
        VkImageCreateInfo *createInfo) = 0;
};

struct IDXGIVkInteropDevice : public IUnknown {
    virtual void STDMETHODCALLTYPE GetVulkanHandles(
        VkInstance *instance,
        VkPhysicalDevice *physicalDevice,
        VkDevice *device) = 0;

    virtual void STDMETHODCALLTYPE GetSubmissionQueue(
        VkQueue *queue,
        uint32_t *queueFamilyIndex) = 0;

    virtual void STDMETHODCALLTYPE TransitionSurfaceLayout(
        IDXGIVkInteropSurface *surface,
        const VkImageSubresourceRange *subresources,
        VkImageLayout oldLayout,
        VkImageLayout newLayout) = 0;

    virtual void STDMETHODCALLTYPE FlushRenderingCommands() = 0;
    virtual void STDMETHODCALLTYPE LockSubmissionQueue() = 0;
    virtual void STDMETHODCALLTYPE ReleaseSubmissionQueue() = 0;
};

struct D3D11_TEXTURE2D_DESC1;

struct IDXGIVkInteropDevice1 : public IDXGIVkInteropDevice {
    virtual void STDMETHODCALLTYPE GetSubmissionQueue1(
        VkQueue *queue,
        uint32_t *queueIndex,
        uint32_t *queueFamilyIndex) = 0;

    virtual HRESULT STDMETHODCALLTYPE CreateTexture2DFromVkImage(
        const D3D11_TEXTURE2D_DESC1 *description,
        VkImage image,
        ID3D11Texture2D **texture) = 0;
};

using PFN_vkUseIOSurfaceMVK = VkResult(VKAPI_PTR *)(VkImage image,
                                                    void *ioSurface);
using PFN_vkGetIOSurfaceMVK = void(VKAPI_PTR *)(VkImage image,
                                               void **ioSurface);
using BridgeScalarFunction = LONG(WINAPI *)(uint64_t input, uint64_t *output);
using BridgeAttachFunction = LONG(WINAPI *)(uint64_t image,
                                           int32_t *vkResult,
                                           uint32_t *surfaceId,
                                           uint32_t *machPort);
using BridgeReleasePortFunction = LONG(WINAPI *)(uint32_t machPort);

struct AlvrIosurfaceBindResult {
    uint64_t frameId;
    int32_t vkResult;
    uint32_t verificationStatus;
    uint32_t slotIndex;
    uint32_t generation;
    uint32_t surfaceId;
    uint32_t width;
    uint32_t height;
    uint32_t bytesPerRow;
    uint32_t pixelFormat;
    uint32_t producerPid;
    uint8_t expectedBgra[4];
};

static_assert(sizeof(AlvrIosurfaceBindResult) == 56);

using BridgeImportBindFunction = LONG(WINAPI *)(
    uint64_t image,
    const char *serviceName,
    uint64_t sessionNonce,
    AlvrIosurfaceBindResult *result);
using BridgeSignalReadyFunction = LONG(WINAPI *)(
    const char *serviceName,
    uint64_t sessionNonce,
    const AlvrIosurfaceBindResult *result,
    uint32_t verificationStatus);

constexpr const char *kReadyFile =
    "C:\\alvr-probes\\d3d11_dxvk_iosurface_ready.txt";
constexpr const char *kDoneFile =
    "C:\\alvr-probes\\d3d11_dxvk_iosurface_done.txt";

struct ProbeOptions {
    uint32_t width = 64;
    uint32_t height = 64;
    bool waitForNativeConsumer = false;
    std::string nativeSurfaceService;
    uint64_t nativeSurfaceNonce = 0;
};

template <typename Interface>
class ComPtr {
  public:
    ComPtr() = default;
    ComPtr(const ComPtr &) = delete;
    ComPtr &operator=(const ComPtr &) = delete;

    ~ComPtr() {
        reset();
    }

    Interface *get() const {
        return value_;
    }

    Interface **put() {
        reset();
        return &value_;
    }

    Interface *operator->() const {
        return value_;
    }

    explicit operator bool() const {
        return value_ != nullptr;
    }

    void reset() {
        if (value_ != nullptr) {
            value_->Release();
            value_ = nullptr;
        }
    }

  private:
    Interface *value_ = nullptr;
};

std::string formatHresult(HRESULT result) {
    std::ostringstream stream;
    stream << "0x" << std::hex << std::setw(8) << std::setfill('0')
           << static_cast<uint32_t>(result);
    return stream.str();
}

double performanceMilliseconds(LARGE_INTEGER start, LARGE_INTEGER end) {
    LARGE_INTEGER frequency{};
    QueryPerformanceFrequency(&frequency);
    return static_cast<double>(end.QuadPart - start.QuadPart) * 1000.0 /
           static_cast<double>(frequency.QuadPart);
}

std::string utf8(const wchar_t *value) {
    if (value == nullptr || *value == L'\0') {
        return {};
    }

    const int required = WideCharToMultiByte(
        CP_UTF8, 0, value, -1, nullptr, 0, nullptr, nullptr);
    if (required <= 1) {
        return {};
    }
    std::string result(static_cast<size_t>(required), '\0');
    WideCharToMultiByte(
        CP_UTF8, 0, value, -1, result.data(), required, nullptr, nullptr);
    result.pop_back();
    return result;
}

void printModulePath(const wchar_t *moduleName) {
    HMODULE module = GetModuleHandleW(moduleName);
    if (module == nullptr) {
        std::wcout << L"PROBE module name=" << moduleName << L" loaded=false\n";
        return;
    }

    wchar_t path[MAX_PATH] = {};
    const DWORD length = GetModuleFileNameW(module, path, MAX_PATH);
    std::cout << "PROBE module name=\"" << utf8(moduleName)
              << "\" loaded=true path=\""
              << (length > 0 ? utf8(path) : std::string("unknown")) << "\"\n";
}

bool isExecutableAddress(const void *address) {
    if (address == nullptr) {
        return false;
    }

    MEMORY_BASIC_INFORMATION memoryInfo{};
    if (VirtualQuery(address, &memoryInfo, sizeof(memoryInfo)) == 0 ||
        memoryInfo.State != MEM_COMMIT) {
        return false;
    }

    const DWORD protection = memoryInfo.Protect & 0xff;
    return protection == PAGE_EXECUTE || protection == PAGE_EXECUTE_READ ||
           protection == PAGE_EXECUTE_READWRITE ||
           protection == PAGE_EXECUTE_WRITECOPY;
}

bool printInterfaceLayout(const char *label, IUnknown *interfaceValue) {
    if (interfaceValue == nullptr) {
        std::cout << "PROBE interface_layout name=" << label
                  << " pointer=0x0 result=invalid\n";
        return false;
    }

    auto **vtable = *reinterpret_cast<void ***>(interfaceValue);
    std::cout << "PROBE interface_layout name=" << label
              << " pointer=0x" << std::hex
              << reinterpret_cast<uintptr_t>(interfaceValue)
              << " vtable=0x" << reinterpret_cast<uintptr_t>(vtable);
    bool allRequiredSlotsExecutable = true;
    for (size_t slot = 0; slot < 5; ++slot) {
        const bool executable = isExecutableAddress(vtable[slot]);
        std::cout << " slot" << slot << "=0x"
                  << reinterpret_cast<uintptr_t>(vtable[slot]) << ':'
                  << (executable ? "exec" : "data");
        if (!executable) {
            allRequiredSlotsExecutable = false;
        }
    }
    std::cout << std::dec << " result="
              << (allRequiredSlotsExecutable ? "valid" : "invalid") << '\n';
    return allRequiredSlotsExecutable;
}

template <typename Function>
Function resolveVulkanFunction(PFN_vkGetInstanceProcAddr getInstanceProcAddr,
                               VkInstance instance,
                               const char *name) {
    if (getInstanceProcAddr == nullptr) {
        return nullptr;
    }
    return reinterpret_cast<Function>(getInstanceProcAddr(instance, name));
}

template <typename Function>
Function resolveModuleFunction(HMODULE module, const char *name) {
    Function function = nullptr;
    if (module == nullptr) {
        return function;
    }

    const FARPROC rawFunction = GetProcAddress(module, name);
    static_assert(sizeof(function) == sizeof(rawFunction));
    std::memcpy(&function, &rawFunction, sizeof(function));
    return function;
}

bool writeReadyFile(uint32_t surfaceId, uint32_t width, uint32_t height) {
    std::ostringstream payload;
    payload << "surface_id=" << surfaceId << '\n'
            << "width=" << width << '\n'
            << "height=" << height << '\n'
            << "expected_bgra=0,0,255,255\n";
    const std::string contents = payload.str();

    HANDLE file = CreateFileA(kReadyFile,
                              GENERIC_WRITE,
                              FILE_SHARE_READ,
                              nullptr,
                              CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }

    DWORD bytesWritten = 0;
    const BOOL wrote = WriteFile(file,
                                 contents.data(),
                                 static_cast<DWORD>(contents.size()),
                                 &bytesWritten,
                                 nullptr);
    const BOOL flushed = FlushFileBuffers(file);
    CloseHandle(file);
    return wrote && flushed && bytesWritten == contents.size();
}

bool waitForConsumer(DWORD timeoutMilliseconds, ULONGLONG *elapsedMilliseconds) {
    const ULONGLONG start = GetTickCount64();
    while (GetTickCount64() - start < timeoutMilliseconds) {
        if (GetFileAttributesA(kDoneFile) != INVALID_FILE_ATTRIBUTES) {
            *elapsedMilliseconds = GetTickCount64() - start;
            return true;
        }
        Sleep(10);
    }

    *elapsedMilliseconds = GetTickCount64() - start;
    return false;
}

bool parseDimension(const char *text, uint32_t *value) {
    if (text == nullptr || *text == '\0') {
        return false;
    }

    errno = 0;
    char *end = nullptr;
    const unsigned long long parsed = std::strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed == 0 ||
        parsed > 16384) {
        return false;
    }
    *value = static_cast<uint32_t>(parsed);
    return true;
}

bool parseNonce(const char *text, uint64_t *value) {
    if (text == nullptr || *text == '\0') {
        return false;
    }

    errno = 0;
    char *end = nullptr;
    const unsigned long long parsed = std::strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed == 0) {
        return false;
    }
    *value = static_cast<uint64_t>(parsed);
    return true;
}

int runProbe(const ProbeOptions &options) {
    std::cout << "PROBE options width=" << options.width
              << " height=" << options.height
              << " wait_consumer="
              << (options.waitForNativeConsumer ? "true" : "false")
              << " native_surface_service=\""
              << options.nativeSurfaceService << "\"\n";
    constexpr UINT creationFlags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
    constexpr D3D_FEATURE_LEVEL requestedLevels[] = {
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    };

    ComPtr<ID3D11Device> d3dDevice;
    ComPtr<ID3D11DeviceContext> d3dContext;
    D3D_FEATURE_LEVEL selectedLevel = D3D_FEATURE_LEVEL_10_0;
    const HRESULT deviceResult = D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        creationFlags,
        requestedLevels,
        static_cast<UINT>(std::size(requestedLevels)),
        D3D11_SDK_VERSION,
        d3dDevice.put(),
        &selectedLevel,
        d3dContext.put());
    std::cout << "PROBE d3d11_create hr=" << formatHresult(deviceResult)
              << " feature_level=0x" << std::hex
              << static_cast<unsigned>(selectedLevel) << std::dec << '\n';
    if (FAILED(deviceResult)) {
        return 1;
    }

    printModulePath(L"d3d11.dll");
    printModulePath(L"dxgi.dll");

    ComPtr<IDXGIDevice> dxgiDevice;
    HRESULT result = d3dDevice->QueryInterface(
        kIidDxgiDevice, reinterpret_cast<void **>(dxgiDevice.put()));
    if (FAILED(result)) {
        std::cout << "PROBE dxgi_device hr=" << formatHresult(result)
                  << " result=fail\n";
        return 1;
    }

    ComPtr<IDXGIAdapter> adapter;
    result = dxgiDevice->GetAdapter(adapter.put());
    if (FAILED(result)) {
        std::cout << "PROBE adapter hr=" << formatHresult(result)
                  << " result=fail\n";
        return 1;
    }

    DXGI_ADAPTER_DESC adapterDescription{};
    result = adapter->GetDesc(&adapterDescription);
    if (FAILED(result)) {
        std::cout << "PROBE adapter_desc hr=" << formatHresult(result)
                  << " result=fail\n";
        return 1;
    }
    std::cout << "PROBE adapter name=\"" << utf8(adapterDescription.Description)
              << "\" vendor=0x" << std::hex << adapterDescription.VendorId
              << " device=0x" << adapterDescription.DeviceId << std::dec << '\n';

    D3D11_TEXTURE2D_DESC handoffDescription{};
    handoffDescription.Width = options.width;
    handoffDescription.Height = options.height;
    handoffDescription.MipLevels = 1;
    handoffDescription.ArraySize = 1;
    handoffDescription.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    handoffDescription.SampleDesc.Count = 1;
    handoffDescription.Usage = D3D11_USAGE_DEFAULT;
    handoffDescription.BindFlags = 0;

    ComPtr<ID3D11Texture2D> handoffTexture;
    result = d3dDevice->CreateTexture2D(
        &handoffDescription, nullptr, handoffTexture.put());
    std::cout << "PROBE handoff_texture_create hr=" << formatHresult(result)
              << " format=" << static_cast<unsigned>(handoffDescription.Format)
              << " bind_flags=0x" << std::hex << handoffDescription.BindFlags
              << std::dec << '\n';
    if (FAILED(result)) {
        return 1;
    }

    ComPtr<IDXGIVkInteropSurface> interopSurface;
    result = handoffTexture->QueryInterface(
        kIidDxgiVkInteropSurface,
        reinterpret_cast<void **>(interopSurface.put()));
    std::cout << "PROBE interop_surface hr=" << formatHresult(result)
              << " available=" << (SUCCEEDED(result) ? "true" : "false")
              << '\n';
    if (FAILED(result)) {
        return 2;
    }

    const bool surfaceLayoutValid = printInterfaceLayout(
        "IDXGIVkInteropSurface",
        reinterpret_cast<IUnknown *>(interopSurface.get()));

    ComPtr<IDXGIVkInteropDevice> interopDevice;
    result = d3dDevice->QueryInterface(
        kIidDxgiVkInteropDevice,
        reinterpret_cast<void **>(interopDevice.put()));
    std::cout << "PROBE interop_device hr=" << formatHresult(result)
              << " available=" << (SUCCEEDED(result) ? "true" : "false")
              << '\n';
    if (FAILED(result) && surfaceLayoutValid) {
        result = interopSurface->GetDevice(interopDevice.put());
        std::cout << "PROBE interop_device_from_surface hr="
                  << formatHresult(result)
                  << " available=" << (SUCCEEDED(result) ? "true" : "false")
                  << '\n';
    }
    if (FAILED(result) || !interopDevice) {
        return 2;
    }
    printInterfaceLayout("IDXGIVkInteropDevice",
                         reinterpret_cast<IUnknown *>(interopDevice.get()));

    ComPtr<IDXGIVkInteropDevice1> interopDevice1;
    result = interopDevice->QueryInterface(
        kIidDxgiVkInteropDevice1,
        reinterpret_cast<void **>(interopDevice1.put()));
    std::cout << "PROBE interop_device1 hr=" << formatHresult(result)
              << " available=" << (SUCCEEDED(result) ? "true" : "false")
              << '\n';

    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physicalDevice = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    interopDevice->GetVulkanHandles(&instance, &physicalDevice, &device);

    VkQueue submissionQueue = VK_NULL_HANDLE;
    uint32_t queueFamilyIndex = UINT32_MAX;
    interopDevice->GetSubmissionQueue(&submissionQueue, &queueFamilyIndex);

    VkImage image = VK_NULL_HANDLE;
    VkImageLayout imageLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VkImageCreateInfo imageInfo{};
    imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
    result = surfaceLayoutValid
                 ? interopSurface->GetVulkanImageInfo(
                       &image, &imageLayout, &imageInfo)
                 : E_NOINTERFACE;
    std::cout << "PROBE vulkan_image hr=" << formatHresult(result)
              << " instance=0x" << std::hex
              << reinterpret_cast<uintptr_t>(instance)
              << " physical_device=0x"
              << reinterpret_cast<uintptr_t>(physicalDevice)
              << " device=0x" << reinterpret_cast<uintptr_t>(device)
              << " image=0x" << reinterpret_cast<uintptr_t>(image)
              << " queue=0x" << reinterpret_cast<uintptr_t>(submissionQueue)
              << std::dec << " queue_family=" << queueFamilyIndex
              << " layout=" << static_cast<int>(imageLayout)
              << " format=" << static_cast<int>(imageInfo.format)
              << " width=" << imageInfo.extent.width
              << " height=" << imageInfo.extent.height
              << " usage=0x" << std::hex << imageInfo.usage << std::dec << '\n';
    if (FAILED(result) || instance == VK_NULL_HANDLE || device == VK_NULL_HANDLE ||
        image == VK_NULL_HANDLE || submissionQueue == VK_NULL_HANDLE) {
        return 2;
    }

    HMODULE vulkanModule = LoadLibraryW(L"vulkan-1.dll");
    printModulePath(L"vulkan-1.dll");
    const auto getInstanceProcAddr =
        resolveModuleFunction<PFN_vkGetInstanceProcAddr>(
            vulkanModule, "vkGetInstanceProcAddr");
    const auto queueWaitIdle = resolveVulkanFunction<PFN_vkQueueWaitIdle>(
        getInstanceProcAddr, instance, "vkQueueWaitIdle");
    const auto queueSubmit = resolveVulkanFunction<PFN_vkQueueSubmit>(
        getInstanceProcAddr, instance, "vkQueueSubmit");
    const auto createFence = resolveVulkanFunction<PFN_vkCreateFence>(
        getInstanceProcAddr, instance, "vkCreateFence");
    const auto waitForFences = resolveVulkanFunction<PFN_vkWaitForFences>(
        getInstanceProcAddr, instance, "vkWaitForFences");
    const auto destroyFence = resolveVulkanFunction<PFN_vkDestroyFence>(
        getInstanceProcAddr, instance, "vkDestroyFence");
    const auto useIOSurface = resolveVulkanFunction<PFN_vkUseIOSurfaceMVK>(
        getInstanceProcAddr, instance, "vkUseIOSurfaceMVK");
    const auto getIOSurface = resolveVulkanFunction<PFN_vkGetIOSurfaceMVK>(
        getInstanceProcAddr, instance, "vkGetIOSurfaceMVK");
    const auto exportMetalObjects = resolveVulkanFunction<PFN_vkVoidFunction>(
        getInstanceProcAddr, instance, "vkExportMetalObjectsEXT");

    std::cout << "PROBE vulkan_functions queue_wait_idle="
              << (queueWaitIdle != nullptr ? "true" : "false")
              << " queue_submit=" << (queueSubmit != nullptr ? "true" : "false")
              << " fence="
              << (createFence != nullptr && waitForFences != nullptr &&
                          destroyFence != nullptr
                      ? "true"
                      : "false")
              << " use_iosurface_mvk="
              << (useIOSurface != nullptr ? "true" : "false")
              << " get_iosurface_mvk="
              << (getIOSurface != nullptr ? "true" : "false")
              << " export_metal_objects="
              << (exportMetalObjects != nullptr ? "true" : "false") << '\n';

    HMODULE bridgeModule = LoadLibraryA("alvr_iosurface_bridge.dll");
    printModulePath(L"alvr_iosurface_bridge.dll");
    const auto bridgeScalar = resolveModuleFunction<BridgeScalarFunction>(
        bridgeModule, "alvr_iosurface_scalar");
    const auto bridgeAttach = resolveModuleFunction<BridgeAttachFunction>(
        bridgeModule, "alvr_iosurface_attach");
    const auto bridgeReleasePort =
        resolveModuleFunction<BridgeReleasePortFunction>(
            bridgeModule, "alvr_iosurface_release_port");
    const auto bridgeImportBind =
        resolveModuleFunction<BridgeImportBindFunction>(
            bridgeModule, "alvr_iosurface_import_bind");
    const auto bridgeSignalReady =
        resolveModuleFunction<BridgeSignalReadyFunction>(
            bridgeModule, "alvr_iosurface_signal_ready");

    constexpr uint64_t scalarInput = UINT64_C(0x1122334455667788);
    constexpr uint64_t scalarExpected = UINT64_C(0x8f154afd2a2c0b9d);
    uint64_t scalarOutput = 0;
    LONG scalarStatus = static_cast<LONG>(0xc0000139);
    if (bridgeScalar != nullptr) {
        scalarStatus = bridgeScalar(scalarInput, &scalarOutput);
    }
    const bool scalarPassed = scalarStatus == 0 &&
                              scalarOutput == scalarExpected;
    std::cout << "PROBE bridge_scalar status="
              << formatHresult(scalarStatus)
              << " input=0x" << std::hex << scalarInput
              << " output=0x" << scalarOutput
              << " expected=0x" << scalarExpected << std::dec
              << " result=" << (scalarPassed ? "pass" : "fail") << '\n';

    int32_t bridgeVkResult = VK_ERROR_EXTENSION_NOT_PRESENT;
    uint32_t surfaceId = 0;
    uint32_t surfacePort = 0;
    const bool nativeSurfaceRequested =
        !options.nativeSurfaceService.empty();
    AlvrIosurfaceBindResult nativeBindResult{};
    LONG nativeBindStatus = static_cast<LONG>(0xc0000139);
    if (nativeSurfaceRequested && scalarPassed && bridgeImportBind != nullptr) {
        nativeBindStatus = bridgeImportBind(
            reinterpret_cast<uintptr_t>(image),
            options.nativeSurfaceService.c_str(),
            options.nativeSurfaceNonce,
            &nativeBindResult);
    }
    const bool nativeSurfaceBound =
        nativeBindStatus == 0 && nativeBindResult.vkResult == VK_SUCCESS &&
        nativeBindResult.verificationStatus == ALVR_IOSURFACE_PROBE_PASS &&
        nativeBindResult.surfaceId != 0;
    if (nativeSurfaceBound) {
        surfaceId = nativeBindResult.surfaceId;
    }
    std::cout << "PROBE bridge_import_bind requested="
              << (nativeSurfaceRequested ? "true" : "false")
              << " status=" << formatHresult(nativeBindStatus)
              << " vk_result=" << nativeBindResult.vkResult
              << " verification_status="
              << nativeBindResult.verificationStatus
              << " surface_id=" << nativeBindResult.surfaceId
              << " size=" << nativeBindResult.width << 'x'
              << nativeBindResult.height
              << " bytes_per_row=" << nativeBindResult.bytesPerRow
              << " result=" << (nativeSurfaceBound ? "pass" : "fail")
              << '\n';

    LONG bridgeAttachStatus = static_cast<LONG>(0xc0000139);
    if (!nativeSurfaceRequested && scalarPassed && bridgeAttach != nullptr) {
        bridgeAttachStatus = bridgeAttach(
            reinterpret_cast<uintptr_t>(image),
            &bridgeVkResult,
            &surfaceId,
            &surfacePort);
    }
    const bool bridgeAttached = bridgeAttachStatus == 0 &&
                                bridgeVkResult == VK_SUCCESS &&
                                surfaceId != 0 && surfacePort != 0;
    std::cout << "PROBE bridge_attach status="
              << formatHresult(bridgeAttachStatus)
              << " vk_result=" << bridgeVkResult
              << " surface_id=" << surfaceId
              << " mach_port=" << surfacePort
              << " result=" << (bridgeAttached ? "pass" : "fail") << '\n';

    void *hostSurface = nullptr;
    VkResult useSurfaceResult = VK_ERROR_EXTENSION_NOT_PRESENT;
    const bool directAttachAttempted =
        !nativeSurfaceRequested && !bridgeAttached &&
        useIOSurface != nullptr && getIOSurface != nullptr;
    if (directAttachAttempted) {
        useSurfaceResult = useIOSurface(image, nullptr);
        if (useSurfaceResult == VK_SUCCESS) {
            getIOSurface(image, &hostSurface);
        }
    }
    std::cout << "PROBE direct_iosurface_attach attempted="
              << (directAttachAttempted ? "true" : "false")
              << " vk_result=" << useSurfaceResult
              << " pointer=0x" << std::hex
              << reinterpret_cast<uintptr_t>(hostSurface) << std::dec << '\n';

    const size_t pixelCount = static_cast<size_t>(options.width) * options.height;
    uint32_t sourcePixel = UINT32_C(0xffff0000);
    if (nativeSurfaceBound) {
        sourcePixel = static_cast<uint32_t>(nativeBindResult.expectedBgra[0]) |
                      (static_cast<uint32_t>(nativeBindResult.expectedBgra[1]) << 8) |
                      (static_cast<uint32_t>(nativeBindResult.expectedBgra[2]) << 16) |
                      (static_cast<uint32_t>(nativeBindResult.expectedBgra[3]) << 24);
    }
    std::vector<uint32_t> sourcePixels(pixelCount, sourcePixel);
    D3D11_SUBRESOURCE_DATA sourceData{};
    sourceData.pSysMem = sourcePixels.data();
    sourceData.SysMemPitch = options.width * sizeof(uint32_t);
    sourceData.SysMemSlicePitch = sourceData.SysMemPitch * options.height;
    D3D11_TEXTURE2D_DESC sourceDescription = handoffDescription;
    sourceDescription.Format = DXGI_FORMAT_B8G8R8A8_TYPELESS;
    sourceDescription.BindFlags =
        D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    ComPtr<ID3D11Texture2D> sourceTexture;
    result = d3dDevice->CreateTexture2D(
        &sourceDescription, &sourceData, sourceTexture.put());
    std::cout << "PROBE source_texture_create hr="
              << formatHresult(result)
              << " format=" << static_cast<unsigned>(sourceDescription.Format)
              << " bind_flags=0x" << std::hex << sourceDescription.BindFlags
              << std::dec << '\n';
    if (FAILED(result)) {
        return 1;
    }

    LARGE_INTEGER copyStart{};
    LARGE_INTEGER copyEnd{};
    QueryPerformanceCounter(&copyStart);
    d3dContext->CopyResource(handoffTexture.get(), sourceTexture.get());
    d3dContext->Flush();
    interopDevice->FlushRenderingCommands();
    QueryPerformanceCounter(&copyEnd);
    const double copyFlushMilliseconds =
        performanceMilliseconds(copyStart, copyEnd);
    std::cout << "PROBE copy_resource submitted=true flush_ms="
              << copyFlushMilliseconds << '\n';

    VkFence completionFence = VK_NULL_HANDLE;
    VkResult fenceCreateResult = VK_ERROR_EXTENSION_NOT_PRESENT;
    VkResult fenceSubmitResult = VK_ERROR_EXTENSION_NOT_PRESENT;
    VkResult waitResult = VK_ERROR_EXTENSION_NOT_PRESENT;
    double fenceSubmitMilliseconds = 0.0;
    double fenceWaitMilliseconds = 0.0;
    if (createFence != nullptr && queueSubmit != nullptr &&
        waitForFences != nullptr && destroyFence != nullptr) {
        VkFenceCreateInfo fenceInfo{};
        fenceInfo.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
        fenceCreateResult = createFence(
            device, &fenceInfo, nullptr, &completionFence);
    }
    if (fenceCreateResult == VK_SUCCESS) {
        VkSubmitInfo markerSubmit{};
        markerSubmit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
        LARGE_INTEGER submitStart{};
        LARGE_INTEGER submitEnd{};
        QueryPerformanceCounter(&submitStart);
        interopDevice->LockSubmissionQueue();
        fenceSubmitResult = queueSubmit(
            submissionQueue, 1, &markerSubmit, completionFence);
        interopDevice->ReleaseSubmissionQueue();
        QueryPerformanceCounter(&submitEnd);
        fenceSubmitMilliseconds =
            performanceMilliseconds(submitStart, submitEnd);
        if (fenceSubmitResult == VK_SUCCESS) {
            LARGE_INTEGER waitStart{};
            LARGE_INTEGER waitEnd{};
            QueryPerformanceCounter(&waitStart);
            waitResult = waitForFences(
                device, 1, &completionFence, VK_TRUE, UINT64_MAX);
            QueryPerformanceCounter(&waitEnd);
            fenceWaitMilliseconds =
                performanceMilliseconds(waitStart, waitEnd);
        }
    } else if (queueWaitIdle != nullptr) {
        interopDevice->LockSubmissionQueue();
        waitResult = queueWaitIdle(submissionQueue);
        interopDevice->ReleaseSubmissionQueue();
    }

    if (!bridgeAttached && getIOSurface != nullptr) {
        getIOSurface(image, &hostSurface);
    }
    std::cout << "PROBE synchronized_copy fence_create=" << fenceCreateResult
              << " fence_submit=" << fenceSubmitResult
              << " wait_result=" << waitResult
              << " fence_submit_ms=" << fenceSubmitMilliseconds
              << " fence_wait_ms=" << fenceWaitMilliseconds
              << " surface_id=" << surfaceId
              << " iosurface_pointer=0x" << std::hex
              << reinterpret_cast<uintptr_t>(hostSurface) << std::dec << '\n';

    const bool interopPassed = waitResult == VK_SUCCESS;
    const bool directHostPath = nativeSurfaceBound || bridgeAttached ||
                                (useSurfaceResult == VK_SUCCESS &&
                                 hostSurface != nullptr);
    bool consumerAcknowledged =
        !options.waitForNativeConsumer && !nativeSurfaceRequested;
    ULONGLONG consumerWaitMilliseconds = 0;
    if (options.waitForNativeConsumer && bridgeAttached && interopPassed) {
        DeleteFileA(kReadyFile);
        DeleteFileA(kDoneFile);
        const bool readyWritten = writeReadyFile(
            surfaceId, imageInfo.extent.width, imageInfo.extent.height);
        std::cout << "PROBE handoff_ready file=\"" << kReadyFile
                  << "\" written=" << (readyWritten ? "true" : "false")
                  << " surface_id=" << surfaceId << std::endl;
        if (readyWritten) {
            consumerAcknowledged = waitForConsumer(
                30000, &consumerWaitMilliseconds);
        }
        std::cout << "PROBE handoff_done file=\"" << kDoneFile
                  << "\" acknowledged="
                  << (consumerAcknowledged ? "true" : "false")
                  << " wait_ms=" << consumerWaitMilliseconds << '\n';
    }

    LONG nativeSignalStatus = 0;
    if (nativeSurfaceBound) {
        const uint32_t completionStatus = interopPassed
                                              ? ALVR_IOSURFACE_PROBE_PASS
                                              : ALVR_IOSURFACE_PROBE_COPY_FAILED;
        nativeSignalStatus = bridgeSignalReady != nullptr
                                 ? bridgeSignalReady(
                                       options.nativeSurfaceService.c_str(),
                                       options.nativeSurfaceNonce,
                                       &nativeBindResult,
                                       completionStatus)
                                 : static_cast<LONG>(0xc0000139);
        consumerAcknowledged = nativeSignalStatus == 0;
        std::cout << "PROBE bridge_signal_ready status="
                  << formatHresult(nativeSignalStatus)
                  << " completion_status=" << completionStatus
                  << " frame_id=" << nativeBindResult.frameId
                  << " slot=" << nativeBindResult.slotIndex
                  << " generation=" << nativeBindResult.generation
                  << " result="
                  << (consumerAcknowledged ? "pass" : "fail") << '\n';
    }

    LONG releaseStatus = 0;
    if (surfacePort != 0 && bridgeReleasePort != nullptr) {
        releaseStatus = bridgeReleasePort(surfacePort);
        std::cout << "PROBE bridge_release_port status="
                  << formatHresult(releaseStatus) << '\n';
    }
    if (completionFence != VK_NULL_HANDLE && destroyFence != nullptr) {
        destroyFence(device, completionFence, nullptr);
    }
    std::cout << "PROBE summary interop="
              << (interopPassed ? "pass" : "fail")
              << " direct_iosurface="
              << (directHostPath ? "pass" : "blocked")
              << " native_surface_bound="
              << (nativeSurfaceBound ? "true" : "false")
              << " native_bridge_required="
              << ((bridgeAttached || nativeSurfaceBound) ? "satisfied" :
                  (directHostPath ? "false" : "true"))
              << " consumer_acknowledged="
              << (consumerAcknowledged ? "true" : "false") << '\n';
    if (bridgeModule != nullptr) {
        FreeLibrary(bridgeModule);
    }
    if (vulkanModule != nullptr) {
        FreeLibrary(vulkanModule);
    }
    return interopPassed && directHostPath && consumerAcknowledged &&
                   releaseStatus == 0
               ? 0
               : 3;
}

}  // namespace dxvk_probe

int main(int argumentCount, char **arguments) {
    dxvk_probe::ProbeOptions options;
    for (int index = 1; index < argumentCount; ++index) {
        if (std::strcmp(arguments[index], "--wait-consumer") == 0) {
            options.waitForNativeConsumer = true;
        } else if (std::strcmp(arguments[index], "--width") == 0 &&
                   index + 1 < argumentCount) {
            if (!dxvk_probe::parseDimension(arguments[++index], &options.width)) {
                std::cerr << "invalid --width\n";
                return 1;
            }
        } else if (std::strcmp(arguments[index], "--height") == 0 &&
                   index + 1 < argumentCount) {
            if (!dxvk_probe::parseDimension(arguments[++index], &options.height)) {
                std::cerr << "invalid --height\n";
                return 1;
            }
        } else if (std::strcmp(arguments[index],
                               "--native-surface-service") == 0 &&
                   index + 1 < argumentCount) {
            options.nativeSurfaceService = arguments[++index];
        } else if (std::strcmp(arguments[index],
                               "--native-surface-nonce") == 0 &&
                   index + 1 < argumentCount) {
            if (!dxvk_probe::parseNonce(
                    arguments[++index], &options.nativeSurfaceNonce)) {
                std::cerr << "invalid --native-surface-nonce\n";
                return 1;
            }
        } else {
            std::cerr << "unknown or incomplete option: " << arguments[index]
                      << '\n';
            return 1;
        }
    }
    if (static_cast<uint64_t>(options.width) * options.height >
        UINT64_C(100000000)) {
        std::cerr << "requested texture is too large\n";
        return 1;
    }
    if (options.nativeSurfaceService.empty() !=
        (options.nativeSurfaceNonce == 0)) {
        std::cerr << "--native-surface-service and --native-surface-nonce "
                     "must be provided together\n";
        return 1;
    }
    if (!options.nativeSurfaceService.empty() &&
        options.waitForNativeConsumer) {
        std::cerr << "--wait-consumer cannot be combined with a native surface\n";
        return 1;
    }

    const HRESULT initializeResult = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(initializeResult) && initializeResult != RPC_E_CHANGED_MODE) {
        std::cout << "PROBE com_init hr="
                  << dxvk_probe::formatHresult(initializeResult)
                  << " result=fail\n";
        return 1;
    }

    const int result = dxvk_probe::runProbe(options);
    if (SUCCEEDED(initializeResult)) {
        CoUninitialize();
    }
    return result;
}
