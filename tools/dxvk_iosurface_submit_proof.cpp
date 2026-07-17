#include "dxvk_iosurface_submit_proof.h"

#include <d3d10.h>
#include <dxgi.h>
#include <vulkan/vulkan.h>
#include <wrl/client.h>

#include <chrono>
#include <condition_variable>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <new>
#include <thread>
#include <utility>
#include <vector>

#include "alvr_iosurface_bridge/iosurface_handoff_protocol.h"

namespace alvr_probe {
namespace {

using Microsoft::WRL::ComPtr;

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

struct DxgiVkInteropDevice;
struct DxgiVkInteropSurface;

struct DxgiVkInteropSurfaceVtable {
    HRESULT(STDMETHODCALLTYPE *queryInterface)(
        DxgiVkInteropSurface *surface, REFIID iid, void **object);
    ULONG(STDMETHODCALLTYPE *addRef)(DxgiVkInteropSurface *surface);
    ULONG(STDMETHODCALLTYPE *release)(DxgiVkInteropSurface *surface);
    HRESULT(STDMETHODCALLTYPE *getDevice)(
        DxgiVkInteropSurface *surface, DxgiVkInteropDevice **device);
    HRESULT(STDMETHODCALLTYPE *getVulkanImageInfo)(
        DxgiVkInteropSurface *surface,
        VkImage *image,
        VkImageLayout *layout,
        VkImageCreateInfo *createInfo);
};

struct DxgiVkInteropSurface {
    const DxgiVkInteropSurfaceVtable *vtable;
};

struct DxgiVkInteropDeviceVtable {
    HRESULT(STDMETHODCALLTYPE *queryInterface)(
        DxgiVkInteropDevice *device, REFIID iid, void **object);
    ULONG(STDMETHODCALLTYPE *addRef)(DxgiVkInteropDevice *device);
    ULONG(STDMETHODCALLTYPE *release)(DxgiVkInteropDevice *device);
    void(STDMETHODCALLTYPE *getVulkanHandles)(
        DxgiVkInteropDevice *device,
        VkInstance *instance,
        VkPhysicalDevice *physicalDevice,
        VkDevice *vulkanDevice);
    void(STDMETHODCALLTYPE *getSubmissionQueue)(
        DxgiVkInteropDevice *device,
        VkQueue *queue,
        uint32_t *queueFamilyIndex);
    void(STDMETHODCALLTYPE *transitionSurfaceLayout)(
        DxgiVkInteropDevice *device,
        DxgiVkInteropSurface *surface,
        const VkImageSubresourceRange *subresources,
        VkImageLayout oldLayout,
        VkImageLayout newLayout);
    void(STDMETHODCALLTYPE *flushRenderingCommands)(
        DxgiVkInteropDevice *device);
    void(STDMETHODCALLTYPE *lockSubmissionQueue)(
        DxgiVkInteropDevice *device);
    void(STDMETHODCALLTYPE *releaseSubmissionQueue)(
        DxgiVkInteropDevice *device);
};

struct DxgiVkInteropDevice {
    const DxgiVkInteropDeviceVtable *vtable;
};

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

struct AlvrIosurfaceFrame {
    uint64_t frameId;
    uint64_t videoTimestampNs;
    uint32_t slotIndex;
    uint32_t generation;
    uint32_t flags;
    uint32_t surfaceId;
    uint32_t width;
    uint32_t height;
    uint32_t sampleX;
    uint32_t sampleY;
    uint8_t expectedBgra[4];
    uint64_t poseTimestampNs;
    uint64_t poseGeneration;
    float pose[3][4];
};

struct AlvrIosurfaceReleaseResult {
    uint32_t status;
    uint32_t consumerPid;
    uint8_t actualBgra[4];
};

static_assert(sizeof(AlvrIosurfaceBindResult) == 56);
static_assert(sizeof(AlvrIosurfaceFrame) == 120);
static_assert(sizeof(AlvrIosurfaceReleaseResult) == 12);

using BridgeImportBindFunction = LONG(WINAPI *)(
    uint64_t image,
    const char *serviceName,
    uint64_t sessionNonce,
    AlvrIosurfaceBindResult *result);
using BridgeFrameReadyFunction = LONG(WINAPI *)(
    const char *serviceName,
    uint64_t sessionNonce,
    const AlvrIosurfaceFrame *frame,
    AlvrIosurfaceReleaseResult *release);

constexpr const char *kReadyFile =
    "C:\\alvr-probes\\real_submit_iosurface_ready.txt";
constexpr const char *kReadyTempFile =
    "C:\\alvr-probes\\real_submit_iosurface_ready.tmp";
constexpr const char *kDoneFile =
    "C:\\alvr-probes\\real_submit_iosurface_done.txt";
constexpr const char *kDoneTempFile =
    "C:\\alvr-probes\\real_submit_iosurface_done.tmp";
constexpr uint32_t kVerifierTimeoutMilliseconds = 30000;
constexpr uint32_t kPoolSlotCount = 3;
constexpr uint32_t kFrameFlagConsumerSample = 1u << 1;
constexpr uint32_t kFrameFlagFallbackPose = 1u << 2;
constexpr uint32_t kFrameFlagStartupBarrier = 1u << 3;
constexpr uint64_t kResizeConsumerSampleFrame = 450;
constexpr uint64_t kSeparateEyeConsumerSampleFrame = 30;
constexpr uint64_t kPoolFenceTimeoutNanoseconds = UINT64_C(5000000000);
constexpr auto kPoolAcquireTimeout = std::chrono::milliseconds(100);
constexpr auto kPoolWorkerOrderTimeout = std::chrono::seconds(40);
constexpr DWORD kPoolShutdownTimeoutMilliseconds = 40000;
const int kModuleAnchor = 0;

std::string environmentValue(const char *name) {
    const DWORD required = GetEnvironmentVariableA(name, nullptr, 0);
    if (required <= 1) {
        return {};
    }
    std::string value(required, '\0');
    const DWORD written = GetEnvironmentVariableA(
        name, value.data(), static_cast<DWORD>(value.size()));
    if (written == 0 || written >= value.size()) {
        return {};
    }
    value.resize(written);
    return value;
}

bool parseEnvironmentNonce(const std::string &value, uint64_t *nonce) {
    if (value.empty()) {
        return false;
    }
    char *end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' || parsed == 0) {
        return false;
    }
    *nonce = static_cast<uint64_t>(parsed);
    return true;
}

bool parseEnvironmentDimension(const std::string &value, uint32_t *dimension) {
    if (value.empty()) {
        return false;
    }
    char *end = nullptr;
    errno = 0;
    const unsigned long parsed = std::strtoul(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' || parsed == 0 ||
        parsed > UINT32_MAX) {
        return false;
    }
    *dimension = static_cast<uint32_t>(parsed);
    return true;
}

template <typename Function>
Function moduleFunction(HMODULE module, const char *name) {
    Function function = nullptr;
    if (module == nullptr) {
        return function;
    }
    const FARPROC rawFunction = GetProcAddress(module, name);
    static_assert(sizeof(function) == sizeof(rawFunction));
    std::memcpy(&function, &rawFunction, sizeof(function));
    return function;
}

template <typename Function>
Function vulkanFunction(PFN_vkGetInstanceProcAddr getInstanceProcAddr,
                        VkInstance instance,
                        const char *name) {
    if (getInstanceProcAddr == nullptr) {
        return nullptr;
    }
    return reinterpret_cast<Function>(getInstanceProcAddr(instance, name));
}

uint32_t elapsedMicroseconds(std::chrono::steady_clock::time_point start,
                             std::chrono::steady_clock::time_point end) {
    const auto value = std::chrono::duration_cast<std::chrono::microseconds>(
                           end - start)
                           .count();
    return value > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(value);
}

uint64_t makeProofNonce(uint64_t submitSequence) {
    LARGE_INTEGER counter{};
    QueryPerformanceCounter(&counter);
    uint64_t nonce = static_cast<uint64_t>(counter.QuadPart) ^
                     (static_cast<uint64_t>(GetCurrentProcessId()) << 32) ^
                     (static_cast<uint64_t>(GetCurrentThreadId()) << 16) ^
                     submitSequence;
    return nonce != 0 ? nonce : 1;
}

bool deleteProofFile(const char *path) {
    if (DeleteFileA(path)) {
        return true;
    }
    const DWORD error = GetLastError();
    return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND;
}

bool writeReadyFile(uint64_t proofNonce,
                    uint32_t surfaceId,
                    uint32_t width,
                    uint32_t height,
                    uint64_t submitSequence,
                    uint32_t eye,
                    const SubmitProofSample &sample,
                    uint32_t sourceFormat,
                    uint32_t sourceBindFlags,
                    uint32_t copyFlushMicroseconds,
                    uint32_t markerSubmitMicroseconds,
                    uint32_t fenceWaitMicroseconds) {
    FILE *file = std::fopen(kReadyTempFile, "wb");
    if (file == nullptr) {
        return false;
    }
    const int written = std::fprintf(
        file,
        "proof_nonce=%llu\n"
        "surface_id=%u\n"
        "width=%u\n"
        "height=%u\n"
        "submit_sequence=%llu\n"
        "eye=%u\n"
        "sample_x=%u\n"
        "sample_y=%u\n"
        "expected_bgra=%u,%u,%u,%u\n"
        "source_format=%u\n"
        "source_bind_flags=%u\n"
        "copy_flush_us=%u\n"
        "marker_submit_us=%u\n"
        "fence_wait_us=%u\n",
        static_cast<unsigned long long>(proofNonce),
        surfaceId,
        width,
        height,
        static_cast<unsigned long long>(submitSequence),
        eye,
        sample.x,
        sample.y,
        static_cast<unsigned>(sample.bgra[0]),
        static_cast<unsigned>(sample.bgra[1]),
        static_cast<unsigned>(sample.bgra[2]),
        static_cast<unsigned>(sample.bgra[3]),
        sourceFormat,
        sourceBindFlags,
        copyFlushMicroseconds,
        markerSubmitMicroseconds,
        fenceWaitMicroseconds);
    const int closeResult = std::fclose(file);
    if (written <= 0 || closeResult != 0) {
        DeleteFileA(kReadyTempFile);
        return false;
    }
    if (!MoveFileExA(kReadyTempFile,
                     kReadyFile,
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DeleteFileA(kReadyTempFile);
        return false;
    }
    return true;
}

bool readConsumerStatus(uint64_t expectedNonce,
                        uint64_t expectedSubmitSequence,
                        uint32_t expectedSurfaceId,
                        int *status) {
    FILE *file = std::fopen(kDoneFile, "rb");
    if (file == nullptr) {
        return false;
    }
    unsigned long long proofNonce = 0;
    unsigned long long submitSequence = 0;
    unsigned surfaceId = 0;
    int value = -1;
    const int matched = std::fscanf(
        file,
        "proof_nonce=%llu\n"
        "submit_sequence=%llu\n"
        "surface_id=%u\n"
        "consumer_status=%d",
        &proofNonce,
        &submitSequence,
        &surfaceId,
        &value);
    std::fclose(file);
    if (matched != 4 || proofNonce != expectedNonce ||
        submitSequence != expectedSubmitSequence ||
        surfaceId != expectedSurfaceId) {
        DeleteFileA(kDoneFile);
        return false;
    }
    *status = value;
    return true;
}

}  // namespace

struct DxvkIosurfaceSubmitProof::ProofState {
    ComPtr<ID3D11Texture2D> sourceTexture;
    ComPtr<ID3D11Texture2D> handoffTexture;
    ComPtr<IUnknown> interopSurface;
    ComPtr<IUnknown> interopDevice;
    HMODULE vulkanModule = nullptr;
    HMODULE bridgeModule = nullptr;
    PFN_vkWaitForFences waitForFences = nullptr;
    PFN_vkDestroyFence destroyFence = nullptr;
    BridgeReleasePortFunction releasePort = nullptr;
    VkDevice device = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    uint32_t surfaceId = 0;
    uint32_t machPort = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    uint64_t submitSequence = 0;
    uint32_t eye = 0;
    SubmitProofSample sample;
    uint32_t sourceFormat = 0;
    uint32_t sourceBindFlags = 0;
    uint32_t copyFlushMicroseconds = 0;
    uint32_t markerSubmitMicroseconds = 0;
    uint64_t proofNonce = 0;
};

struct DxvkIosurfaceSubmitProof::WorkerContext {
    std::shared_ptr<ProofState> state;
    SubmitProofLogFunction logFunction = nullptr;
    HMODULE moduleReference = nullptr;
};

enum class PoolTransferMode {
    DirectCopy,
    StereoLinearBlit,
    StereoNearestBlit,
    SeparateEyeBlit,
};

const char *poolTransferModeName(PoolTransferMode mode) {
    switch (mode) {
        case PoolTransferMode::DirectCopy:
            return "copy";
        case PoolTransferMode::StereoLinearBlit:
            return "stereo-linear-clamped";
        case PoolTransferMode::StereoNearestBlit:
            return "stereo-nearest";
        case PoolTransferMode::SeparateEyeBlit:
            return "separate-eye-nearest";
    }
    return "unknown";
}

struct PoolTransferSubmission {
    VkFence fence = VK_NULL_HANDLE;
    uint32_t prepareMicroseconds = 0;
    uint32_t submitMicroseconds = 0;
    PoolTransferMode mode = PoolTransferMode::DirectCopy;
    const char *failureReason = nullptr;
    VkResult failureResult = VK_SUCCESS;
};

struct DxvkIosurfaceSubmitProof::PoolState {
    enum class SlotStatus {
        Available,
        CopySubmitted,
        AwaitingRelease,
    };

    struct Slot {
        ComPtr<ID3D11Texture2D> handoffTexture;
        ComPtr<IUnknown> interopSurface;
        VkImage image = VK_NULL_HANDLE;
        VkImageLayout layout = VK_IMAGE_LAYOUT_UNDEFINED;
        VkFormat format = VK_FORMAT_UNDEFINED;
        VkImageUsageFlags usage = 0;
        VkImageTiling tiling = VK_IMAGE_TILING_OPTIMAL;
        VkCommandPool commandPool = VK_NULL_HANDLE;
        VkCommandBuffer commandBuffer = VK_NULL_HANDLE;
        uint32_t surfaceId = 0;
        uint32_t generation = 0;
        SlotStatus status = SlotStatus::Available;
    };

    ~PoolState() {
        if (device != VK_NULL_HANDLE && destroyCommandPool != nullptr) {
            for (Slot &slot : slots) {
                if (slot.commandPool != VK_NULL_HANDLE) {
                    destroyCommandPool(device, slot.commandPool, nullptr);
                    slot.commandPool = VK_NULL_HANDLE;
                    slot.commandBuffer = VK_NULL_HANDLE;
                }
            }
        }
        if (multithread && !multithreadWasProtected) {
            multithread->SetMultithreadProtected(FALSE);
        }
        if (bridgeModule != nullptr) {
            FreeLibrary(bridgeModule);
        }
        if (vulkanModule != nullptr) {
            FreeLibrary(vulkanModule);
        }
    }

    std::mutex mutex;
    std::condition_variable sendCondition;
    std::condition_variable workerCondition;
    bool failed = false;
    bool closing = false;
    bool startupReady = false;
    uint64_t nextFrameId = 1;
    uint64_t nextFrameToSend = 0;
    uint64_t submittedFrames = 0;
    uint64_t releasedFrames = 0;
    uint64_t droppedFrames = 0;
    uint64_t sourceTransitions = 0;
    uint64_t directCopyFrames = 0;
    uint64_t linearBlitFrames = 0;
    uint64_t nearestBlitFrames = 0;
    uint64_t separateEyeFrames = 0;
    uint64_t resizedFramesSinceTransition = 0;
    uint64_t resizedConsumerSamples = 0;
    uint64_t separateEyeConsumerSamples = 0;
    uint32_t activeWorkers = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t lastSourceWidth = 0;
    uint32_t lastSourceHeight = 0;
    std::string serviceName;
    uint64_t sessionNonce = 0;
    std::array<Slot, kPoolSlotCount> slots;
    ComPtr<ID3D11Device> d3dDevice;
    ComPtr<ID3D11DeviceContext> d3dContext;
    ComPtr<ID3D10Multithread> multithread;
    bool multithreadWasProtected = false;
    ComPtr<IUnknown> interopDevice;
    HMODULE vulkanModule = nullptr;
    HMODULE bridgeModule = nullptr;
    VkPhysicalDevice physicalDevice = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t queueFamilyIndex = UINT32_MAX;
    PFN_vkGetPhysicalDeviceFormatProperties getPhysicalDeviceFormatProperties =
        nullptr;
    PFN_vkCreateFence createFence = nullptr;
    PFN_vkQueueSubmit queueSubmit = nullptr;
    PFN_vkWaitForFences waitForFences = nullptr;
    PFN_vkDestroyFence destroyFence = nullptr;
    PFN_vkCreateCommandPool createCommandPool = nullptr;
    PFN_vkDestroyCommandPool destroyCommandPool = nullptr;
    PFN_vkAllocateCommandBuffers allocateCommandBuffers = nullptr;
    PFN_vkResetCommandPool resetCommandPool = nullptr;
    PFN_vkBeginCommandBuffer beginCommandBuffer = nullptr;
    PFN_vkEndCommandBuffer endCommandBuffer = nullptr;
    PFN_vkCmdBlitImage cmdBlitImage = nullptr;
    PFN_vkCmdPipelineBarrier cmdPipelineBarrier = nullptr;
    BridgeImportBindFunction importBind = nullptr;
    BridgeFrameReadyFunction frameReady = nullptr;

    bool submitTransfer(ID3D11Texture2D *sourceTexture,
                        const D3D11_TEXTURE2D_DESC &sourceDescription,
                        uint32_t slotIndex,
                        PoolTransferSubmission *submission);
    bool submitSeparateEyeTransfer(
        ID3D11Texture2D *leftTexture,
        const D3D11_TEXTURE2D_DESC &leftDescription,
        ID3D11Texture2D *rightTexture,
        const D3D11_TEXTURE2D_DESC &rightDescription,
        uint32_t slotIndex,
        PoolTransferSubmission *submission);
};

struct DxvkIosurfaceSubmitProof::PoolWorkerContext {
    ~PoolWorkerContext() {
        if (startEvent != nullptr) {
            CloseHandle(startEvent);
        }
    }

    std::shared_ptr<PoolState> state;
    uint32_t slotIndex = 0;
    uint32_t generation = 0;
    uint64_t frameId = 0;
    uint64_t videoTimestampNs = 0;
    SubmitProofPose pose;
    uint32_t eye = 0;
    SubmitProofSample sample;
    uint32_t sourceFormat = 0;
    uint32_t sourceBindFlags = 0;
    uint32_t sourceWidth = 0;
    uint32_t sourceHeight = 0;
    PoolTransferMode transferMode = PoolTransferMode::DirectCopy;
    uint32_t copyFlushMicroseconds = 0;
    uint32_t markerSubmitMicroseconds = 0;
    VkFence fence = VK_NULL_HANDLE;
    ComPtr<ID3D11Texture2D> sourceTexture;
    ComPtr<ID3D11Texture2D> rightSourceTexture;
    HANDLE startEvent = nullptr;
    bool cancelled = false;
    bool consumerSample = false;
    SubmitProofLogFunction logFunction = nullptr;
    HMODULE moduleReference = nullptr;
};

struct DxvkIosurfaceSubmitProof::PoolInitializationState {
    ~PoolInitializationState() {
        if (thread != nullptr) {
            CloseHandle(thread);
        }
    }

    std::mutex mutex;
    std::condition_variable condition;
    std::shared_ptr<PoolState> poolState;
    HANDLE thread = nullptr;
    DWORD threadId = 0;
    bool startupFinished = false;
    bool published = false;
    bool completed = false;
    bool failed = false;
};

struct DxvkIosurfaceSubmitProof::PoolInitializationContext {
    std::shared_ptr<PoolInitializationState> initialization;
    ComPtr<ID3D11Texture2D> sourceTexture;
    D3D11_TEXTURE2D_DESC sourceDescription{};
    uint32_t outputWidth = 0;
    uint32_t outputHeight = 0;
    ComPtr<ID3D10Multithread> multithread;
    bool multithreadWasProtected = false;
    std::string serviceName;
    uint64_t sessionNonce = 0;
    SubmitProofLogFunction logFunction = nullptr;
    HMODULE moduleReference = nullptr;
};

bool DxvkIosurfaceSubmitProof::PoolState::submitTransfer(
    ID3D11Texture2D *sourceTexture,
    const D3D11_TEXTURE2D_DESC &sourceDescription,
    uint32_t slotIndex,
    PoolTransferSubmission *submission) {
    if (sourceTexture == nullptr || submission == nullptr ||
        slotIndex >= slots.size()) {
        return false;
    }
    *submission = {};

    VkFence fence = VK_NULL_HANDLE;
    auto fail = [&](const char *reason, VkResult result) {
        if (fence != VK_NULL_HANDLE) {
            destroyFence(device, fence, nullptr);
            fence = VK_NULL_HANDLE;
        }
        submission->failureReason = reason;
        submission->failureResult = result;
        return false;
    };

    VkFenceCreateInfo fenceInfo{};
    fenceInfo.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    VkResult vkResult = createFence(device, &fenceInfo, nullptr, &fence);
    if (vkResult != VK_SUCCESS) {
        return fail("fence-create", vkResult);
    }

    auto *interopDeviceRaw = reinterpret_cast<DxgiVkInteropDevice *>(
        interopDevice.Get());
    const auto prepareStart = std::chrono::steady_clock::now();
    VkSubmitInfo submitInfo{};
    submitInfo.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    ComPtr<IUnknown> resizeSourceInteropHolder;
    DxgiVkInteropSurface *resizeSourceInterop = nullptr;
    VkImageLayout resizeSourceLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VkImageSubresourceRange resizeSubresourceRange = {
        VK_IMAGE_ASPECT_COLOR_BIT,
        0,
        1,
        0,
        1,
    };

    const bool sourceBgra =
        sourceDescription.Format == DXGI_FORMAT_B8G8R8A8_TYPELESS ||
        sourceDescription.Format == DXGI_FORMAT_B8G8R8A8_UNORM ||
        sourceDescription.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
    if (sourceDescription.Width == width &&
        sourceDescription.Height == height && sourceBgra) {
        submission->mode = PoolTransferMode::DirectCopy;
        d3dContext->CopyResource(
            slots[slotIndex].handoffTexture.Get(), sourceTexture);
        d3dContext->Flush();
        interopDeviceRaw->vtable->flushRenderingCommands(interopDeviceRaw);
    } else {
        if (sourceDescription.Width < 4 || sourceDescription.Height < 2 ||
            sourceDescription.Width % 2 != 0 || width < 4 || width % 2 != 0 ||
            sourceDescription.Width > INT32_MAX ||
            sourceDescription.Height > INT32_MAX || width > INT32_MAX ||
            height > INT32_MAX ||
            static_cast<uint64_t>(sourceDescription.Width) * height !=
                static_cast<uint64_t>(width) * sourceDescription.Height) {
            return fail("resize-geometry", VK_ERROR_FORMAT_NOT_SUPPORTED);
        }

        void *sourceInteropRaw = nullptr;
        HRESULT result = sourceTexture->QueryInterface(
            kIidDxgiVkInteropSurface, &sourceInteropRaw);
        if (SUCCEEDED(result) && sourceInteropRaw != nullptr) {
            resizeSourceInteropHolder.Attach(
                static_cast<IUnknown *>(sourceInteropRaw));
        }
        if (FAILED(result) || !resizeSourceInteropHolder) {
            return fail("source-interop-surface", VK_ERROR_INITIALIZATION_FAILED);
        }
        resizeSourceInterop = reinterpret_cast<DxgiVkInteropSurface *>(
            resizeSourceInteropHolder.Get());
        if (resizeSourceInterop->vtable == nullptr ||
            resizeSourceInterop->vtable->getVulkanImageInfo == nullptr) {
            return fail("source-interop-vtable", VK_ERROR_INITIALIZATION_FAILED);
        }

        VkImage sourceImage = VK_NULL_HANDLE;
        VkImageCreateInfo sourceInfo{};
        sourceInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
        result = resizeSourceInterop->vtable->getVulkanImageInfo(
            resizeSourceInterop,
            &sourceImage,
            &resizeSourceLayout,
            &sourceInfo);
        Slot &slot = slots[slotIndex];
        const bool supportedSourceFormat =
            sourceInfo.format == VK_FORMAT_R8G8B8A8_UNORM ||
            sourceInfo.format == VK_FORMAT_B8G8R8A8_UNORM;
        if (FAILED(result) || sourceImage == VK_NULL_HANDLE ||
            resizeSourceLayout == VK_IMAGE_LAYOUT_UNDEFINED ||
            slot.layout == VK_IMAGE_LAYOUT_UNDEFINED ||
            sourceInfo.imageType != VK_IMAGE_TYPE_2D ||
            sourceInfo.extent.width != sourceDescription.Width ||
            sourceInfo.extent.height != sourceDescription.Height ||
            sourceInfo.extent.depth != 1 || sourceInfo.mipLevels != 1 ||
            sourceInfo.arrayLayers != 1 ||
            sourceInfo.samples != VK_SAMPLE_COUNT_1_BIT ||
            !supportedSourceFormat ||
            slot.format != VK_FORMAT_B8G8R8A8_UNORM ||
            (sourceInfo.usage & VK_IMAGE_USAGE_TRANSFER_SRC_BIT) == 0 ||
            (slot.usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT) == 0) {
            return fail("source-vulkan-image", VK_ERROR_FORMAT_NOT_SUPPORTED);
        }

        VkFormatProperties sourceProperties{};
        VkFormatProperties destinationProperties{};
        getPhysicalDeviceFormatProperties(
            physicalDevice, sourceInfo.format, &sourceProperties);
        getPhysicalDeviceFormatProperties(
            physicalDevice, slot.format, &destinationProperties);
        const VkFormatFeatureFlags sourceFeatures =
            sourceInfo.tiling == VK_IMAGE_TILING_LINEAR
                ? sourceProperties.linearTilingFeatures
                : sourceProperties.optimalTilingFeatures;
        const VkFormatFeatureFlags destinationFeatures =
            slot.tiling == VK_IMAGE_TILING_LINEAR
                ? destinationProperties.linearTilingFeatures
                : destinationProperties.optimalTilingFeatures;
        if ((sourceFeatures & VK_FORMAT_FEATURE_BLIT_SRC_BIT) == 0 ||
            (destinationFeatures & VK_FORMAT_FEATURE_BLIT_DST_BIT) == 0) {
            return fail("resize-blit-features", VK_ERROR_FORMAT_NOT_SUPPORTED);
        }
        const bool useLinear =
            (sourceFeatures &
             VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT) != 0 &&
            sourceDescription.Width / 2 > 2 && width / 2 > 2;
        submission->mode = useLinear
                               ? PoolTransferMode::StereoLinearBlit
                               : PoolTransferMode::StereoNearestBlit;

        vkResult = resetCommandPool(device, slot.commandPool, 0);
        if (vkResult != VK_SUCCESS) {
            return fail("command-pool-reset", vkResult);
        }
        VkCommandBufferBeginInfo beginInfo{};
        beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
        beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        vkResult = beginCommandBuffer(slot.commandBuffer, &beginInfo);
        if (vkResult != VK_SUCCESS) {
            return fail("command-buffer-begin", vkResult);
        }

        const int32_t sourceWidth = static_cast<int32_t>(sourceDescription.Width);
        const int32_t sourceHeight =
            static_cast<int32_t>(sourceDescription.Height);
        const int32_t destinationWidth = static_cast<int32_t>(width);
        const int32_t destinationHeight = static_cast<int32_t>(height);
        const int32_t sourceEyeWidth = sourceWidth / 2;
        const int32_t destinationEyeWidth = destinationWidth / 2;
        auto makeRegion = [&](int32_t sourceX0,
                              int32_t sourceX1,
                              int32_t destinationX0,
                              int32_t destinationX1) {
            VkImageBlit region{};
            region.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
            region.srcSubresource.mipLevel = 0;
            region.srcSubresource.baseArrayLayer = 0;
            region.srcSubresource.layerCount = 1;
            region.srcOffsets[0] = {sourceX0, 0, 0};
            region.srcOffsets[1] = {sourceX1, sourceHeight, 1};
            region.dstSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
            region.dstSubresource.mipLevel = 0;
            region.dstSubresource.baseArrayLayer = 0;
            region.dstSubresource.layerCount = 1;
            region.dstOffsets[0] = {destinationX0, 0, 0};
            region.dstOffsets[1] = {
                destinationX1, destinationHeight, 1};
            return region;
        };

        std::array<VkImageMemoryBarrier, 2> forwardBarriers{};
        uint32_t forwardBarrierCount = 0;
        if (resizeSourceLayout != VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL) {
            VkImageMemoryBarrier &barrier =
                forwardBarriers[forwardBarrierCount++];
            barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
            barrier.srcAccessMask =
                VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
            barrier.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
            barrier.oldLayout = resizeSourceLayout;
            barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
            barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.image = sourceImage;
            barrier.subresourceRange = resizeSubresourceRange;
        }
        if (slot.layout != VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL) {
            VkImageMemoryBarrier &barrier =
                forwardBarriers[forwardBarrierCount++];
            barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
            barrier.srcAccessMask =
                VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
            barrier.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            barrier.oldLayout = slot.layout;
            barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.image = slot.image;
            barrier.subresourceRange = resizeSubresourceRange;
        }
        if (forwardBarrierCount > 0) {
            cmdPipelineBarrier(slot.commandBuffer,
                               VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                               VK_PIPELINE_STAGE_TRANSFER_BIT,
                               0,
                               0,
                               nullptr,
                               0,
                               nullptr,
                               forwardBarrierCount,
                               forwardBarriers.data());
        }

        if (useLinear) {
            const std::array<VkImageBlit, 2> interiorRegions = {
                makeRegion(0,
                           sourceEyeWidth - 1,
                           0,
                           destinationEyeWidth - 1),
                makeRegion(sourceEyeWidth + 1,
                           sourceWidth,
                           destinationEyeWidth + 1,
                           destinationWidth),
            };
            cmdBlitImage(slot.commandBuffer,
                         sourceImage,
                         VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                         slot.image,
                         VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                         static_cast<uint32_t>(interiorRegions.size()),
                         interiorRegions.data(),
                         VK_FILTER_LINEAR);
            const std::array<VkImageBlit, 2> seamRegions = {
                makeRegion(sourceEyeWidth - 1,
                           sourceEyeWidth,
                           destinationEyeWidth - 1,
                           destinationEyeWidth),
                makeRegion(sourceEyeWidth,
                           sourceEyeWidth + 1,
                           destinationEyeWidth,
                           destinationEyeWidth + 1),
            };
            cmdBlitImage(slot.commandBuffer,
                         sourceImage,
                         VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                         slot.image,
                         VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                         static_cast<uint32_t>(seamRegions.size()),
                         seamRegions.data(),
                         VK_FILTER_NEAREST);
        } else {
            const std::array<VkImageBlit, 2> eyeRegions = {
                makeRegion(0, sourceEyeWidth, 0, destinationEyeWidth),
                makeRegion(sourceEyeWidth,
                           sourceWidth,
                           destinationEyeWidth,
                           destinationWidth),
            };
            cmdBlitImage(slot.commandBuffer,
                         sourceImage,
                         VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                         slot.image,
                         VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                         static_cast<uint32_t>(eyeRegions.size()),
                         eyeRegions.data(),
                         VK_FILTER_NEAREST);
        }

        std::array<VkImageMemoryBarrier, 2> restoreBarriers{};
        uint32_t restoreBarrierCount = 0;
        if (resizeSourceLayout != VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL) {
            VkImageMemoryBarrier &barrier =
                restoreBarriers[restoreBarrierCount++];
            barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
            barrier.srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
            barrier.dstAccessMask =
                VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
            barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
            barrier.newLayout = resizeSourceLayout;
            barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.image = sourceImage;
            barrier.subresourceRange = resizeSubresourceRange;
        }
        if (slot.layout != VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL) {
            VkImageMemoryBarrier &barrier =
                restoreBarriers[restoreBarrierCount++];
            barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
            barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            barrier.dstAccessMask =
                VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
            barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            barrier.newLayout = slot.layout;
            barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.image = slot.image;
            barrier.subresourceRange = resizeSubresourceRange;
        }
        if (restoreBarrierCount > 0) {
            cmdPipelineBarrier(slot.commandBuffer,
                               VK_PIPELINE_STAGE_TRANSFER_BIT,
                               VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                               0,
                               0,
                               nullptr,
                               0,
                               nullptr,
                               restoreBarrierCount,
                               restoreBarriers.data());
        }

        vkResult = endCommandBuffer(slot.commandBuffer);
        if (vkResult != VK_SUCCESS) {
            return fail("command-buffer-end", vkResult);
        }

        interopDeviceRaw->vtable->flushRenderingCommands(interopDeviceRaw);
        submitInfo.commandBufferCount = 1;
        submitInfo.pCommandBuffers = &slot.commandBuffer;
    }

    const auto prepareDone = std::chrono::steady_clock::now();
    submission->prepareMicroseconds = elapsedMicroseconds(
        prepareStart, prepareDone);
    const auto submitStart = std::chrono::steady_clock::now();
    interopDeviceRaw->vtable->lockSubmissionQueue(interopDeviceRaw);
    vkResult = queueSubmit(queue, 1, &submitInfo, fence);
    interopDeviceRaw->vtable->releaseSubmissionQueue(interopDeviceRaw);
    if (vkResult != VK_SUCCESS) {
        return fail("queue-submit", vkResult);
    }

    const auto submitDone = std::chrono::steady_clock::now();
    submission->submitMicroseconds = elapsedMicroseconds(
        submitStart, submitDone);

    submission->fence = fence;
    return true;
}

bool DxvkIosurfaceSubmitProof::PoolState::submitSeparateEyeTransfer(
    ID3D11Texture2D *leftTexture,
    const D3D11_TEXTURE2D_DESC &leftDescription,
    ID3D11Texture2D *rightTexture,
    const D3D11_TEXTURE2D_DESC &rightDescription,
    uint32_t slotIndex,
    PoolTransferSubmission *submission) {
    if (leftTexture == nullptr || rightTexture == nullptr ||
        submission == nullptr || slotIndex >= slots.size()) {
        return false;
    }
    *submission = {};

    VkFence fence = VK_NULL_HANDLE;
    auto fail = [&](const char *reason, VkResult result) {
        if (fence != VK_NULL_HANDLE) {
            destroyFence(device, fence, nullptr);
            fence = VK_NULL_HANDLE;
        }
        submission->failureReason = reason;
        submission->failureResult = result;
        return false;
    };

    if (leftDescription.Width != rightDescription.Width ||
        leftDescription.Height != rightDescription.Height ||
        leftDescription.Format != rightDescription.Format ||
        leftDescription.SampleDesc.Count != 1 ||
        rightDescription.SampleDesc.Count != 1 ||
        leftDescription.ArraySize != 1 || rightDescription.ArraySize != 1 ||
        leftDescription.MipLevels != 1 || rightDescription.MipLevels != 1 ||
        leftDescription.Width > INT32_MAX ||
        leftDescription.Height > INT32_MAX || width > INT32_MAX ||
        height > INT32_MAX ||
        static_cast<uint64_t>(leftDescription.Width) * 2 != width ||
        leftDescription.Height != height) {
        return fail("separate-eye-geometry", VK_ERROR_FORMAT_NOT_SUPPORTED);
    }

    VkFenceCreateInfo fenceInfo{};
    fenceInfo.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    VkResult vkResult = createFence(device, &fenceInfo, nullptr, &fence);
    if (vkResult != VK_SUCCESS) {
        return fail("fence-create", vkResult);
    }

    struct SourceImage {
        ComPtr<IUnknown> interopHolder;
        VkImage image = VK_NULL_HANDLE;
        VkImageLayout layout = VK_IMAGE_LAYOUT_UNDEFINED;
        VkImageCreateInfo info{};
    };
    auto querySourceImage = [&](ID3D11Texture2D *texture,
                                const D3D11_TEXTURE2D_DESC &description,
                                SourceImage *source) {
        void *interopRaw = nullptr;
        HRESULT result = texture->QueryInterface(
            kIidDxgiVkInteropSurface, &interopRaw);
        if (SUCCEEDED(result) && interopRaw != nullptr) {
            source->interopHolder.Attach(static_cast<IUnknown *>(interopRaw));
        }
        if (FAILED(result) || !source->interopHolder) {
            return false;
        }
        auto *interop = reinterpret_cast<DxgiVkInteropSurface *>(
            source->interopHolder.Get());
        if (interop->vtable == nullptr ||
            interop->vtable->getVulkanImageInfo == nullptr) {
            return false;
        }
        source->info.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
        result = interop->vtable->getVulkanImageInfo(
            interop,
            &source->image,
            &source->layout,
            &source->info);
        return SUCCEEDED(result) && source->image != VK_NULL_HANDLE &&
               source->layout != VK_IMAGE_LAYOUT_UNDEFINED &&
               source->info.imageType == VK_IMAGE_TYPE_2D &&
               source->info.extent.width == description.Width &&
               source->info.extent.height == description.Height &&
               source->info.extent.depth == 1 &&
               source->info.mipLevels == 1 &&
               source->info.arrayLayers == 1 &&
               source->info.samples == VK_SAMPLE_COUNT_1_BIT &&
               (source->info.usage & VK_IMAGE_USAGE_TRANSFER_SRC_BIT) != 0;
    };

    SourceImage leftSource;
    SourceImage rightSource;
    if (!querySourceImage(leftTexture, leftDescription, &leftSource) ||
        !querySourceImage(rightTexture, rightDescription, &rightSource)) {
        return fail("separate-eye-source-image", VK_ERROR_FORMAT_NOT_SUPPORTED);
    }
    if (leftSource.image == rightSource.image ||
        leftSource.info.format != rightSource.info.format) {
        return fail("separate-eye-source-alias", VK_ERROR_FORMAT_NOT_SUPPORTED);
    }

    const auto supportedSourceFormat = [](VkFormat format) {
        return format == VK_FORMAT_R8G8B8A8_UNORM ||
               format == VK_FORMAT_B8G8R8A8_UNORM;
    };
    Slot &slot = slots[slotIndex];
    if (!supportedSourceFormat(leftSource.info.format) ||
        slot.layout == VK_IMAGE_LAYOUT_UNDEFINED ||
        slot.format != VK_FORMAT_B8G8R8A8_UNORM ||
        (slot.usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT) == 0) {
        return fail("separate-eye-format", VK_ERROR_FORMAT_NOT_SUPPORTED);
    }

    VkFormatProperties sourceProperties{};
    VkFormatProperties destinationProperties{};
    getPhysicalDeviceFormatProperties(
        physicalDevice, leftSource.info.format, &sourceProperties);
    getPhysicalDeviceFormatProperties(
        physicalDevice, slot.format, &destinationProperties);
    const VkFormatFeatureFlags sourceFeatures =
        leftSource.info.tiling == VK_IMAGE_TILING_LINEAR
            ? sourceProperties.linearTilingFeatures
            : sourceProperties.optimalTilingFeatures;
    const VkFormatFeatureFlags destinationFeatures =
        slot.tiling == VK_IMAGE_TILING_LINEAR
            ? destinationProperties.linearTilingFeatures
            : destinationProperties.optimalTilingFeatures;
    if ((sourceFeatures & VK_FORMAT_FEATURE_BLIT_SRC_BIT) == 0 ||
        (destinationFeatures & VK_FORMAT_FEATURE_BLIT_DST_BIT) == 0) {
        return fail(
            "separate-eye-blit-features", VK_ERROR_FORMAT_NOT_SUPPORTED);
    }

    vkResult = resetCommandPool(device, slot.commandPool, 0);
    if (vkResult != VK_SUCCESS) {
        return fail("command-pool-reset", vkResult);
    }
    VkCommandBufferBeginInfo beginInfo{};
    beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
    beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    vkResult = beginCommandBuffer(slot.commandBuffer, &beginInfo);
    if (vkResult != VK_SUCCESS) {
        return fail("command-buffer-begin", vkResult);
    }

    const VkImageSubresourceRange subresourceRange = {
        VK_IMAGE_ASPECT_COLOR_BIT,
        0,
        1,
        0,
        1,
    };
    std::array<VkImageMemoryBarrier, 3> forwardBarriers{};
    const std::array<SourceImage *, 2> sources = {
        &leftSource,
        &rightSource,
    };
    for (size_t index = 0; index < sources.size(); ++index) {
        VkImageMemoryBarrier &barrier = forwardBarriers[index];
        barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
        barrier.srcAccessMask =
            VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
        barrier.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        barrier.oldLayout = sources[index]->layout;
        barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
        barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        barrier.image = sources[index]->image;
        barrier.subresourceRange = subresourceRange;
    }
    VkImageMemoryBarrier &destinationForward = forwardBarriers[2];
    destinationForward.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
    destinationForward.srcAccessMask =
        VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
    destinationForward.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    destinationForward.oldLayout = slot.layout;
    destinationForward.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    destinationForward.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    destinationForward.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    destinationForward.image = slot.image;
    destinationForward.subresourceRange = subresourceRange;
    cmdPipelineBarrier(slot.commandBuffer,
                       VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                       VK_PIPELINE_STAGE_TRANSFER_BIT,
                       0,
                       0,
                       nullptr,
                       0,
                       nullptr,
                       static_cast<uint32_t>(forwardBarriers.size()),
                       forwardBarriers.data());

    auto makeRegion = [&](int32_t destinationX0, int32_t destinationX1) {
        VkImageBlit region{};
        region.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
        region.srcSubresource.mipLevel = 0;
        region.srcSubresource.baseArrayLayer = 0;
        region.srcSubresource.layerCount = 1;
        region.srcOffsets[0] = {0, 0, 0};
        region.srcOffsets[1] = {
            static_cast<int32_t>(leftDescription.Width),
            static_cast<int32_t>(leftDescription.Height),
            1,
        };
        region.dstSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
        region.dstSubresource.mipLevel = 0;
        region.dstSubresource.baseArrayLayer = 0;
        region.dstSubresource.layerCount = 1;
        region.dstOffsets[0] = {destinationX0, 0, 0};
        region.dstOffsets[1] = {
            destinationX1,
            static_cast<int32_t>(height),
            1,
        };
        return region;
    };
    const int32_t destinationEyeWidth = static_cast<int32_t>(width / 2);
    const VkImageBlit leftRegion = makeRegion(0, destinationEyeWidth);
    const VkImageBlit rightRegion = makeRegion(
        destinationEyeWidth, static_cast<int32_t>(width));
    cmdBlitImage(slot.commandBuffer,
                 leftSource.image,
                 VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                 slot.image,
                 VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                 1,
                 &leftRegion,
                 VK_FILTER_NEAREST);
    cmdBlitImage(slot.commandBuffer,
                 rightSource.image,
                 VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                 slot.image,
                 VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                 1,
                 &rightRegion,
                 VK_FILTER_NEAREST);

    std::array<VkImageMemoryBarrier, 3> restoreBarriers{};
    for (size_t index = 0; index < sources.size(); ++index) {
        VkImageMemoryBarrier &barrier = restoreBarriers[index];
        barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
        barrier.srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        barrier.dstAccessMask =
            VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
        barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
        barrier.newLayout = sources[index]->layout;
        barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        barrier.image = sources[index]->image;
        barrier.subresourceRange = subresourceRange;
    }
    VkImageMemoryBarrier &destinationRestore = restoreBarriers[2];
    destinationRestore.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
    destinationRestore.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    destinationRestore.dstAccessMask =
        VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
    destinationRestore.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    destinationRestore.newLayout = slot.layout;
    destinationRestore.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    destinationRestore.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    destinationRestore.image = slot.image;
    destinationRestore.subresourceRange = subresourceRange;
    cmdPipelineBarrier(slot.commandBuffer,
                       VK_PIPELINE_STAGE_TRANSFER_BIT,
                       VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                       0,
                       0,
                       nullptr,
                       0,
                       nullptr,
                       static_cast<uint32_t>(restoreBarriers.size()),
                       restoreBarriers.data());

    vkResult = endCommandBuffer(slot.commandBuffer);
    if (vkResult != VK_SUCCESS) {
        return fail("command-buffer-end", vkResult);
    }

    auto *interopDeviceRaw = reinterpret_cast<DxgiVkInteropDevice *>(
        interopDevice.Get());
    const auto prepareStart = std::chrono::steady_clock::now();
    interopDeviceRaw->vtable->flushRenderingCommands(interopDeviceRaw);
    const auto prepareDone = std::chrono::steady_clock::now();
    submission->prepareMicroseconds = elapsedMicroseconds(
        prepareStart, prepareDone);

    VkSubmitInfo submitInfo{};
    submitInfo.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    submitInfo.commandBufferCount = 1;
    submitInfo.pCommandBuffers = &slot.commandBuffer;
    const auto submitStart = std::chrono::steady_clock::now();
    interopDeviceRaw->vtable->lockSubmissionQueue(interopDeviceRaw);
    vkResult = queueSubmit(queue, 1, &submitInfo, fence);
    interopDeviceRaw->vtable->releaseSubmissionQueue(interopDeviceRaw);
    if (vkResult != VK_SUCCESS) {
        return fail("queue-submit", vkResult);
    }

    const auto submitDone = std::chrono::steady_clock::now();
    submission->submitMicroseconds = elapsedMicroseconds(
        submitStart, submitDone);
    submission->mode = PoolTransferMode::SeparateEyeBlit;
    submission->fence = fence;
    return true;
}

DxvkIosurfaceSubmitProof::DxvkIosurfaceSubmitProof(
    SubmitProofLogFunction logFunction)
    : logFunction_(logFunction) {
    poolService_ = environmentValue("ALVR_IOSURFACE_POOL_SERVICE");
    const std::string nonceValue = environmentValue(
        "ALVR_IOSURFACE_POOL_NONCE");
    const std::string widthValue = environmentValue(
        "ALVR_IOSURFACE_SOURCE_WIDTH");
    const std::string heightValue = environmentValue(
        "ALVR_IOSURFACE_SOURCE_HEIGHT");
    const bool nonceValid = parseEnvironmentNonce(nonceValue, &poolNonce_);
    const bool dimensionsAbsent = widthValue.empty() && heightValue.empty();
    const bool dimensionsValid =
        parseEnvironmentDimension(widthValue, &poolWidth_) &&
        parseEnvironmentDimension(heightValue, &poolHeight_) &&
        poolWidth_ % 4 == 0 && poolHeight_ % 2 == 0;
    poolConfigured_ = !poolService_.empty() && nonceValid &&
                      (dimensionsAbsent || dimensionsValid);
    logFunction_(
        "iosurface pool configuration service=%u nonce=%u width=%u "
        "height=%u configured=%u",
        poolService_.empty() ? 0 : 1,
        nonceValid ? 1 : 0,
        dimensionsValid ? poolWidth_ : 0,
        dimensionsValid ? poolHeight_ : 0,
        poolConfigured_ ? 1 : 0);
    if ((!poolService_.empty() || !nonceValue.empty() || !dimensionsAbsent) &&
        !poolConfigured_) {
        logFunction_(
            "iosurface pool disabled reason=invalid-environment service=%u "
            "nonce=%u width=%u height=%u",
            poolService_.empty() ? 0 : 1,
            nonceValid ? 1 : 0,
            dimensionsValid ? poolWidth_ : 0,
            dimensionsValid ? poolHeight_ : 0);
    }
}

DxvkIosurfaceSubmitProof::~DxvkIosurfaceSubmitProof() {
    shutdown();
}

void DxvkIosurfaceSubmitProof::collectPoolInitialization() {
    std::shared_ptr<PoolInitializationState> initialization;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (shutdownInProgress_ || shutdownComplete_) {
            return;
        }
        initialization = poolInitialization_;
    }
    if (!initialization) {
        return;
    }

    std::shared_ptr<PoolState> state;
    bool completed = false;
    bool failed = false;
    {
        std::lock_guard<std::mutex> lock(initialization->mutex);
        if (!initialization->published) {
            return;
        }
        state = initialization->poolState;
        failed = initialization->failed;
        completed = initialization->completed;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (poolInitialization_ != initialization || shutdownInProgress_ ||
        shutdownComplete_) {
        return;
    }
    if (!poolState_ && state) {
        poolState_ = state;
    }
    if (completed) {
        poolInitializationFailed_ = failed;
        poolInitialization_.reset();
    }
}

bool DxvkIosurfaceSubmitProof::pending() {
    collectPoolInitialization();
    std::shared_ptr<PoolState> state;
    bool initializationFailed = false;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!poolConfigured_) {
            return !attempted_;
        }
        state = poolState_;
        initializationFailed = poolInitializationFailed_;
    }
    if (!state) {
        return !initializationFailed;
    }
    std::lock_guard<std::mutex> lock(state->mutex);
    return !state->failed && !state->closing;
}

bool DxvkIosurfaceSubmitProof::usesPool() {
    std::lock_guard<std::mutex> lock(mutex_);
    return poolConfigured_;
}

void DxvkIosurfaceSubmitProof::shutdown() {
    std::shared_ptr<PoolInitializationState> initialization;
    {
        std::unique_lock<std::mutex> lock(mutex_);
        while (shutdownInProgress_) {
            poolInitializationCondition_.wait(
                lock, [&]() { return !shutdownInProgress_; });
        }
        if (shutdownComplete_) {
            return;
        }
        if (poolConfigured_ && !poolState_ && !poolInitialization_ &&
            !poolInitializationFailed_) {
            logFunction_(
                "iosurface pool shutdown deferred reason=unused-runtime-probe");
            return;
        }
        shutdownInProgress_ = true;
        poolConfigured_ = false;
        initialization = std::move(poolInitialization_);
    }

    std::shared_ptr<PoolState> initializedState;
    bool initializationFailed = false;
    auto deferShutdown = [&]() {
        std::lock_guard<std::mutex> lock(mutex_);
        poolInitialization_ = initialization;
        shutdownInProgress_ = false;
        shutdownComplete_ = false;
        poolInitializationCondition_.notify_all();
    };
    if (initialization) {
        HANDLE initializationThread = nullptr;
        DWORD initializationThreadId = 0;
        bool startupFinished = false;
        {
            std::unique_lock<std::mutex> lock(initialization->mutex);
            startupFinished = initialization->condition.wait_for(
                lock,
                std::chrono::milliseconds(kPoolShutdownTimeoutMilliseconds),
                [&]() { return initialization->startupFinished; });
            if (startupFinished) {
                initializationThread = initialization->thread;
                initializationThreadId = initialization->threadId;
            }
        }

        if (!startupFinished) {
            logFunction_(
                "iosurface pool shutdown initialization_startup=timeout");
            deferShutdown();
            return;
        } else if (initializationThread != nullptr) {
            DWORD waitResult = WAIT_TIMEOUT;
            if (GetCurrentThreadId() != initializationThreadId) {
                waitResult = WaitForSingleObject(
                    initializationThread, kPoolShutdownTimeoutMilliseconds);
            }
            if (waitResult != WAIT_OBJECT_0) {
                logFunction_(
                    "iosurface pool shutdown initialization_wait=%lu",
                    static_cast<unsigned long>(waitResult));
                deferShutdown();
                return;
            }
            {
                std::lock_guard<std::mutex> lock(initialization->mutex);
                if (initialization->thread == initializationThread) {
                    CloseHandle(initializationThread);
                    initialization->thread = nullptr;
                    initialization->threadId = 0;
                }
            }
        }

        std::lock_guard<std::mutex> lock(initialization->mutex);
        if (initialization->completed) {
            initializedState = initialization->poolState;
            initializationFailed = initialization->failed;
        }
    }

    if (initializedState || initializationFailed) {
        std::lock_guard<std::mutex> lock(mutex_);
        poolState_ = std::move(initializedState);
        poolInitializationFailed_ = initializationFailed;
    }

    auto finishShutdown = [&](bool complete) {
        std::lock_guard<std::mutex> lock(mutex_);
        poolInitializationFailed_ = false;
        shutdownInProgress_ = false;
        shutdownComplete_ = complete;
        poolInitializationCondition_.notify_all();
    };

    std::shared_ptr<PoolState> state;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        state = poolState_;
    }
    if (!state) {
        finishShutdown(true);
        return;
    }

    bool drained = false;
    {
        std::unique_lock<std::mutex> lock(state->mutex);
        state->closing = true;
        state->sendCondition.notify_all();
        drained = state->workerCondition.wait_for(
            lock,
            std::chrono::milliseconds(kPoolShutdownTimeoutMilliseconds),
            [&]() { return state->activeWorkers == 0; });
    }
    if (!drained) {
        logFunction_("iosurface pool shutdown worker_drain=timeout");
        finishShutdown(false);
        return;
    }

    {
        std::lock_guard<std::mutex> lock(state->mutex);
        logFunction_(
            "iosurface pool summary submitted=%llu released=%llu dropped=%llu "
            "source_transitions=%llu copy_frames=%llu linear_blit_frames=%llu "
            "nearest_blit_frames=%llu separate_eye_frames=%llu "
            "resized_consumer_samples=%llu separate_eye_consumer_samples=%llu "
            "last_source=%ux%u output=%ux%u",
            static_cast<unsigned long long>(state->submittedFrames),
            static_cast<unsigned long long>(state->releasedFrames),
            static_cast<unsigned long long>(state->droppedFrames),
            static_cast<unsigned long long>(state->sourceTransitions),
            static_cast<unsigned long long>(state->directCopyFrames),
            static_cast<unsigned long long>(state->linearBlitFrames),
            static_cast<unsigned long long>(state->nearestBlitFrames),
            static_cast<unsigned long long>(state->separateEyeFrames),
            static_cast<unsigned long long>(state->resizedConsumerSamples),
            static_cast<unsigned long long>(
                state->separateEyeConsumerSamples),
            state->lastSourceWidth,
            state->lastSourceHeight,
            state->width,
            state->height);
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (poolState_ == state) {
            poolState_.reset();
        }
        poolInitializationFailed_ = false;
        shutdownInProgress_ = false;
        shutdownComplete_ = true;
        poolInitializationCondition_.notify_all();
    }
    logFunction_("iosurface pool shutdown result=drained");
}

void DxvkIosurfaceSubmitProof::capturePoolFrame(
    ID3D11Texture2D *sourceTexture,
    uint64_t submitSequence,
    uint32_t eye,
    uint64_t videoTimestampNs,
    const SubmitProofPose &pose) {
    if (!usesPool()) {
        return;
    }
    capturePoolSources(
        sourceTexture,
        nullptr,
        submitSequence,
        eye,
        videoTimestampNs,
        pose);
}

void DxvkIosurfaceSubmitProof::capturePoolFramePair(
    ID3D11Texture2D *leftTexture,
    ID3D11Texture2D *rightTexture,
    uint64_t submitSequence,
    uint64_t videoTimestampNs,
    const SubmitProofPose &pose) {
    if (!usesPool()) {
        return;
    }
    capturePoolSources(
        leftTexture,
        rightTexture,
        submitSequence,
        0,
        videoTimestampNs,
        pose);
}

void DxvkIosurfaceSubmitProof::captureOnce(
    ID3D11Texture2D *sourceTexture,
    uint64_t submitSequence,
    uint32_t eye,
    const SubmitProofSample &sample,
    uint64_t videoTimestampNs) {
    if (poolConfigured_) {
        return;
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (attempted_) {
            return;
        }
        attempted_ = true;
    }

    if (sourceTexture == nullptr) {
        logFunction_("iosurface proof failed reason=null-source");
        return;
    }

    D3D11_TEXTURE2D_DESC sourceDescription{};
    sourceTexture->GetDesc(&sourceDescription);
    if (sourceDescription.SampleDesc.Count != 1 ||
        sourceDescription.ArraySize != 1 ||
        sourceDescription.MipLevels != 1 ||
        (sourceDescription.Format != DXGI_FORMAT_B8G8R8A8_TYPELESS &&
         sourceDescription.Format != DXGI_FORMAT_B8G8R8A8_UNORM &&
         sourceDescription.Format != DXGI_FORMAT_B8G8R8A8_UNORM_SRGB)) {
        logFunction_(
            "iosurface proof failed reason=unsupported-source size=%ux%u "
            "format=%u samples=%u array=%u mips=%u",
            sourceDescription.Width,
            sourceDescription.Height,
            sourceDescription.Format,
            sourceDescription.SampleDesc.Count,
            sourceDescription.ArraySize,
            sourceDescription.MipLevels);
        return;
    }
    if (sample.x >= sourceDescription.Width ||
        sample.y >= sourceDescription.Height) {
        logFunction_("iosurface proof failed reason=sample-out-of-range x=%u y=%u",
                     sample.x,
                     sample.y);
        return;
    }

    logFunction_(
        "iosurface proof stage=begin sequence=%llu eye=%u source=%p "
        "size=%ux%u format=%u bind=0x%x sample=%u,%u",
        static_cast<unsigned long long>(submitSequence),
        eye,
        sourceTexture,
        sourceDescription.Width,
        sourceDescription.Height,
        sourceDescription.Format,
        sourceDescription.BindFlags,
        sample.x,
        sample.y);

    auto state = std::make_shared<ProofState>();
    state->sourceTexture = sourceTexture;
    state->width = sourceDescription.Width;
    state->height = sourceDescription.Height;
    state->submitSequence = submitSequence;
    state->eye = eye;
    state->sample = sample;
    state->sourceFormat = static_cast<uint32_t>(sourceDescription.Format);
    state->sourceBindFlags = sourceDescription.BindFlags;
    state->proofNonce = makeProofNonce(submitSequence);

    ComPtr<ID3D11Device> d3dDevice;
    sourceTexture->GetDevice(&d3dDevice);
    logFunction_("iosurface proof stage=source-device device=%p",
                 d3dDevice.Get());
    if (!d3dDevice) {
        logFunction_("iosurface proof failed reason=missing-d3d-device");
        return;
    }

    D3D11_TEXTURE2D_DESC handoffDescription{};
    handoffDescription.Width = sourceDescription.Width;
    handoffDescription.Height = sourceDescription.Height;
    handoffDescription.MipLevels = 1;
    handoffDescription.ArraySize = 1;
    handoffDescription.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    handoffDescription.SampleDesc.Count = 1;
    handoffDescription.Usage = D3D11_USAGE_DEFAULT;

    logFunction_("iosurface proof stage=handoff-create-begin");
    HRESULT result = d3dDevice->CreateTexture2D(
        &handoffDescription, nullptr, state->handoffTexture.GetAddressOf());
    logFunction_(
        "iosurface proof stage=handoff-create-end hr=0x%08lx texture=%p",
        static_cast<unsigned long>(result),
        state->handoffTexture.Get());
    if (FAILED(result)) {
        logFunction_("iosurface proof failed reason=handoff-create hr=0x%08lx",
                     static_cast<unsigned long>(result));
        return;
    }

    void *interopSurfaceRaw = nullptr;
    logFunction_("iosurface proof stage=interop-surface-query-begin");
    result = state->handoffTexture->QueryInterface(
        kIidDxgiVkInteropSurface, &interopSurfaceRaw);
    if (SUCCEEDED(result) && interopSurfaceRaw != nullptr) {
        state->interopSurface.Attach(
            static_cast<IUnknown *>(interopSurfaceRaw));
    }
    logFunction_(
        "iosurface proof stage=interop-surface-query-end hr=0x%08lx "
        "surface=%p",
        static_cast<unsigned long>(result),
        state->interopSurface.Get());
    if (FAILED(result) || !state->interopSurface) {
        logFunction_("iosurface proof failed reason=interop-surface hr=0x%08lx",
                     static_cast<unsigned long>(result));
        return;
    }

    auto *interopSurface = reinterpret_cast<DxgiVkInteropSurface *>(
        state->interopSurface.Get());
    if (interopSurface->vtable == nullptr ||
        interopSurface->vtable->getDevice == nullptr ||
        interopSurface->vtable->getVulkanImageInfo == nullptr) {
        logFunction_("iosurface proof failed reason=interop-surface-vtable");
        return;
    }

    void *interopDeviceRaw = nullptr;
    logFunction_("iosurface proof stage=interop-device-query-begin");
    result = d3dDevice->QueryInterface(
        kIidDxgiVkInteropDevice, &interopDeviceRaw);
    if (SUCCEEDED(result) && interopDeviceRaw != nullptr) {
        state->interopDevice.Attach(static_cast<IUnknown *>(interopDeviceRaw));
    }
    logFunction_(
        "iosurface proof stage=interop-device-query-end hr=0x%08lx device=%p",
        static_cast<unsigned long>(result),
        state->interopDevice.Get());
    if (FAILED(result) || !state->interopDevice) {
        DxgiVkInteropDevice *fallbackDevice = nullptr;
        logFunction_("iosurface proof stage=interop-device-fallback-begin");
        result = interopSurface->vtable->getDevice(
            interopSurface, &fallbackDevice);
        if (SUCCEEDED(result) && fallbackDevice != nullptr) {
            state->interopDevice.Attach(
                reinterpret_cast<IUnknown *>(fallbackDevice));
        }
        logFunction_(
            "iosurface proof stage=interop-device-fallback-end hr=0x%08lx "
            "device=%p",
            static_cast<unsigned long>(result),
            state->interopDevice.Get());
    }
    if (FAILED(result) || !state->interopDevice) {
        logFunction_("iosurface proof failed reason=interop-device hr=0x%08lx",
                     static_cast<unsigned long>(result));
        return;
    }

    auto *interopDevice = reinterpret_cast<DxgiVkInteropDevice *>(
        state->interopDevice.Get());
    if (interopDevice->vtable == nullptr ||
        interopDevice->vtable->getVulkanHandles == nullptr ||
        interopDevice->vtable->getSubmissionQueue == nullptr ||
        interopDevice->vtable->flushRenderingCommands == nullptr ||
        interopDevice->vtable->lockSubmissionQueue == nullptr ||
        interopDevice->vtable->releaseSubmissionQueue == nullptr) {
        logFunction_("iosurface proof failed reason=interop-device-vtable");
        return;
    }

    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physicalDevice = VK_NULL_HANDLE;
    logFunction_("iosurface proof stage=vulkan-handles-begin");
    interopDevice->vtable->getVulkanHandles(
        interopDevice, &instance, &physicalDevice, &state->device);
    logFunction_(
        "iosurface proof stage=vulkan-handles-end instance=%p physical=%p "
        "device=%p",
        instance,
        physicalDevice,
        state->device);
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t queueFamilyIndex = UINT32_MAX;
    interopDevice->vtable->getSubmissionQueue(
        interopDevice, &queue, &queueFamilyIndex);
    logFunction_(
        "iosurface proof stage=vulkan-queue queue=%p family=%u",
        queue,
        queueFamilyIndex);

    VkImage image = VK_NULL_HANDLE;
    VkImageLayout layout = VK_IMAGE_LAYOUT_UNDEFINED;
    VkImageCreateInfo imageInfo{};
    imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
    logFunction_("iosurface proof stage=vulkan-image-info-begin");
    result = interopSurface->vtable->getVulkanImageInfo(
        interopSurface, &image, &layout, &imageInfo);
    logFunction_(
        "iosurface proof stage=vulkan-image-info-end hr=0x%08lx image=%p "
        "format=%d usage=0x%x layout=%d",
        static_cast<unsigned long>(result),
        image,
        static_cast<int>(imageInfo.format),
        imageInfo.usage,
        static_cast<int>(layout));
    if (FAILED(result) || instance == VK_NULL_HANDLE ||
        state->device == VK_NULL_HANDLE || queue == VK_NULL_HANDLE ||
        image == VK_NULL_HANDLE ||
        imageInfo.format != VK_FORMAT_B8G8R8A8_UNORM ||
        (imageInfo.usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT) == 0) {
        logFunction_(
            "iosurface proof failed reason=vulkan-image hr=0x%08lx "
            "format=%d usage=0x%x layout=%d queue_family=%u",
            static_cast<unsigned long>(result),
            static_cast<int>(imageInfo.format),
            imageInfo.usage,
            static_cast<int>(layout),
            queueFamilyIndex);
        return;
    }

    state->vulkanModule = LoadLibraryW(L"vulkan-1.dll");
    logFunction_("iosurface proof stage=vulkan-module module=%p",
                 state->vulkanModule);
    const auto getInstanceProcAddr =
        moduleFunction<PFN_vkGetInstanceProcAddr>(
            state->vulkanModule, "vkGetInstanceProcAddr");
    const auto createFence = vulkanFunction<PFN_vkCreateFence>(
        getInstanceProcAddr, instance, "vkCreateFence");
    const auto queueSubmit = vulkanFunction<PFN_vkQueueSubmit>(
        getInstanceProcAddr, instance, "vkQueueSubmit");
    state->waitForFences = vulkanFunction<PFN_vkWaitForFences>(
        getInstanceProcAddr, instance, "vkWaitForFences");
    state->destroyFence = vulkanFunction<PFN_vkDestroyFence>(
        getInstanceProcAddr, instance, "vkDestroyFence");
    if (createFence == nullptr || queueSubmit == nullptr ||
        state->waitForFences == nullptr || state->destroyFence == nullptr) {
        logFunction_("iosurface proof failed reason=missing-vulkan-functions");
        if (state->vulkanModule != nullptr) {
            FreeLibrary(state->vulkanModule);
        }
        return;
    }

    state->bridgeModule = LoadLibraryA("alvr_iosurface_bridge.dll");
    logFunction_("iosurface proof stage=bridge-module module=%p",
                 state->bridgeModule);
    const auto bridgeScalar = moduleFunction<BridgeScalarFunction>(
        state->bridgeModule, "alvr_iosurface_scalar");
    const auto bridgeAttach = moduleFunction<BridgeAttachFunction>(
        state->bridgeModule, "alvr_iosurface_attach");
    state->releasePort = moduleFunction<BridgeReleasePortFunction>(
        state->bridgeModule, "alvr_iosurface_release_port");
    if (bridgeScalar == nullptr || bridgeAttach == nullptr ||
        state->releasePort == nullptr) {
        logFunction_("iosurface proof failed reason=missing-bridge-functions");
        FreeLibrary(state->vulkanModule);
        if (state->bridgeModule != nullptr) {
            FreeLibrary(state->bridgeModule);
        }
        return;
    }

    constexpr uint64_t scalarInput = UINT64_C(0x1122334455667788);
    constexpr uint64_t scalarExpected = UINT64_C(0x8f154afd2a2c0b9d);
    uint64_t scalarOutput = 0;
    logFunction_("iosurface proof stage=bridge-scalar-begin");
    const LONG scalarStatus = bridgeScalar(scalarInput, &scalarOutput);
    logFunction_(
        "iosurface proof stage=bridge-scalar-end status=0x%08lx output=0x%llx",
        static_cast<unsigned long>(scalarStatus),
        static_cast<unsigned long long>(scalarOutput));
    int32_t attachResult = VK_ERROR_EXTENSION_NOT_PRESENT;
    LONG attachStatus = static_cast<LONG>(0xc0000001);
    if (scalarStatus == 0 && scalarOutput == scalarExpected) {
        logFunction_("iosurface proof stage=bridge-attach-begin image=%p",
                     image);
        attachStatus = bridgeAttach(
            reinterpret_cast<uintptr_t>(image),
            &attachResult,
            &state->surfaceId,
            &state->machPort);
        logFunction_(
            "iosurface proof stage=bridge-attach-end status=0x%08lx "
            "vk_result=%d surface_id=%u mach_port=%u",
            static_cast<unsigned long>(attachStatus),
            attachResult,
            state->surfaceId,
            state->machPort);
    }
    if (scalarStatus != 0 || scalarOutput != scalarExpected ||
        attachStatus != 0 || attachResult != VK_SUCCESS ||
        state->surfaceId == 0 || state->machPort == 0) {
        logFunction_(
            "iosurface proof failed reason=bridge-attach scalar_status=0x%08lx "
            "attach_status=0x%08lx vk_result=%d",
            static_cast<unsigned long>(scalarStatus),
            static_cast<unsigned long>(attachStatus),
            attachResult);
        FreeLibrary(state->vulkanModule);
        FreeLibrary(state->bridgeModule);
        return;
    }

    VkFenceCreateInfo fenceInfo{};
    fenceInfo.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    logFunction_("iosurface proof stage=fence-create-begin");
    const VkResult fenceResult = createFence(
        state->device, &fenceInfo, nullptr, &state->fence);
    logFunction_(
        "iosurface proof stage=fence-create-end vk_result=%d fence=%p",
        fenceResult,
        state->fence);
    if (fenceResult != VK_SUCCESS) {
        state->releasePort(state->machPort);
        logFunction_("iosurface proof failed reason=fence-create vk_result=%d",
                     fenceResult);
        FreeLibrary(state->vulkanModule);
        FreeLibrary(state->bridgeModule);
        return;
    }

    if (!deleteProofFile(kReadyFile) ||
        !deleteProofFile(kReadyTempFile) ||
        !deleteProofFile(kDoneFile) ||
        !deleteProofFile(kDoneTempFile)) {
        state->destroyFence(state->device, state->fence, nullptr);
        state->releasePort(state->machPort);
        logFunction_("iosurface proof failed reason=stale-proof-files");
        FreeLibrary(state->vulkanModule);
        FreeLibrary(state->bridgeModule);
        return;
    }
    ComPtr<ID3D11DeviceContext> context;
    d3dDevice->GetImmediateContext(&context);
    if (!context) {
        state->destroyFence(state->device, state->fence, nullptr);
        state->releasePort(state->machPort);
        logFunction_("iosurface proof failed reason=missing-immediate-context");
        FreeLibrary(state->vulkanModule);
        FreeLibrary(state->bridgeModule);
        return;
    }
    const auto copyStart = std::chrono::steady_clock::now();
    logFunction_("iosurface proof stage=copy-resource-begin context=%p",
                 context.Get());
    context->CopyResource(state->handoffTexture.Get(), sourceTexture);
    logFunction_("iosurface proof stage=copy-resource-end");
    context->Flush();
    logFunction_("iosurface proof stage=d3d-flush-end");
    interopDevice->vtable->flushRenderingCommands(interopDevice);
    logFunction_("iosurface proof stage=dxvk-flush-end");
    const auto copyDone = std::chrono::steady_clock::now();
    state->copyFlushMicroseconds = elapsedMicroseconds(copyStart, copyDone);

    VkSubmitInfo markerSubmit{};
    markerSubmit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    const auto submitStart = std::chrono::steady_clock::now();
    logFunction_("iosurface proof stage=marker-lock-begin");
    interopDevice->vtable->lockSubmissionQueue(interopDevice);
    logFunction_("iosurface proof stage=marker-submit-begin");
    const VkResult submitResult = queueSubmit(
        queue, 1, &markerSubmit, state->fence);
    logFunction_("iosurface proof stage=marker-submit-end vk_result=%d",
                 submitResult);
    interopDevice->vtable->releaseSubmissionQueue(interopDevice);
    logFunction_("iosurface proof stage=marker-release-end");
    const auto submitDone = std::chrono::steady_clock::now();
    state->markerSubmitMicroseconds = elapsedMicroseconds(
        submitStart, submitDone);
    if (submitResult != VK_SUCCESS) {
        state->destroyFence(state->device, state->fence, nullptr);
        state->releasePort(state->machPort);
        logFunction_("iosurface proof failed reason=marker-submit vk_result=%d",
                     submitResult);
        FreeLibrary(state->vulkanModule);
        FreeLibrary(state->bridgeModule);
        return;
    }

    logFunction_(
        "iosurface proof submitted sequence=%llu eye=%u source=%ux%u "
        "source_format=%u source_bind=0x%x handoff_format=%u handoff_bind=0x0 "
        "vk_format=%d vk_usage=0x%x surface_id=%u proof_nonce=%llu sample=%u,%u "
        "expected_bgra=%u,%u,%u,%u copy_flush_us=%u marker_submit_us=%u",
        static_cast<unsigned long long>(submitSequence),
        eye,
        sourceDescription.Width,
        sourceDescription.Height,
        sourceDescription.Format,
        sourceDescription.BindFlags,
        handoffDescription.Format,
        static_cast<int>(imageInfo.format),
        imageInfo.usage,
        state->surfaceId,
        static_cast<unsigned long long>(state->proofNonce),
        sample.x,
        sample.y,
        static_cast<unsigned>(sample.bgra[0]),
        static_cast<unsigned>(sample.bgra[1]),
        static_cast<unsigned>(sample.bgra[2]),
        static_cast<unsigned>(sample.bgra[3]),
        state->copyFlushMicroseconds,
        state->markerSubmitMicroseconds);

    HMODULE moduleReference = nullptr;
    if (!GetModuleHandleExA(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            reinterpret_cast<LPCSTR>(&kModuleAnchor),
            &moduleReference)) {
        const VkResult waitResult = state->waitForFences(
            state->device, 1, &state->fence, VK_TRUE,
            kPoolFenceTimeoutNanoseconds);
        if (state->fence != VK_NULL_HANDLE) {
            state->destroyFence(state->device, state->fence, nullptr);
        }
        state->releasePort(state->machPort);
        FreeLibrary(state->bridgeModule);
        FreeLibrary(state->vulkanModule);
        logFunction_(
            "iosurface proof failed reason=worker-module-reference "
            "wait_result=%d",
            waitResult);
        return;
    }

    logFunction_("iosurface proof stage=worker-start module=%p",
                 moduleReference);

    auto *workerContext = new (std::nothrow) WorkerContext{
        state, logFunction_, moduleReference};
    if (workerContext == nullptr) {
        const VkResult waitResult = state->waitForFences(
            state->device, 1, &state->fence, VK_TRUE,
            kPoolFenceTimeoutNanoseconds);
        if (state->fence != VK_NULL_HANDLE) {
            state->destroyFence(state->device, state->fence, nullptr);
        }
        state->releasePort(state->machPort);
        FreeLibrary(state->bridgeModule);
        FreeLibrary(state->vulkanModule);
        FreeLibrary(moduleReference);
        logFunction_(
            "iosurface proof failed reason=worker-context-allocation "
            "wait_result=%d",
            waitResult);
        return;
    }

    HANDLE workerThread = CreateThread(
        nullptr, 0, &DxvkIosurfaceSubmitProof::workerEntry, workerContext, 0, nullptr);
    if (workerThread == nullptr) {
        const DWORD threadError = GetLastError();
        delete workerContext;
        const VkResult waitResult = state->waitForFences(
            state->device, 1, &state->fence, VK_TRUE,
            kPoolFenceTimeoutNanoseconds);
        if (state->fence != VK_NULL_HANDLE) {
            state->destroyFence(state->device, state->fence, nullptr);
        }
        state->releasePort(state->machPort);
        FreeLibrary(state->bridgeModule);
        FreeLibrary(state->vulkanModule);
        FreeLibrary(moduleReference);
        logFunction_(
            "iosurface proof failed reason=worker-thread-create "
            "win32_error=%lu wait_result=%d",
            static_cast<unsigned long>(threadError),
            waitResult);
        return;
    }
    CloseHandle(workerThread);
}

std::shared_ptr<DxvkIosurfaceSubmitProof::PoolState>
DxvkIosurfaceSubmitProof::initializePool(
    ID3D11Texture2D *sourceTexture,
    const D3D11_TEXTURE2D_DESC &sourceDescription,
    uint32_t outputWidth,
    uint32_t outputHeight,
    const std::string &serviceName,
    uint64_t sessionNonce,
    SubmitProofLogFunction logFunction) {
    auto state = std::make_shared<PoolState>();
    state->serviceName = serviceName;
    state->sessionNonce = sessionNonce;
    state->width = outputWidth;
    state->height = outputHeight;
    state->lastSourceWidth = sourceDescription.Width;
    state->lastSourceHeight = sourceDescription.Height;

    auto fail = [&](const char *reason) {
        {
            std::lock_guard<std::mutex> lock(state->mutex);
            state->failed = true;
        }
        logFunction("iosurface pool failed reason=%s", reason);
        return state;
    };

    if (state->width == 0 || state->height == 0 || state->width % 4 != 0 ||
        state->height % 2 != 0) {
        return fail("invalid-output-size");
    }

    sourceTexture->GetDevice(&state->d3dDevice);
    if (!state->d3dDevice) {
        return fail("missing-d3d-device");
    }
    state->d3dDevice->GetImmediateContext(&state->d3dContext);
    if (!state->d3dContext) {
        return fail("missing-immediate-context");
    }

    void *interopDeviceRaw = nullptr;
    HRESULT result = state->d3dDevice->QueryInterface(
        kIidDxgiVkInteropDevice, &interopDeviceRaw);
    if (SUCCEEDED(result) && interopDeviceRaw != nullptr) {
        state->interopDevice.Attach(static_cast<IUnknown *>(interopDeviceRaw));
    }
    if (FAILED(result) || !state->interopDevice) {
        return fail("interop-device");
    }
    auto *interopDevice = reinterpret_cast<DxgiVkInteropDevice *>(
        state->interopDevice.Get());
    if (interopDevice->vtable == nullptr ||
        interopDevice->vtable->getVulkanHandles == nullptr ||
        interopDevice->vtable->getSubmissionQueue == nullptr ||
        interopDevice->vtable->flushRenderingCommands == nullptr ||
        interopDevice->vtable->lockSubmissionQueue == nullptr ||
        interopDevice->vtable->releaseSubmissionQueue == nullptr) {
        return fail("interop-device-vtable");
    }

    VkInstance instance = VK_NULL_HANDLE;
    interopDevice->vtable->getVulkanHandles(
        interopDevice, &instance, &state->physicalDevice, &state->device);
    interopDevice->vtable->getSubmissionQueue(
        interopDevice, &state->queue, &state->queueFamilyIndex);
    if (instance == VK_NULL_HANDLE || state->physicalDevice == VK_NULL_HANDLE ||
        state->device == VK_NULL_HANDLE || state->queue == VK_NULL_HANDLE ||
        state->queueFamilyIndex == UINT32_MAX) {
        return fail("vulkan-handles");
    }

    state->vulkanModule = LoadLibraryW(L"vulkan-1.dll");
    const auto getInstanceProcAddr =
        moduleFunction<PFN_vkGetInstanceProcAddr>(
            state->vulkanModule, "vkGetInstanceProcAddr");
    state->createFence = vulkanFunction<PFN_vkCreateFence>(
        getInstanceProcAddr, instance, "vkCreateFence");
    state->queueSubmit = vulkanFunction<PFN_vkQueueSubmit>(
        getInstanceProcAddr, instance, "vkQueueSubmit");
    state->waitForFences = vulkanFunction<PFN_vkWaitForFences>(
        getInstanceProcAddr, instance, "vkWaitForFences");
    state->destroyFence = vulkanFunction<PFN_vkDestroyFence>(
        getInstanceProcAddr, instance, "vkDestroyFence");
    state->getPhysicalDeviceFormatProperties =
        vulkanFunction<PFN_vkGetPhysicalDeviceFormatProperties>(
            getInstanceProcAddr, instance, "vkGetPhysicalDeviceFormatProperties");
    state->createCommandPool = vulkanFunction<PFN_vkCreateCommandPool>(
        getInstanceProcAddr, instance, "vkCreateCommandPool");
    state->destroyCommandPool = vulkanFunction<PFN_vkDestroyCommandPool>(
        getInstanceProcAddr, instance, "vkDestroyCommandPool");
    state->allocateCommandBuffers = vulkanFunction<PFN_vkAllocateCommandBuffers>(
        getInstanceProcAddr, instance, "vkAllocateCommandBuffers");
    state->resetCommandPool = vulkanFunction<PFN_vkResetCommandPool>(
        getInstanceProcAddr, instance, "vkResetCommandPool");
    state->beginCommandBuffer = vulkanFunction<PFN_vkBeginCommandBuffer>(
        getInstanceProcAddr, instance, "vkBeginCommandBuffer");
    state->endCommandBuffer = vulkanFunction<PFN_vkEndCommandBuffer>(
        getInstanceProcAddr, instance, "vkEndCommandBuffer");
    state->cmdBlitImage = vulkanFunction<PFN_vkCmdBlitImage>(
        getInstanceProcAddr, instance, "vkCmdBlitImage");
    state->cmdPipelineBarrier = vulkanFunction<PFN_vkCmdPipelineBarrier>(
        getInstanceProcAddr, instance, "vkCmdPipelineBarrier");
    if (state->vulkanModule == nullptr || state->createFence == nullptr ||
        state->queueSubmit == nullptr || state->waitForFences == nullptr ||
        state->destroyFence == nullptr ||
        state->getPhysicalDeviceFormatProperties == nullptr ||
        state->createCommandPool == nullptr ||
        state->destroyCommandPool == nullptr ||
        state->allocateCommandBuffers == nullptr ||
        state->resetCommandPool == nullptr ||
        state->beginCommandBuffer == nullptr ||
        state->endCommandBuffer == nullptr || state->cmdBlitImage == nullptr ||
        state->cmdPipelineBarrier == nullptr) {
        return fail("vulkan-functions");
    }

    state->bridgeModule = LoadLibraryA("alvr_iosurface_bridge.dll");
    state->importBind = moduleFunction<BridgeImportBindFunction>(
        state->bridgeModule, "alvr_iosurface_import_bind");
    state->frameReady = moduleFunction<BridgeFrameReadyFunction>(
        state->bridgeModule, "alvr_iosurface_frame_ready");
    if (state->bridgeModule == nullptr || state->importBind == nullptr ||
        state->frameReady == nullptr) {
        return fail("bridge-functions");
    }

    D3D11_TEXTURE2D_DESC handoffDescription{};
    handoffDescription.Width = state->width;
    handoffDescription.Height = state->height;
    handoffDescription.MipLevels = 1;
    handoffDescription.ArraySize = 1;
    handoffDescription.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    handoffDescription.SampleDesc.Count = 1;
    handoffDescription.Usage = D3D11_USAGE_DEFAULT;

    std::array<bool, kPoolSlotCount> imported{};
    for (uint32_t importIndex = 0; importIndex < kPoolSlotCount; ++importIndex) {
        ComPtr<ID3D11Texture2D> handoffTexture;
        result = state->d3dDevice->CreateTexture2D(
            &handoffDescription, nullptr, &handoffTexture);
        if (FAILED(result) || !handoffTexture) {
            return fail("handoff-create");
        }

        ComPtr<IUnknown> interopSurfaceHolder;
        void *interopSurfaceRaw = nullptr;
        result = handoffTexture->QueryInterface(
            kIidDxgiVkInteropSurface, &interopSurfaceRaw);
        if (SUCCEEDED(result) && interopSurfaceRaw != nullptr) {
            interopSurfaceHolder.Attach(
                static_cast<IUnknown *>(interopSurfaceRaw));
        }
        if (FAILED(result) || !interopSurfaceHolder) {
            return fail("interop-surface");
        }
        auto *interopSurface = reinterpret_cast<DxgiVkInteropSurface *>(
            interopSurfaceHolder.Get());
        if (interopSurface->vtable == nullptr ||
            interopSurface->vtable->getVulkanImageInfo == nullptr) {
            return fail("interop-surface-vtable");
        }

        VkImage image = VK_NULL_HANDLE;
        VkImageLayout layout = VK_IMAGE_LAYOUT_UNDEFINED;
        VkImageCreateInfo imageInfo{};
        imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
        result = interopSurface->vtable->getVulkanImageInfo(
            interopSurface, &image, &layout, &imageInfo);
        if (FAILED(result) || image == VK_NULL_HANDLE ||
            imageInfo.format != VK_FORMAT_B8G8R8A8_UNORM ||
            (imageInfo.usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT) == 0) {
            return fail("vulkan-image");
        }

        AlvrIosurfaceBindResult bindResult{};
        const LONG bindStatus = state->importBind(
            reinterpret_cast<uintptr_t>(image),
            state->serviceName.c_str(),
            state->sessionNonce,
            &bindResult);
        if (bindStatus != 0 || bindResult.vkResult != VK_SUCCESS ||
            bindResult.verificationStatus != ALVR_IOSURFACE_PROBE_PASS ||
            bindResult.slotIndex >= kPoolSlotCount ||
            imported[bindResult.slotIndex] || bindResult.surfaceId == 0 ||
            bindResult.width != state->width ||
            bindResult.height != state->height) {
            return fail("native-bind");
        }

        imported[bindResult.slotIndex] = true;
        PoolState::Slot &slot = state->slots[bindResult.slotIndex];
        slot.handoffTexture = std::move(handoffTexture);
        slot.interopSurface = std::move(interopSurfaceHolder);
        slot.image = image;
        slot.layout = layout;
        slot.format = imageInfo.format;
        slot.usage = imageInfo.usage;
        slot.tiling = imageInfo.tiling;
        slot.surfaceId = bindResult.surfaceId;
        slot.generation = bindResult.generation;

        VkCommandPoolCreateInfo commandPoolInfo{};
        commandPoolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
        commandPoolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
        commandPoolInfo.queueFamilyIndex = state->queueFamilyIndex;
        VkResult commandResult = state->createCommandPool(
            state->device, &commandPoolInfo, nullptr, &slot.commandPool);
        if (commandResult != VK_SUCCESS) {
            return fail("command-pool-create");
        }
        VkCommandBufferAllocateInfo commandBufferInfo{};
        commandBufferInfo.sType =
            VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
        commandBufferInfo.commandPool = slot.commandPool;
        commandBufferInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        commandBufferInfo.commandBufferCount = 1;
        commandResult = state->allocateCommandBuffers(
            state->device, &commandBufferInfo, &slot.commandBuffer);
        if (commandResult != VK_SUCCESS) {
            return fail("command-buffer-allocate");
        }
        logFunction(
            "iosurface pool imported slot=%u surface_id=%u size=%ux%u "
            "bytes_per_row=%u vk_format=%d vk_layout=%d vk_usage=0x%x",
            bindResult.slotIndex,
            bindResult.surfaceId,
            bindResult.width,
            bindResult.height,
            bindResult.bytesPerRow,
            static_cast<int>(slot.format),
            static_cast<int>(slot.layout),
            slot.usage);
    }

    const uint32_t selfTestWidth = state->width / 2;
    const uint32_t selfTestHeight = state->height / 2;
    if (selfTestWidth < 4 || selfTestHeight < 2 || selfTestWidth % 2 != 0) {
        return fail("self-test-size");
    }
    const std::array<uint8_t, 4> leftColor = {0x21, 0x45, 0x89, 0xff};
    const std::array<uint8_t, 4> rightColor = {0xc3, 0x67, 0x2d, 0xff};
    const uint32_t leftPixel = static_cast<uint32_t>(leftColor[0]) |
                               (static_cast<uint32_t>(leftColor[1]) << 8) |
                               (static_cast<uint32_t>(leftColor[2]) << 16) |
                               (static_cast<uint32_t>(leftColor[3]) << 24);
    const uint32_t rightPixel = static_cast<uint32_t>(rightColor[0]) |
                                (static_cast<uint32_t>(rightColor[1]) << 8) |
                                (static_cast<uint32_t>(rightColor[2]) << 16) |
                                (static_cast<uint32_t>(rightColor[3]) << 24);
    std::vector<uint32_t> selfTestPixels(
        static_cast<size_t>(selfTestWidth) * selfTestHeight);
    for (uint32_t y = 0; y < selfTestHeight; ++y) {
        for (uint32_t x = 0; x < selfTestWidth; ++x) {
            selfTestPixels[static_cast<size_t>(y) * selfTestWidth + x] =
                x < selfTestWidth / 2 ? leftPixel : rightPixel;
        }
    }
    D3D11_TEXTURE2D_DESC selfTestDescription{};
    selfTestDescription.Width = selfTestWidth;
    selfTestDescription.Height = selfTestHeight;
    selfTestDescription.MipLevels = 1;
    selfTestDescription.ArraySize = 1;
    selfTestDescription.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    selfTestDescription.SampleDesc.Count = 1;
    selfTestDescription.Usage = D3D11_USAGE_DEFAULT;
    selfTestDescription.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA selfTestData{};
    selfTestData.pSysMem = selfTestPixels.data();
    selfTestData.SysMemPitch = selfTestWidth * sizeof(uint32_t);
    selfTestData.SysMemSlicePitch =
        selfTestData.SysMemPitch * selfTestHeight;
    ComPtr<ID3D11Texture2D> selfTestTexture;
    result = state->d3dDevice->CreateTexture2D(
        &selfTestDescription, &selfTestData, &selfTestTexture);
    if (FAILED(result) || !selfTestTexture) {
        return fail("self-test-source-create");
    }
    std::vector<uint32_t> selfTestRgbaPixels(
        static_cast<size_t>(selfTestWidth) * selfTestHeight);
    const auto rgbaPixel = [](const std::array<uint8_t, 4> &bgra) {
        return static_cast<uint32_t>(bgra[2]) |
               (static_cast<uint32_t>(bgra[1]) << 8) |
               (static_cast<uint32_t>(bgra[0]) << 16) |
               (static_cast<uint32_t>(bgra[3]) << 24);
    };
    const uint32_t leftRgbaPixel = rgbaPixel(leftColor);
    const uint32_t rightRgbaPixel = rgbaPixel(rightColor);
    for (uint32_t y = 0; y < selfTestHeight; ++y) {
        for (uint32_t x = 0; x < selfTestWidth; ++x) {
            selfTestRgbaPixels[static_cast<size_t>(y) * selfTestWidth + x] =
                x < selfTestWidth / 2 ? leftRgbaPixel : rightRgbaPixel;
        }
    }
    D3D11_TEXTURE2D_DESC selfTestRgbaDescription = selfTestDescription;
    selfTestRgbaDescription.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    D3D11_SUBRESOURCE_DATA selfTestRgbaData = selfTestData;
    selfTestRgbaData.pSysMem = selfTestRgbaPixels.data();
    ComPtr<ID3D11Texture2D> selfTestRgbaTexture;
    result = state->d3dDevice->CreateTexture2D(
        &selfTestRgbaDescription, &selfTestRgbaData, &selfTestRgbaTexture);
    if (FAILED(result) || !selfTestRgbaTexture) {
        return fail("self-test-rgba-source-create");
    }

    for (uint32_t slotIndex = 0; slotIndex < kPoolSlotCount; ++slotIndex) {
        PoolState::Slot &slot = state->slots[slotIndex];
        uint32_t sampleX = state->width / 4;
        const uint32_t sampleY = state->height / 2;
        const std::array<uint8_t, 4> *expected = &leftColor;
        if (slotIndex == 1) {
            sampleX = state->width / 2 - 1;
        } else if (slotIndex == 2) {
            sampleX = state->width / 2;
            expected = &rightColor;
        }

        PoolTransferSubmission transfer{};
        ID3D11Texture2D *transferTexture = selfTestTexture.Get();
        const D3D11_TEXTURE2D_DESC *transferDescription = &selfTestDescription;
        if (slotIndex == 2) {
            transferTexture = selfTestRgbaTexture.Get();
            transferDescription = &selfTestRgbaDescription;
        }
        if (!state->submitTransfer(transferTexture,
                                   *transferDescription,
                                   slotIndex,
                                   &transfer)) {
            logFunction(
                "iosurface pool self-test transfer failed slot=%u reason=%s "
                "vk_result=%d",
                slotIndex,
                transfer.failureReason != nullptr ? transfer.failureReason
                                                  : "unknown",
                transfer.failureResult);
            return fail("self-test-transfer");
        }
        VkResult vkResult = VK_SUCCESS;
        uint64_t waitAttempts = 0;
        for (;;) {
            vkResult = state->waitForFences(
                state->device, 1, &transfer.fence, VK_TRUE,
                kPoolFenceTimeoutNanoseconds);
            if (vkResult == VK_SUCCESS || vkResult == VK_ERROR_DEVICE_LOST) {
                break;
            }
            ++waitAttempts;
            if (waitAttempts == 1 || waitAttempts % 12 == 0) {
                logFunction(
                    "iosurface pool self-test fence quarantine slot=%u "
                    "wait_result=%d attempts=%llu",
                    slotIndex,
                    vkResult,
                    static_cast<unsigned long long>(waitAttempts));
            }
            if (vkResult != VK_TIMEOUT) {
                Sleep(100);
            }
        }
        state->destroyFence(state->device, transfer.fence, nullptr);
        transfer.fence = VK_NULL_HANDLE;
        if (vkResult != VK_SUCCESS) {
            return fail("self-test-fence");
        }

        AlvrIosurfaceFrame frame{};
        frame.frameId = state->nextFrameId++;
        frame.slotIndex = slotIndex;
        frame.generation = ++slot.generation;
        frame.flags = ALVR_IOSURFACE_FRAME_SELF_TEST;
        frame.surfaceId = slot.surfaceId;
        frame.width = state->width;
        frame.height = state->height;
        frame.sampleX = sampleX;
        frame.sampleY = sampleY;
        std::memcpy(
            frame.expectedBgra, expected->data(), expected->size());
        AlvrIosurfaceReleaseResult release{};
        const LONG frameStatus = state->frameReady(
            state->serviceName.c_str(),
            state->sessionNonce,
            &frame,
            &release);
        if (frameStatus != 0 ||
            release.status != ALVR_IOSURFACE_PROBE_PASS ||
            std::memcmp(
                release.actualBgra, expected->data(), expected->size()) != 0) {
            return fail("self-test-release");
        }
        logFunction(
            "iosurface pool self-test slot=%u generation=%u surface_id=%u "
            "source=%ux%u output=%ux%u transfer=%s sample=%u,%u "
            "actual_bgra=%u,%u,%u,%u result=pass",
            slotIndex,
            slot.generation,
            slot.surfaceId,
            selfTestWidth,
            selfTestHeight,
            state->width,
            state->height,
            poolTransferModeName(transfer.mode),
            sampleX,
            sampleY,
            release.actualBgra[0],
            release.actualBgra[1],
            release.actualBgra[2],
            release.actualBgra[3]);
    }

    return state;
}

void DxvkIosurfaceSubmitProof::startPoolInitialization(
    ID3D11Texture2D *sourceTexture,
    const D3D11_TEXTURE2D_DESC &sourceDescription) {
    std::shared_ptr<PoolInitializationState> initialization;
    std::string serviceName;
    uint64_t sessionNonce = 0;
    SubmitProofLogFunction logFunction = nullptr;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (poolState_ || poolInitialization_ || poolInitializationFailed_ ||
            shutdownInProgress_ || shutdownComplete_ || !poolConfigured_) {
            return;
        }
        initialization = std::make_shared<PoolInitializationState>();
        poolInitialization_ = initialization;
        serviceName = poolService_;
        sessionNonce = poolNonce_;
        logFunction = logFunction_;
    }

    auto fail = [&](const char *reason) {
        {
            std::lock_guard<std::mutex> lock(initialization->mutex);
            initialization->failed = true;
            initialization->completed = true;
            initialization->published = true;
            initialization->startupFinished = true;
        }
        initialization->condition.notify_all();
        logFunction("iosurface pool failed reason=%s", reason);
    };

    ComPtr<ID3D11Device> device;
    sourceTexture->GetDevice(&device);
    if (!device) {
        fail("initialization-device");
        return;
    }

    ComPtr<ID3D10Multithread> multithread;
    const HRESULT multithreadResult = device->QueryInterface(
        __uuidof(ID3D10Multithread),
        reinterpret_cast<void **>(multithread.GetAddressOf()));
    if (FAILED(multithreadResult) || !multithread) {
        fail("multithread-interface");
        return;
    }
    const BOOL wasProtected = multithread->SetMultithreadProtected(TRUE);
    if (!multithread->GetMultithreadProtected()) {
        fail("multithread-protection");
        return;
    }

    HMODULE moduleReference = nullptr;
    if (!GetModuleHandleExA(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            reinterpret_cast<LPCSTR>(&kModuleAnchor),
            &moduleReference)) {
        if (!wasProtected) {
            multithread->SetMultithreadProtected(FALSE);
        }
        fail("initialization-module-reference");
        return;
    }

    auto *context = new (std::nothrow) PoolInitializationContext();
    if (context == nullptr) {
        FreeLibrary(moduleReference);
        if (!wasProtected) {
            multithread->SetMultithreadProtected(FALSE);
        }
        fail("initialization-context-allocation");
        return;
    }
    context->sourceTexture = sourceTexture;
    context->sourceDescription = sourceDescription;
    context->outputWidth = poolWidth_ != 0 ? poolWidth_ : sourceDescription.Width;
    context->outputHeight = poolHeight_ != 0 ? poolHeight_ : sourceDescription.Height;
    context->multithread = multithread;
    context->multithreadWasProtected = wasProtected != FALSE;
    context->initialization = initialization;
    context->serviceName = serviceName;
    context->sessionNonce = sessionNonce;
    context->logFunction = logFunction;
    context->moduleReference = moduleReference;

    DWORD workerThreadId = 0;
    HANDLE workerThread = CreateThread(
        nullptr,
        0,
        &DxvkIosurfaceSubmitProof::poolInitializationEntry,
        context,
        0,
        &workerThreadId);
    if (workerThread == nullptr) {
        const DWORD threadError = GetLastError();
        delete context;
        if (!wasProtected) {
            multithread->SetMultithreadProtected(FALSE);
        }
        {
            std::lock_guard<std::mutex> lock(initialization->mutex);
            initialization->failed = true;
            initialization->completed = true;
            initialization->published = true;
            initialization->startupFinished = true;
        }
        initialization->condition.notify_all();
        logFunction(
            "iosurface pool failed reason=initialization-thread "
            "win32_error=%lu",
            static_cast<unsigned long>(threadError));
        FreeLibrary(moduleReference);
        return;
    }
    {
        std::lock_guard<std::mutex> lock(initialization->mutex);
        initialization->thread = workerThread;
        initialization->threadId = workerThreadId;
        initialization->startupFinished = true;
    }
    initialization->condition.notify_all();
    logFunction(
        "iosurface pool initialization started source=%ux%u output=%ux%u "
        "multithread_was_protected=%u",
        sourceDescription.Width,
        sourceDescription.Height,
        context->outputWidth,
        context->outputHeight,
        wasProtected ? 1 : 0);
}

void DxvkIosurfaceSubmitProof::runPoolInitialization(
    PoolInitializationContext *context) {
    std::shared_ptr<PoolState> state;
    try {
        state = initializePool(
            context->sourceTexture.Get(),
            context->sourceDescription,
            context->outputWidth,
            context->outputHeight,
            context->serviceName,
            context->sessionNonce,
            context->logFunction);
    } catch (...) {
        context->logFunction(
            "iosurface pool failed reason=initialization-exception");
    }

    bool failed = !state;
    if (state) {
        state->multithread = context->multithread;
        state->multithreadWasProtected = context->multithreadWasProtected;
        std::lock_guard<std::mutex> stateLock(state->mutex);
        failed = state->failed;
    } else if (context->multithread &&
               !context->multithreadWasProtected) {
        context->multithread->SetMultithreadProtected(FALSE);
    }
    {
        std::lock_guard<std::mutex> lock(context->initialization->mutex);
        context->initialization->poolState = state;
        context->initialization->failed = failed;
        context->initialization->published = true;
    }
    context->initialization->condition.notify_all();

    if (state && !failed) {
        AlvrIosurfaceFrame frame{};
        {
            std::lock_guard<std::mutex> stateLock(state->mutex);
            PoolState::Slot &slot = state->slots[0];
            frame.frameId = state->nextFrameId++;
            frame.slotIndex = 0;
            frame.generation = ++slot.generation;
            frame.flags = kFrameFlagStartupBarrier;
            frame.surfaceId = slot.surfaceId;
            frame.width = state->width;
            frame.height = state->height;
        }

        AlvrIosurfaceReleaseResult release{};
        const LONG barrierStatus = state->frameReady(
            state->serviceName.c_str(),
            state->sessionNonce,
            &frame,
            &release);
        if (barrierStatus != 0 ||
            release.status != ALVR_IOSURFACE_PROBE_PASS) {
            context->logFunction(
                "iosurface pool failed reason=startup-barrier ipc_status=0x%08lx "
                "release_status=%u",
                static_cast<unsigned long>(barrierStatus),
                release.status);
            std::lock_guard<std::mutex> stateLock(state->mutex);
            state->failed = true;
            failed = true;
        } else {
            {
                std::lock_guard<std::mutex> stateLock(state->mutex);
                state->startupReady = true;
                state->nextFrameToSend = state->nextFrameId;
            }
            context->logFunction(
                "iosurface pool startup barrier frame_id=%llu slot=%u "
                "generation=%u result=pass",
                static_cast<unsigned long long>(frame.frameId),
                frame.slotIndex,
                frame.generation);
            context->logFunction(
                "iosurface pool initialized service=%s nonce=%llu slots=%u "
                "source=%ux%u output=%ux%u",
                state->serviceName.c_str(),
                static_cast<unsigned long long>(state->sessionNonce),
                kPoolSlotCount,
                state->lastSourceWidth,
                state->lastSourceHeight,
                state->width,
                state->height);
        }
    }

    {
        std::lock_guard<std::mutex> lock(context->initialization->mutex);
        context->initialization->failed = failed;
        context->initialization->completed = true;
    }
    context->initialization->condition.notify_all();
}

DWORD WINAPI DxvkIosurfaceSubmitProof::poolInitializationEntry(
    void *parameter) {
    auto *context = static_cast<PoolInitializationContext *>(parameter);
    const HMODULE moduleReference = context->moduleReference;
    runPoolInitialization(context);
    delete context;
    FreeLibraryAndExitThread(moduleReference, 0);
    return 0;
}

void DxvkIosurfaceSubmitProof::capturePoolSources(
    ID3D11Texture2D *leftTexture,
    ID3D11Texture2D *rightTexture,
    uint64_t submitSequence,
    uint32_t eye,
    uint64_t videoTimestampNs,
    const SubmitProofPose &pose) {
    if (leftTexture == nullptr) {
        return;
    }
    D3D11_TEXTURE2D_DESC leftDescription{};
    leftTexture->GetDesc(&leftDescription);
    const bool separateEyes = rightTexture != nullptr;
    const bool leftBgra =
        leftDescription.Format == DXGI_FORMAT_B8G8R8A8_TYPELESS ||
        leftDescription.Format == DXGI_FORMAT_B8G8R8A8_UNORM ||
        leftDescription.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
    const bool leftRgba =
        leftDescription.Format == DXGI_FORMAT_R8G8B8A8_TYPELESS ||
        leftDescription.Format == DXGI_FORMAT_R8G8B8A8_UNORM ||
        leftDescription.Format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
    if (leftDescription.SampleDesc.Count != 1 ||
        leftDescription.ArraySize != 1 || leftDescription.MipLevels != 1 ||
        (!leftBgra && !leftRgba)) {
        return;
    }
    D3D11_TEXTURE2D_DESC rightDescription{};
    if (separateEyes) {
        rightTexture->GetDesc(&rightDescription);
        if (rightDescription.Width != leftDescription.Width ||
            rightDescription.Height != leftDescription.Height ||
            rightDescription.Format != leftDescription.Format ||
            rightDescription.SampleDesc.Count != 1 ||
            rightDescription.ArraySize != 1 ||
            rightDescription.MipLevels != 1) {
            return;
        }
    }
    const uint32_t sourceWidth = separateEyes
                                     ? leftDescription.Width +
                                           rightDescription.Width
                                     : leftDescription.Width;
    const uint32_t sourceHeight = leftDescription.Height;

    collectPoolInitialization();
    std::shared_ptr<PoolState> state;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        state = poolState_;
    }
    if (!state) {
        startPoolInitialization(leftTexture, leftDescription);
        return;
    }

    uint32_t slotIndex = UINT32_MAX;
    uint32_t generation = 0;
    uint64_t frameId = 0;
    uint64_t droppedFrames = 0;
    uint32_t acquireWaitMicroseconds = 0;
    bool startupPrime = false;
    bool sourceTransition = false;
    uint32_t previousSourceWidth = 0;
    uint32_t previousSourceHeight = 0;
    uint64_t sourceTransitionCount = 0;
    {
        std::unique_lock<std::mutex> lock(state->mutex);
        if (state->failed || state->closing || !state->startupReady) {
            return;
        }
        if (sourceWidth != state->lastSourceWidth ||
            sourceHeight != state->lastSourceHeight) {
            sourceTransition = true;
            previousSourceWidth = state->lastSourceWidth;
            previousSourceHeight = state->lastSourceHeight;
            state->lastSourceWidth = sourceWidth;
            state->lastSourceHeight = sourceHeight;
            state->resizedFramesSinceTransition = 0;
            sourceTransitionCount = ++state->sourceTransitions;
        }
        auto availableSlot = [&]() {
            for (uint32_t index = 0; index < kPoolSlotCount; ++index) {
                if (state->slots[index].status ==
                    PoolState::SlotStatus::Available) {
                    return index;
                }
            }
            return UINT32_MAX;
        };
        slotIndex = availableSlot();
        if (slotIndex == UINT32_MAX) {
            const auto acquireStart = std::chrono::steady_clock::now();
            state->workerCondition.wait_for(
                lock,
                kPoolAcquireTimeout,
                [&]() {
                    return state->failed || state->closing ||
                           availableSlot() != UINT32_MAX;
                });
            acquireWaitMicroseconds = elapsedMicroseconds(
                acquireStart, std::chrono::steady_clock::now());
            if (state->failed || state->closing) {
                return;
            }
            slotIndex = availableSlot();
        }
        if (slotIndex != UINT32_MAX) {
            PoolState::Slot &slot = state->slots[slotIndex];
            slot.status = PoolState::SlotStatus::CopySubmitted;
            generation = ++slot.generation;
            frameId = state->nextFrameId++;
            startupPrime = state->submittedFrames == 0;
            ++state->submittedFrames;
            ++state->activeWorkers;
        }
        if (slotIndex == UINT32_MAX) {
            droppedFrames = ++state->droppedFrames;
        }
    }
    if (sourceTransition) {
        logFunction_(
            "iosurface pool source geometry transition old=%ux%u new=%ux%u "
            "output=%ux%u transition=%llu",
            previousSourceWidth,
            previousSourceHeight,
            sourceWidth,
            sourceHeight,
            state->width,
            state->height,
            static_cast<unsigned long long>(sourceTransitionCount));
    }
    if (acquireWaitMicroseconds > 0 && slotIndex != UINT32_MAX) {
        logFunction_(
            "iosurface pool backpressure wait_us=%u slot=%u",
            acquireWaitMicroseconds,
            slotIndex);
    }
    if (slotIndex == UINT32_MAX) {
        if (droppedFrames <= 5 || droppedFrames % 120 == 0) {
            logFunction_(
                "iosurface pool drop reason=exhausted submit_sequence=%llu "
                "wait_us=%u dropped=%llu",
                static_cast<unsigned long long>(submitSequence),
                acquireWaitMicroseconds,
                static_cast<unsigned long long>(droppedFrames));
        }
        return;
    }

    auto finishClaimedSlot = [&](bool failed) {
        std::lock_guard<std::mutex> lock(state->mutex);
        state->slots[slotIndex].status = PoolState::SlotStatus::Available;
        state->failed = state->failed || failed;
        if (state->activeWorkers > 0) {
            --state->activeWorkers;
        }
        state->workerCondition.notify_all();
        state->sendCondition.notify_all();
    };

    HMODULE moduleReference = nullptr;
    if (!GetModuleHandleExA(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            reinterpret_cast<LPCSTR>(&kModuleAnchor),
            &moduleReference)) {
        finishClaimedSlot(true);
        logFunction_("iosurface pool failed reason=module-reference");
        return;
    }

    auto *workerContext = new (std::nothrow) PoolWorkerContext();
    if (workerContext == nullptr) {
        FreeLibrary(moduleReference);
        finishClaimedSlot(true);
        logFunction_("iosurface pool failed reason=worker-allocation");
        return;
    }
    workerContext->state = state;
    workerContext->slotIndex = slotIndex;
    workerContext->generation = generation;
    workerContext->frameId = frameId;
    workerContext->videoTimestampNs = videoTimestampNs;
    workerContext->pose = pose;
    workerContext->eye = eye;
    workerContext->sourceFormat = leftDescription.Format;
    workerContext->sourceBindFlags = leftDescription.BindFlags;
    workerContext->sourceWidth = sourceWidth;
    workerContext->sourceHeight = sourceHeight;
    workerContext->logFunction = logFunction_;
    workerContext->moduleReference = moduleReference;
    workerContext->startEvent = CreateEventA(nullptr, TRUE, FALSE, nullptr);
    if (workerContext->startEvent == nullptr) {
        const DWORD eventError = GetLastError();
        delete workerContext;
        FreeLibrary(moduleReference);
        finishClaimedSlot(true);
        logFunction_(
            "iosurface pool failed reason=worker-event win32_error=%lu",
            static_cast<unsigned long>(eventError));
        return;
    }

    HANDLE workerThread = CreateThread(
        nullptr,
        0,
        &DxvkIosurfaceSubmitProof::poolWorkerEntry,
        workerContext,
        0,
        nullptr);
    if (workerThread == nullptr) {
        const DWORD threadError = GetLastError();
        delete workerContext;
        FreeLibrary(moduleReference);
        finishClaimedSlot(true);
        logFunction_(
            "iosurface pool failed reason=worker-thread win32_error=%lu",
            static_cast<unsigned long>(threadError));
        return;
    }
    CloseHandle(workerThread);

    PoolTransferSubmission transfer{};
    const bool transferSubmitted = separateEyes
        ? state->submitSeparateEyeTransfer(
              leftTexture,
              leftDescription,
              rightTexture,
              rightDescription,
              slotIndex,
              &transfer)
        : state->submitTransfer(
              leftTexture, leftDescription, slotIndex, &transfer);
    if (!transferSubmitted) {
        {
            std::lock_guard<std::mutex> lock(state->mutex);
            state->failed = true;
        }
        workerContext->cancelled = true;
        SetEvent(workerContext->startEvent);
        logFunction_(
            "iosurface pool failed reason=transfer-%s vk_result=%d "
            "source=%ux%u output=%ux%u",
            transfer.failureReason != nullptr ? transfer.failureReason : "unknown",
            transfer.failureResult,
            sourceWidth,
            sourceHeight,
            state->width,
            state->height);
        return;
    }
    const uint32_t copyFlushMicroseconds = transfer.prepareMicroseconds;
    const uint32_t markerSubmitMicroseconds = transfer.submitMicroseconds;
    workerContext->transferMode = transfer.mode;
    workerContext->copyFlushMicroseconds = copyFlushMicroseconds;
    workerContext->markerSubmitMicroseconds = markerSubmitMicroseconds;
    workerContext->fence = transfer.fence;
    workerContext->sourceTexture = leftTexture;
    workerContext->rightSourceTexture = rightTexture;

    bool slotReady = false;
    {
        std::lock_guard<std::mutex> lock(state->mutex);
        PoolState::Slot &slot = state->slots[slotIndex];
        if (slot.generation != generation ||
            slot.status != PoolState::SlotStatus::CopySubmitted) {
            state->failed = true;
        } else {
            slot.status = PoolState::SlotStatus::AwaitingRelease;
            slotReady = true;
            switch (transfer.mode) {
                case PoolTransferMode::DirectCopy:
                    ++state->directCopyFrames;
                    break;
                case PoolTransferMode::StereoLinearBlit:
                    ++state->linearBlitFrames;
                    ++state->resizedFramesSinceTransition;
                    break;
                case PoolTransferMode::StereoNearestBlit:
                    ++state->nearestBlitFrames;
                    ++state->resizedFramesSinceTransition;
                    break;
                case PoolTransferMode::SeparateEyeBlit:
                    ++state->separateEyeFrames;
                    break;
            }
            if ((transfer.mode == PoolTransferMode::StereoLinearBlit ||
                 transfer.mode == PoolTransferMode::StereoNearestBlit) &&
                state->resizedFramesSinceTransition ==
                    kResizeConsumerSampleFrame) {
                workerContext->consumerSample = true;
                ++state->resizedConsumerSamples;
            } else if (
                transfer.mode == PoolTransferMode::SeparateEyeBlit &&
                state->separateEyeFrames ==
                    kSeparateEyeConsumerSampleFrame) {
                workerContext->consumerSample = true;
                ++state->separateEyeConsumerSamples;
            }
        }
    }
    if (!slotReady) {
        workerContext->cancelled = true;
        SetEvent(workerContext->startEvent);
        logFunction_("iosurface pool failed reason=slot-transition");
        return;
    }
    const bool consumerSample = workerContext->consumerSample;
    if (!SetEvent(workerContext->startEvent)) {
        const DWORD eventError = GetLastError();
        {
            std::lock_guard<std::mutex> lock(state->mutex);
            state->failed = true;
        }
        logFunction_(
            "iosurface pool failed reason=worker-start-event win32_error=%lu",
            static_cast<unsigned long>(eventError));
        return;
    }

    logFunction_(
        "iosurface pool submitted frame_id=%llu submit_sequence=%llu eye=%u "
        "slot=%u generation=%u surface_id=%u validation=%s "
        "source=%ux%u output=%ux%u source_format=%u source_bind=0x%x "
        "transfer=%s "
        "copy_flush_us=%u marker_submit_us=%u pose_source=%s "
        "pose_generation=%llu pose_timestamp_ns=%llu "
        "pose_source_session=%llu pose_source_generation=%llu",
        static_cast<unsigned long long>(frameId),
        static_cast<unsigned long long>(submitSequence),
        eye,
        slotIndex,
        generation,
        state->slots[slotIndex].surfaceId,
        consumerSample ? "consumer-sample" : "metadata-only",
        sourceWidth,
        sourceHeight,
        state->width,
        state->height,
        leftDescription.Format,
        leftDescription.BindFlags,
        poolTransferModeName(transfer.mode),
        copyFlushMicroseconds,
        markerSubmitMicroseconds,
        pose.fallback ? "fallback" : "shared",
        static_cast<unsigned long long>(pose.generation),
        static_cast<unsigned long long>(pose.timestampNs),
        static_cast<unsigned long long>(pose.sourceSessionId),
        static_cast<unsigned long long>(pose.sourceGeneration));

    if (startupPrime) {
        bool released = false;
        {
            std::unique_lock<std::mutex> lock(state->mutex);
            const bool finished = state->workerCondition.wait_for(
                lock,
                kPoolWorkerOrderTimeout,
                [&]() {
                    return state->failed || state->closing ||
                           state->releasedFrames > 0;
                });
            released = finished && state->releasedFrames > 0 && !state->failed;
            if (!released && !state->closing) {
                state->failed = true;
                state->sendCondition.notify_all();
                state->workerCondition.notify_all();
            }
        }
        logFunction_(
            "iosurface pool startup prime frame_id=%llu result=%s",
            static_cast<unsigned long long>(frameId),
            released ? "pass" : "fail");
    }
}

void DxvkIosurfaceSubmitProof::runPoolWorker(PoolWorkerContext *context) {
    std::shared_ptr<PoolState> state = context->state;
    const auto waitStart = std::chrono::steady_clock::now();
    VkResult waitResult = VK_SUCCESS;
    uint64_t waitAttempts = 0;
    if (context->fence != VK_NULL_HANDLE) {
        for (;;) {
            waitResult = state->waitForFences(
                state->device,
                1,
                &context->fence,
                VK_TRUE,
                kPoolFenceTimeoutNanoseconds);
            if (waitResult == VK_SUCCESS ||
                waitResult == VK_ERROR_DEVICE_LOST) {
                break;
            }
            ++waitAttempts;
            {
                std::lock_guard<std::mutex> lock(state->mutex);
                state->failed = true;
                state->sendCondition.notify_all();
                state->workerCondition.notify_all();
            }
            if (waitAttempts == 1 || waitAttempts % 12 == 0) {
                context->logFunction(
                    "iosurface pool fence quarantine frame_id=%llu slot=%u "
                    "wait_result=%d attempts=%llu",
                    static_cast<unsigned long long>(context->frameId),
                    context->slotIndex,
                    waitResult,
                    static_cast<unsigned long long>(waitAttempts));
            }
            if (waitResult != VK_TIMEOUT) {
                Sleep(100);
            }
        }
    }
    const auto waitDone = std::chrono::steady_clock::now();
    const uint32_t fenceWaitMicroseconds = elapsedMicroseconds(
        waitStart, waitDone);

    AlvrIosurfaceFrame frame{};
    frame.frameId = context->frameId;
    frame.videoTimestampNs = context->videoTimestampNs;
    frame.slotIndex = context->slotIndex;
    frame.generation = context->generation;
    frame.flags = (context->pose.fallback ? kFrameFlagFallbackPose : 0) |
                  (context->consumerSample ? kFrameFlagConsumerSample : 0);
    frame.surfaceId = state->slots[context->slotIndex].surfaceId;
    frame.width = state->width;
    frame.height = state->height;
    frame.sampleX = context->sample.x;
    frame.sampleY = context->sample.y;
    std::memcpy(
        frame.expectedBgra, context->sample.bgra.data(), context->sample.bgra.size());
    frame.poseTimestampNs = context->pose.timestampNs;
    frame.poseGeneration = context->pose.generation;
    std::memcpy(frame.pose, context->pose.matrix.data(), sizeof(frame.pose));

    AlvrIosurfaceReleaseResult release{};
    LONG frameStatus = static_cast<LONG>(0xc0000001);
    bool sendTurn = false;
    if (!context->cancelled && waitResult == VK_SUCCESS) {
        std::unique_lock<std::mutex> lock(state->mutex);
        sendTurn = state->sendCondition.wait_for(
            lock,
            kPoolWorkerOrderTimeout,
            [&]() {
                return state->failed ||
                       state->closing ||
                       state->nextFrameToSend == context->frameId;
            });
        sendTurn = sendTurn && !state->failed && !state->closing &&
                   state->nextFrameToSend == context->frameId;
    }
    if (sendTurn) {
        frameStatus = state->frameReady(
            state->serviceName.c_str(),
            state->sessionNonce,
            &frame,
            &release);
    }
    if (context->fence != VK_NULL_HANDLE) {
        state->destroyFence(state->device, context->fence, nullptr);
        context->fence = VK_NULL_HANDLE;
    }

    bool recycled = false;
    bool closed = false;
    bool released = false;
    bool releaseDropped = false;
    uint64_t releasedFrames = 0;
    {
        std::lock_guard<std::mutex> lock(state->mutex);
        PoolState::Slot &slot = state->slots[context->slotIndex];
        const bool releasePass =
            release.status == ALVR_IOSURFACE_PROBE_PASS;
        const bool releaseClosed =
            release.status == ALVR_IOSURFACE_PROBE_SESSION_CLOSED;
        releaseDropped =
            release.status == ALVR_IOSURFACE_PROBE_FRAME_DROPPED;
        if (state->nextFrameToSend == context->frameId) {
            ++state->nextFrameToSend;
        }
        if (!context->cancelled && waitResult == VK_SUCCESS && sendTurn &&
            frameStatus == 0 &&
            (releasePass || releaseClosed || releaseDropped) &&
            slot.generation == context->generation &&
            slot.status == PoolState::SlotStatus::AwaitingRelease) {
            slot.status = PoolState::SlotStatus::Available;
            releasedFrames = ++state->releasedFrames;
            released = true;
            recycled = releasePass || releaseDropped;
            closed = releaseClosed;
            if (closed) {
                state->closing = true;
            }
        } else {
            slot.status = PoolState::SlotStatus::Available;
            state->failed = state->failed || !state->closing;
        }
        --state->activeWorkers;
        state->sendCondition.notify_all();
        state->workerCondition.notify_all();
    }

    context->logFunction(
        "iosurface pool release frame_id=%llu slot=%u generation=%u "
        "source=%ux%u transfer=%s "
        "wait_result=%d wait_us=%u ipc_status=0x%08lx release_status=%u "
        "consumer_pid=%u actual_bgra=%u,%u,%u,%u released=%u recycled=%u "
        "released_total=%llu "
        "result=%s",
        static_cast<unsigned long long>(context->frameId),
        context->slotIndex,
        context->generation,
        context->sourceWidth,
        context->sourceHeight,
        poolTransferModeName(context->transferMode),
        waitResult,
        fenceWaitMicroseconds,
        static_cast<unsigned long>(frameStatus),
        release.status,
        release.consumerPid,
        release.actualBgra[0],
        release.actualBgra[1],
        release.actualBgra[2],
        release.actualBgra[3],
        released ? 1 : 0,
        recycled ? 1 : 0,
        static_cast<unsigned long long>(releasedFrames),
        !released
            ? "fail"
            : (releaseDropped
                   ? "dropped"
                   : (recycled ? "pass" : (closed ? "closed" : "fail"))));
}

DWORD WINAPI DxvkIosurfaceSubmitProof::poolWorkerEntry(void *parameter) {
    auto *context = static_cast<PoolWorkerContext *>(parameter);
    const HMODULE moduleReference = context->moduleReference;
    const DWORD startResult = WaitForSingleObject(context->startEvent, INFINITE);
    if (startResult != WAIT_OBJECT_0) {
        context->cancelled = true;
        context->logFunction(
            "iosurface pool failed reason=worker-start-wait win32_result=%lu",
            static_cast<unsigned long>(startResult));
    }
    runPoolWorker(context);
    delete context;
    FreeLibraryAndExitThread(moduleReference, 0);
    return 0;
}

void DxvkIosurfaceSubmitProof::runWorker(
    std::shared_ptr<ProofState> state,
    SubmitProofLogFunction logFunction) {
    const auto fenceStart = std::chrono::steady_clock::now();
    const VkResult waitResult = state->waitForFences(
        state->device,
        1,
        &state->fence,
        VK_TRUE,
        kPoolFenceTimeoutNanoseconds);
    const auto fenceDone = std::chrono::steady_clock::now();
    const uint32_t fenceWaitMicroseconds = elapsedMicroseconds(
        fenceStart, fenceDone);

    bool readyWritten = false;
    if (waitResult == VK_SUCCESS) {
        readyWritten = writeReadyFile(
            state->proofNonce,
            state->surfaceId,
            state->width,
            state->height,
            state->submitSequence,
            state->eye,
            state->sample,
            state->sourceFormat,
            state->sourceBindFlags,
            state->copyFlushMicroseconds,
            state->markerSubmitMicroseconds,
            fenceWaitMicroseconds);
    }

    logFunction(
        "iosurface proof fence sequence=%llu proof_nonce=%llu wait_result=%d "
        "wait_us=%u ready_written=%u ready_file=%s",
        static_cast<unsigned long long>(state->submitSequence),
        static_cast<unsigned long long>(state->proofNonce),
        waitResult,
        fenceWaitMicroseconds,
        readyWritten ? 1 : 0,
        kReadyFile);

    int consumerStatus = -1;
    bool acknowledged = false;
    const auto verifierStart = std::chrono::steady_clock::now();
    while (readyWritten) {
        if (readConsumerStatus(state->proofNonce,
                               state->submitSequence,
                               state->surfaceId,
                               &consumerStatus)) {
            acknowledged = true;
            break;
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - verifierStart);
        if (elapsed.count() >= kVerifierTimeoutMilliseconds) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    const LONG releaseStatus = state->machPort != 0
                                   ? state->releasePort(state->machPort)
                                   : 0;
    if (state->fence != VK_NULL_HANDLE) {
        state->destroyFence(state->device, state->fence, nullptr);
    }
    if (state->bridgeModule != nullptr) {
        FreeLibrary(state->bridgeModule);
    }
    if (state->vulkanModule != nullptr) {
        FreeLibrary(state->vulkanModule);
    }

    logFunction(
        "iosurface proof result sequence=%llu proof_nonce=%llu acknowledged=%u "
        "consumer_status=%d release_status=0x%08lx result=%s",
        static_cast<unsigned long long>(state->submitSequence),
        static_cast<unsigned long long>(state->proofNonce),
        acknowledged ? 1 : 0,
        consumerStatus,
        static_cast<unsigned long>(releaseStatus),
        acknowledged && consumerStatus == 0 && releaseStatus == 0 ? "pass"
                                                                 : "fail");
}

DWORD WINAPI DxvkIosurfaceSubmitProof::workerEntry(void *parameter) {
    auto *workerContext = static_cast<WorkerContext *>(parameter);
    const HMODULE moduleReference = workerContext->moduleReference;
    {
        std::shared_ptr<ProofState> state = std::move(workerContext->state);
        const SubmitProofLogFunction logFunction = workerContext->logFunction;
        delete workerContext;
        runWorker(std::move(state), logFunction);
    }
    FreeLibraryAndExitThread(moduleReference, 0);
    return 0;
}

}  // namespace alvr_probe
