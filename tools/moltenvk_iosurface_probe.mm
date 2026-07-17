#define VK_NO_PROTOTYPES
#define VK_USE_PLATFORM_METAL_EXT

#include <vulkan/vulkan.h>
#include <vulkan/vulkan_metal.h>

#import <Foundation/Foundation.h>
#import <IOSurface/IOSurface.h>
#import <Metal/Metal.h>

#include <dlfcn.h>
#include <mach/mach.h>
#include <mach/machine.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <sys/sysctl.h>
#include <sys/wait.h>

#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

extern char **environ;

namespace {

constexpr std::string_view kDefaultMoltenVKPath =
    "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/"
    "libMoltenVK.dylib";
constexpr uint32_t kImageWidth = 64;
constexpr uint32_t kImageHeight = 64;
constexpr std::array<uint8_t, 4> kExpectedBgra = {0, 0, 255, 255};

using Clock = std::chrono::steady_clock;

double elapsedMilliseconds(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

std::string architectureName() {
#if defined(__arm64__)
    return "arm64";
#elif defined(__x86_64__)
    return "x86_64";
#else
    return "unknown";
#endif
}

bool isTranslated() {
    int translated = 0;
    size_t translatedSize = sizeof(translated);
    return sysctlbyname("sysctl.proc_translated", &translated, &translatedSize, nullptr, 0) == 0 &&
           translated == 1;
}

std::string vkVersion(uint32_t version) {
    std::ostringstream stream;
    stream << VK_VERSION_MAJOR(version) << '.' << VK_VERSION_MINOR(version) << '.'
           << VK_VERSION_PATCH(version);
    return stream.str();
}

void requireVk(VkResult result, std::string_view operation) {
    if (result == VK_SUCCESS) {
        return;
    }

    std::ostringstream message;
    message << operation << " failed with VkResult " << result;
    throw std::runtime_error(message.str());
}

template <typename Function>
Function requireInstanceFunction(PFN_vkGetInstanceProcAddr getInstanceProcAddr,
                                 VkInstance instance,
                                 const char *name) {
    auto function = reinterpret_cast<Function>(getInstanceProcAddr(instance, name));
    if (function == nullptr) {
        throw std::runtime_error(std::string("missing Vulkan function: ") + name);
    }
    return function;
}

template <typename Function>
Function requireDeviceFunction(PFN_vkGetDeviceProcAddr getDeviceProcAddr,
                               VkDevice device,
                               const char *name) {
    auto function = reinterpret_cast<Function>(getDeviceProcAddr(device, name));
    if (function == nullptr) {
        throw std::runtime_error(std::string("missing Vulkan device function: ") + name);
    }
    return function;
}

bool hasExtension(const std::vector<VkExtensionProperties> &extensions,
                  std::string_view name) {
    for (const auto &extension : extensions) {
        if (name == extension.extensionName) {
            return true;
        }
    }
    return false;
}

uint32_t parseUnsigned(std::string_view value, std::string_view label) {
    size_t consumed = 0;
    const auto parsed = std::stoull(std::string(value), &consumed, 10);
    if (consumed != value.size() || parsed > UINT32_MAX) {
        throw std::runtime_error(std::string("invalid ") + std::string(label));
    }
    return static_cast<uint32_t>(parsed);
}

std::string executablePath() {
    uint32_t pathSize = 0;
    _NSGetExecutablePath(nullptr, &pathSize);
    std::vector<char> path(pathSize);
    if (_NSGetExecutablePath(path.data(), &pathSize) != 0) {
        throw std::runtime_error("failed to resolve executable path");
    }
    return std::filesystem::canonical(path.data()).string();
}

struct PixelReadResult {
    std::array<uint8_t, 4> bgra{};
    double deviceSetupMilliseconds = 0.0;
    double textureImportMilliseconds = 0.0;
    double blitWaitMilliseconds = 0.0;
    double importAndReadMilliseconds = 0.0;
};

PixelReadResult readPixelWithMetal(IOSurfaceRef surface,
                                   uint32_t sourceX = 0,
                                   uint32_t sourceY = 0) {
    const auto start = Clock::now();
    const size_t width = IOSurfaceGetWidth(surface);
    const size_t height = IOSurfaceGetHeight(surface);
    if (sourceX >= width || sourceY >= height) {
        throw std::runtime_error("Metal sample coordinate exceeds IOSurface bounds");
    }

    const auto deviceStart = Clock::now();
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        throw std::runtime_error("Metal did not return a system device");
    }
    const double deviceSetupMilliseconds = elapsedMilliseconds(deviceStart);

    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                            width:width
                                                           height:height
                                                        mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead;

    const auto textureImportStart = Clock::now();
    id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor
                                                    iosurface:surface
                                                        plane:0];
    if (texture == nil) {
        throw std::runtime_error("Metal failed to create a texture from the IOSurface");
    }
    const double textureImportMilliseconds =
        elapsedMilliseconds(textureImportStart);

    id<MTLCommandQueue> commandQueue = [device newCommandQueue];
    id<MTLBuffer> readbackBuffer =
        [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> commandBuffer = [commandQueue commandBuffer];
    id<MTLBlitCommandEncoder> blitEncoder = [commandBuffer blitCommandEncoder];
    if (commandQueue == nil || readbackBuffer == nil || commandBuffer == nil ||
        blitEncoder == nil) {
        throw std::runtime_error("Metal failed to allocate readback resources");
    }

    [blitEncoder copyFromTexture:texture
                     sourceSlice:0
                     sourceLevel:0
                    sourceOrigin:MTLOriginMake(sourceX, sourceY, 0)
                      sourceSize:MTLSizeMake(1, 1, 1)
                        toBuffer:readbackBuffer
               destinationOffset:0
          destinationBytesPerRow:256
        destinationBytesPerImage:256];
    [blitEncoder endEncoding];
    const auto blitStart = Clock::now();
    [commandBuffer commit];
    [commandBuffer waitUntilCompleted];
    const double blitWaitMilliseconds = elapsedMilliseconds(blitStart);

    if (commandBuffer.status == MTLCommandBufferStatusError) {
        const char *description = commandBuffer.error.localizedDescription.UTF8String;
        throw std::runtime_error(std::string("Metal blit failed: ") +
                                 (description != nullptr ? description : "unknown error"));
    }

    PixelReadResult result;
    std::memcpy(result.bgra.data(), readbackBuffer.contents, result.bgra.size());
    result.deviceSetupMilliseconds = deviceSetupMilliseconds;
    result.textureImportMilliseconds = textureImportMilliseconds;
    result.blitWaitMilliseconds = blitWaitMilliseconds;
    result.importAndReadMilliseconds = elapsedMilliseconds(start);
    return result;
}

bool pixelMatches(const std::array<uint8_t, 4> &actual,
                  const std::array<uint8_t, 4> &expected) {
    return actual == expected;
}

void printPixel(std::string_view label, const std::array<uint8_t, 4> &pixel) {
    std::cout << label << '=' << static_cast<unsigned>(pixel[0]) << ','
              << static_cast<unsigned>(pixel[1]) << ','
              << static_cast<unsigned>(pixel[2]) << ','
              << static_cast<unsigned>(pixel[3]);
}

int runConsumer(int argumentCount, char **arguments) {
    if (argumentCount != 9 && argumentCount != 11) {
        throw std::runtime_error(
            "consumer usage: --consume-iosurface ID WIDTH HEIGHT [X Y] B G R A");
    }

    const auto surfaceId = parseUnsigned(arguments[2], "IOSurface ID");
    const auto expectedWidth = parseUnsigned(arguments[3], "width");
    const auto expectedHeight = parseUnsigned(arguments[4], "height");
    const bool hasCoordinate = argumentCount == 11;
    const auto sourceX = hasCoordinate ? parseUnsigned(arguments[5], "sample x") : 0;
    const auto sourceY = hasCoordinate ? parseUnsigned(arguments[6], "sample y") : 0;
    const int pixelArgument = hasCoordinate ? 7 : 5;
    std::array<uint8_t, 4> expectedPixel{};
    for (size_t component = 0; component < expectedPixel.size(); ++component) {
        const auto value = parseUnsigned(
            arguments[pixelArgument + static_cast<int>(component)], "pixel component");
        if (value > UINT8_MAX) {
            throw std::runtime_error("pixel component exceeds 255");
        }
        expectedPixel[component] = static_cast<uint8_t>(value);
    }

    const auto lookupStart = Clock::now();
    IOSurfaceRef surface = IOSurfaceLookup(surfaceId);
    const double lookupMilliseconds = elapsedMilliseconds(lookupStart);
    if (surface == nullptr) {
        throw std::runtime_error("IOSurfaceLookup returned null");
    }

    const size_t actualWidth = IOSurfaceGetWidth(surface);
    const size_t actualHeight = IOSurfaceGetHeight(surface);
    if (actualWidth != expectedWidth || actualHeight != expectedHeight) {
        CFRelease(surface);
        throw std::runtime_error("consumer IOSurface dimensions did not match producer");
    }

    const PixelReadResult pixelResult = readPixelWithMetal(surface, sourceX, sourceY);
    CFRelease(surface);

    const bool passed = pixelMatches(pixelResult.bgra, expectedPixel) &&
                        architectureName() == "arm64";
    std::cout << "PROBE consumer architecture=" << architectureName()
              << " translated=" << (isTranslated() ? "true" : "false")
              << " iosurface_id=" << surfaceId
              << " sample_x=" << sourceX
              << " sample_y=" << sourceY
              << " lookup_ms=" << lookupMilliseconds
              << " device_setup_ms=" << pixelResult.deviceSetupMilliseconds
              << " texture_import_ms=" << pixelResult.textureImportMilliseconds
              << " blit_wait_ms=" << pixelResult.blitWaitMilliseconds
              << " import_read_ms=" << pixelResult.importAndReadMilliseconds << ' ';
    printPixel("actual_bgra", pixelResult.bgra);
    std::cout << ' ';
    printPixel("expected_bgra", expectedPixel);
    std::cout << " result=" << (passed ? "pass" : "fail") << '\n';
    return passed ? 0 : 2;
}

struct VulkanState {
    void *library = nullptr;
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkCommandPool commandPool = VK_NULL_HANDLE;
    PFN_vkDestroyInstance destroyInstance = nullptr;
    PFN_vkDestroyDevice destroyDevice = nullptr;
    PFN_vkDestroyImage destroyImage = nullptr;
    PFN_vkFreeMemory freeMemory = nullptr;
    PFN_vkDestroyCommandPool destroyCommandPool = nullptr;

    ~VulkanState() {
        if (device != VK_NULL_HANDLE) {
            if (commandPool != VK_NULL_HANDLE && destroyCommandPool != nullptr) {
                destroyCommandPool(device, commandPool, nullptr);
            }
            if (image != VK_NULL_HANDLE && destroyImage != nullptr) {
                destroyImage(device, image, nullptr);
            }
            if (memory != VK_NULL_HANDLE && freeMemory != nullptr) {
                freeMemory(device, memory, nullptr);
            }
            if (destroyDevice != nullptr) {
                destroyDevice(device, nullptr);
            }
        }
        if (instance != VK_NULL_HANDLE && destroyInstance != nullptr) {
            destroyInstance(instance, nullptr);
        }
        if (library != nullptr) {
            dlclose(library);
        }
    }
};

uint32_t chooseMemoryType(uint32_t allowedTypes,
                          const VkPhysicalDeviceMemoryProperties &memoryProperties) {
    for (uint32_t index = 0; index < memoryProperties.memoryTypeCount; ++index) {
        const bool allowed = (allowedTypes & (1u << index)) != 0;
        const bool deviceLocal =
            (memoryProperties.memoryTypes[index].propertyFlags &
             VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) != 0;
        if (allowed && deviceLocal) {
            return index;
        }
    }

    for (uint32_t index = 0; index < memoryProperties.memoryTypeCount; ++index) {
        if ((allowedTypes & (1u << index)) != 0) {
            return index;
        }
    }
    throw std::runtime_error("no compatible Vulkan memory type");
}

int spawnArm64Consumer(IOSurfaceID surfaceId,
                       uint32_t width,
                       uint32_t height,
                       const std::array<uint8_t, 4> &expectedPixel) {
    const std::string binary = executablePath();
    const std::string surfaceIdArgument = std::to_string(surfaceId);
    const std::string widthArgument = std::to_string(width);
    const std::string heightArgument = std::to_string(height);
    std::array<std::string, 4> pixelArguments;
    for (size_t component = 0; component < expectedPixel.size(); ++component) {
        pixelArguments[component] = std::to_string(expectedPixel[component]);
    }

    std::array<char *, 10> childArguments = {
        const_cast<char *>(binary.c_str()),
        const_cast<char *>("--consume-iosurface"),
        const_cast<char *>(surfaceIdArgument.c_str()),
        const_cast<char *>(widthArgument.c_str()),
        const_cast<char *>(heightArgument.c_str()),
        const_cast<char *>(pixelArguments[0].c_str()),
        const_cast<char *>(pixelArguments[1].c_str()),
        const_cast<char *>(pixelArguments[2].c_str()),
        const_cast<char *>(pixelArguments[3].c_str()),
        nullptr,
    };

    posix_spawnattr_t spawnAttributes;
    if (posix_spawnattr_init(&spawnAttributes) != 0) {
        throw std::runtime_error("posix_spawnattr_init failed");
    }

    cpu_type_t architecturePreference = CPU_TYPE_ARM64;
    size_t selectedArchitectures = 0;
    const int preferenceResult = posix_spawnattr_setbinpref_np(
        &spawnAttributes, 1, &architecturePreference, &selectedArchitectures);
    if (preferenceResult != 0 || selectedArchitectures != 1) {
        posix_spawnattr_destroy(&spawnAttributes);
        throw std::runtime_error("failed to select the arm64 binary slice");
    }

    pid_t childProcess = 0;
    const int spawnResult = posix_spawn(&childProcess,
                                        binary.c_str(),
                                        nullptr,
                                        &spawnAttributes,
                                        childArguments.data(),
                                        environ);
    posix_spawnattr_destroy(&spawnAttributes);
    if (spawnResult != 0) {
        throw std::runtime_error(std::string("posix_spawn failed: ") +
                                 std::strerror(spawnResult));
    }

    int childStatus = 0;
    if (waitpid(childProcess, &childStatus, 0) < 0) {
        throw std::runtime_error("waitpid failed");
    }
    if (!WIFEXITED(childStatus)) {
        throw std::runtime_error("arm64 consumer terminated abnormally");
    }
    return WEXITSTATUS(childStatus);
}

int runProducer(std::string_view moltenVKPath) {
    if (architectureName() != "x86_64") {
        throw std::runtime_error("producer must run as x86_64");
    }

    VulkanState state;
    state.library = dlopen(std::string(moltenVKPath).c_str(), RTLD_NOW | RTLD_LOCAL);
    if (state.library == nullptr) {
        throw std::runtime_error(std::string("dlopen failed: ") + dlerror());
    }

    auto getInstanceProcAddr = reinterpret_cast<PFN_vkGetInstanceProcAddr>(
        dlsym(state.library, "vkGetInstanceProcAddr"));
    if (getInstanceProcAddr == nullptr) {
        throw std::runtime_error("MoltenVK did not export vkGetInstanceProcAddr");
    }

    const auto enumerateInstanceExtensions =
        requireInstanceFunction<PFN_vkEnumerateInstanceExtensionProperties>(
            getInstanceProcAddr, VK_NULL_HANDLE, "vkEnumerateInstanceExtensionProperties");
    const auto createInstance = requireInstanceFunction<PFN_vkCreateInstance>(
        getInstanceProcAddr, VK_NULL_HANDLE, "vkCreateInstance");
    const auto enumerateInstanceVersion = reinterpret_cast<PFN_vkEnumerateInstanceVersion>(
        getInstanceProcAddr(VK_NULL_HANDLE, "vkEnumerateInstanceVersion"));

    uint32_t instanceExtensionCount = 0;
    requireVk(enumerateInstanceExtensions(nullptr, &instanceExtensionCount, nullptr),
              "vkEnumerateInstanceExtensionProperties(count)");
    std::vector<VkExtensionProperties> instanceExtensions(instanceExtensionCount);
    requireVk(enumerateInstanceExtensions(
                  nullptr, &instanceExtensionCount, instanceExtensions.data()),
              "vkEnumerateInstanceExtensionProperties(list)");

    const bool hasPortabilityEnumeration =
        hasExtension(instanceExtensions, VK_KHR_PORTABILITY_ENUMERATION_EXTENSION_NAME);
    std::vector<const char *> enabledInstanceExtensions;
    if (hasPortabilityEnumeration) {
        enabledInstanceExtensions.push_back(VK_KHR_PORTABILITY_ENUMERATION_EXTENSION_NAME);
    }

    uint32_t supportedInstanceVersion = VK_API_VERSION_1_0;
    if (enumerateInstanceVersion != nullptr) {
        requireVk(enumerateInstanceVersion(&supportedInstanceVersion),
                  "vkEnumerateInstanceVersion");
    }

    VkApplicationInfo applicationInfo{};
    applicationInfo.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
    applicationInfo.pApplicationName = "MoltenVK IOSurface probe";
    applicationInfo.applicationVersion = VK_MAKE_VERSION(1, 0, 0);
    applicationInfo.pEngineName = "none";
    applicationInfo.engineVersion = VK_MAKE_VERSION(1, 0, 0);
    applicationInfo.apiVersion =
        supportedInstanceVersion >= VK_API_VERSION_1_1 ? VK_API_VERSION_1_1
                                                       : VK_API_VERSION_1_0;

    VkInstanceCreateInfo instanceInfo{};
    instanceInfo.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
    instanceInfo.flags = hasPortabilityEnumeration
                             ? VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR
                             : 0;
    instanceInfo.pApplicationInfo = &applicationInfo;
    instanceInfo.enabledExtensionCount =
        static_cast<uint32_t>(enabledInstanceExtensions.size());
    instanceInfo.ppEnabledExtensionNames = enabledInstanceExtensions.data();
    requireVk(createInstance(&instanceInfo, nullptr, &state.instance), "vkCreateInstance");

    state.destroyInstance = requireInstanceFunction<PFN_vkDestroyInstance>(
        getInstanceProcAddr, state.instance, "vkDestroyInstance");
    const auto enumeratePhysicalDevices =
        requireInstanceFunction<PFN_vkEnumeratePhysicalDevices>(
            getInstanceProcAddr, state.instance, "vkEnumeratePhysicalDevices");
    const auto getPhysicalDeviceProperties =
        requireInstanceFunction<PFN_vkGetPhysicalDeviceProperties>(
            getInstanceProcAddr, state.instance, "vkGetPhysicalDeviceProperties");
    const auto getPhysicalDeviceMemoryProperties =
        requireInstanceFunction<PFN_vkGetPhysicalDeviceMemoryProperties>(
            getInstanceProcAddr, state.instance, "vkGetPhysicalDeviceMemoryProperties");
    const auto getQueueFamilyProperties =
        requireInstanceFunction<PFN_vkGetPhysicalDeviceQueueFamilyProperties>(
            getInstanceProcAddr, state.instance, "vkGetPhysicalDeviceQueueFamilyProperties");
    const auto enumerateDeviceExtensions =
        requireInstanceFunction<PFN_vkEnumerateDeviceExtensionProperties>(
            getInstanceProcAddr, state.instance, "vkEnumerateDeviceExtensionProperties");
    const auto createDevice = requireInstanceFunction<PFN_vkCreateDevice>(
        getInstanceProcAddr, state.instance, "vkCreateDevice");
    const auto getDeviceProcAddr = requireInstanceFunction<PFN_vkGetDeviceProcAddr>(
        getInstanceProcAddr, state.instance, "vkGetDeviceProcAddr");

    uint32_t physicalDeviceCount = 0;
    requireVk(enumeratePhysicalDevices(state.instance, &physicalDeviceCount, nullptr),
              "vkEnumeratePhysicalDevices(count)");
    if (physicalDeviceCount == 0) {
        throw std::runtime_error("MoltenVK reported no physical devices");
    }
    std::vector<VkPhysicalDevice> physicalDevices(physicalDeviceCount);
    requireVk(enumeratePhysicalDevices(
                  state.instance, &physicalDeviceCount, physicalDevices.data()),
              "vkEnumeratePhysicalDevices(list)");
    const VkPhysicalDevice physicalDevice = physicalDevices.front();

    VkPhysicalDeviceProperties deviceProperties{};
    getPhysicalDeviceProperties(physicalDevice, &deviceProperties);

    uint32_t queueFamilyCount = 0;
    getQueueFamilyProperties(physicalDevice, &queueFamilyCount, nullptr);
    std::vector<VkQueueFamilyProperties> queueFamilies(queueFamilyCount);
    getQueueFamilyProperties(physicalDevice, &queueFamilyCount, queueFamilies.data());
    uint32_t queueFamilyIndex = UINT32_MAX;
    for (uint32_t index = 0; index < queueFamilyCount; ++index) {
        if ((queueFamilies[index].queueFlags & VK_QUEUE_GRAPHICS_BIT) != 0) {
            queueFamilyIndex = index;
            break;
        }
    }
    if (queueFamilyIndex == UINT32_MAX) {
        throw std::runtime_error("MoltenVK reported no graphics queue family");
    }

    uint32_t deviceExtensionCount = 0;
    requireVk(enumerateDeviceExtensions(
                  physicalDevice, nullptr, &deviceExtensionCount, nullptr),
              "vkEnumerateDeviceExtensionProperties(count)");
    std::vector<VkExtensionProperties> deviceExtensions(deviceExtensionCount);
    requireVk(enumerateDeviceExtensions(physicalDevice,
                                        nullptr,
                                        &deviceExtensionCount,
                                        deviceExtensions.data()),
              "vkEnumerateDeviceExtensionProperties(list)");
    if (!hasExtension(deviceExtensions, VK_EXT_METAL_OBJECTS_EXTENSION_NAME)) {
        throw std::runtime_error("MoltenVK did not advertise VK_EXT_metal_objects");
    }

    std::vector<const char *> enabledDeviceExtensions = {
        VK_EXT_METAL_OBJECTS_EXTENSION_NAME,
    };
    constexpr const char *portabilitySubset = "VK_KHR_portability_subset";
    if (hasExtension(deviceExtensions, portabilitySubset)) {
        enabledDeviceExtensions.push_back(portabilitySubset);
    }

    const float queuePriority = 1.0f;
    VkDeviceQueueCreateInfo queueInfo{};
    queueInfo.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
    queueInfo.queueFamilyIndex = queueFamilyIndex;
    queueInfo.queueCount = 1;
    queueInfo.pQueuePriorities = &queuePriority;

    VkDeviceCreateInfo deviceInfo{};
    deviceInfo.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
    deviceInfo.queueCreateInfoCount = 1;
    deviceInfo.pQueueCreateInfos = &queueInfo;
    deviceInfo.enabledExtensionCount =
        static_cast<uint32_t>(enabledDeviceExtensions.size());
    deviceInfo.ppEnabledExtensionNames = enabledDeviceExtensions.data();
    requireVk(createDevice(physicalDevice, &deviceInfo, nullptr, &state.device),
              "vkCreateDevice");

    state.destroyDevice = requireDeviceFunction<PFN_vkDestroyDevice>(
        getDeviceProcAddr, state.device, "vkDestroyDevice");
    const auto getDeviceQueue = requireDeviceFunction<PFN_vkGetDeviceQueue>(
        getDeviceProcAddr, state.device, "vkGetDeviceQueue");
    const auto createImage = requireDeviceFunction<PFN_vkCreateImage>(
        getDeviceProcAddr, state.device, "vkCreateImage");
    state.destroyImage = requireDeviceFunction<PFN_vkDestroyImage>(
        getDeviceProcAddr, state.device, "vkDestroyImage");
    const auto getImageMemoryRequirements =
        requireDeviceFunction<PFN_vkGetImageMemoryRequirements>(
            getDeviceProcAddr, state.device, "vkGetImageMemoryRequirements");
    const auto allocateMemory = requireDeviceFunction<PFN_vkAllocateMemory>(
        getDeviceProcAddr, state.device, "vkAllocateMemory");
    state.freeMemory = requireDeviceFunction<PFN_vkFreeMemory>(
        getDeviceProcAddr, state.device, "vkFreeMemory");
    const auto bindImageMemory = requireDeviceFunction<PFN_vkBindImageMemory>(
        getDeviceProcAddr, state.device, "vkBindImageMemory");
    const auto createCommandPool = requireDeviceFunction<PFN_vkCreateCommandPool>(
        getDeviceProcAddr, state.device, "vkCreateCommandPool");
    state.destroyCommandPool = requireDeviceFunction<PFN_vkDestroyCommandPool>(
        getDeviceProcAddr, state.device, "vkDestroyCommandPool");
    const auto allocateCommandBuffers =
        requireDeviceFunction<PFN_vkAllocateCommandBuffers>(
            getDeviceProcAddr, state.device, "vkAllocateCommandBuffers");
    const auto beginCommandBuffer = requireDeviceFunction<PFN_vkBeginCommandBuffer>(
        getDeviceProcAddr, state.device, "vkBeginCommandBuffer");
    const auto endCommandBuffer = requireDeviceFunction<PFN_vkEndCommandBuffer>(
        getDeviceProcAddr, state.device, "vkEndCommandBuffer");
    const auto commandPipelineBarrier =
        requireDeviceFunction<PFN_vkCmdPipelineBarrier>(
            getDeviceProcAddr, state.device, "vkCmdPipelineBarrier");
    const auto commandClearColorImage =
        requireDeviceFunction<PFN_vkCmdClearColorImage>(
            getDeviceProcAddr, state.device, "vkCmdClearColorImage");
    const auto queueSubmit = requireDeviceFunction<PFN_vkQueueSubmit>(
        getDeviceProcAddr, state.device, "vkQueueSubmit");
    const auto queueWaitIdle = requireDeviceFunction<PFN_vkQueueWaitIdle>(
        getDeviceProcAddr, state.device, "vkQueueWaitIdle");
    const auto exportMetalObjects =
        requireDeviceFunction<PFN_vkExportMetalObjectsEXT>(
            getDeviceProcAddr, state.device, "vkExportMetalObjectsEXT");

    VkQueue queue = VK_NULL_HANDLE;
    getDeviceQueue(state.device, queueFamilyIndex, 0, &queue);

    VkExportMetalObjectCreateInfoEXT exportIntent{};
    exportIntent.sType = VK_STRUCTURE_TYPE_EXPORT_METAL_OBJECT_CREATE_INFO_EXT;
    exportIntent.exportObjectType =
        VK_EXPORT_METAL_OBJECT_TYPE_METAL_IOSURFACE_BIT_EXT;

    VkImageCreateInfo imageInfo{};
    imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
    imageInfo.pNext = &exportIntent;
    imageInfo.imageType = VK_IMAGE_TYPE_2D;
    imageInfo.format = VK_FORMAT_B8G8R8A8_UNORM;
    imageInfo.extent = {kImageWidth, kImageHeight, 1};
    imageInfo.mipLevels = 1;
    imageInfo.arrayLayers = 1;
    imageInfo.samples = VK_SAMPLE_COUNT_1_BIT;
    imageInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
    imageInfo.usage =
        VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
        VK_IMAGE_USAGE_SAMPLED_BIT;
    imageInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    imageInfo.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    requireVk(createImage(state.device, &imageInfo, nullptr, &state.image),
              "vkCreateImage");

    VkMemoryRequirements memoryRequirements{};
    getImageMemoryRequirements(state.device, state.image, &memoryRequirements);
    VkPhysicalDeviceMemoryProperties memoryProperties{};
    getPhysicalDeviceMemoryProperties(physicalDevice, &memoryProperties);

    VkMemoryAllocateInfo allocationInfo{};
    allocationInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
    allocationInfo.allocationSize = memoryRequirements.size;
    allocationInfo.memoryTypeIndex =
        chooseMemoryType(memoryRequirements.memoryTypeBits, memoryProperties);
    requireVk(allocateMemory(state.device, &allocationInfo, nullptr, &state.memory),
              "vkAllocateMemory");
    requireVk(bindImageMemory(state.device, state.image, state.memory, 0),
              "vkBindImageMemory");

    VkCommandPoolCreateInfo commandPoolInfo{};
    commandPoolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
    commandPoolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
    commandPoolInfo.queueFamilyIndex = queueFamilyIndex;
    requireVk(createCommandPool(
                  state.device, &commandPoolInfo, nullptr, &state.commandPool),
              "vkCreateCommandPool");

    VkCommandBufferAllocateInfo commandBufferInfo{};
    commandBufferInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
    commandBufferInfo.commandPool = state.commandPool;
    commandBufferInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    commandBufferInfo.commandBufferCount = 1;
    VkCommandBuffer commandBuffer = VK_NULL_HANDLE;
    requireVk(allocateCommandBuffers(state.device, &commandBufferInfo, &commandBuffer),
              "vkAllocateCommandBuffers");

    VkCommandBufferBeginInfo beginInfo{};
    beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
    beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    requireVk(beginCommandBuffer(commandBuffer, &beginInfo), "vkBeginCommandBuffer");

    VkImageSubresourceRange colorRange{};
    colorRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
    colorRange.baseMipLevel = 0;
    colorRange.levelCount = 1;
    colorRange.baseArrayLayer = 0;
    colorRange.layerCount = 1;

    VkImageMemoryBarrier prepareForClear{};
    prepareForClear.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
    prepareForClear.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    prepareForClear.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    prepareForClear.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    prepareForClear.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    prepareForClear.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    prepareForClear.image = state.image;
    prepareForClear.subresourceRange = colorRange;
    commandPipelineBarrier(commandBuffer,
                           VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                           VK_PIPELINE_STAGE_TRANSFER_BIT,
                           0,
                           0,
                           nullptr,
                           0,
                           nullptr,
                           1,
                           &prepareForClear);

    VkClearColorValue clearColor{};
    clearColor.float32[0] = 1.0f;
    clearColor.float32[1] = 0.0f;
    clearColor.float32[2] = 0.0f;
    clearColor.float32[3] = 1.0f;
    commandClearColorImage(commandBuffer,
                           state.image,
                           VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                           &clearColor,
                           1,
                           &colorRange);

    VkImageMemoryBarrier makeReadable{};
    makeReadable.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
    makeReadable.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    makeReadable.dstAccessMask = VK_ACCESS_MEMORY_READ_BIT;
    makeReadable.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    makeReadable.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    makeReadable.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    makeReadable.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    makeReadable.image = state.image;
    makeReadable.subresourceRange = colorRange;
    commandPipelineBarrier(commandBuffer,
                           VK_PIPELINE_STAGE_TRANSFER_BIT,
                           VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                           0,
                           0,
                           nullptr,
                           0,
                           nullptr,
                           1,
                           &makeReadable);
    requireVk(endCommandBuffer(commandBuffer), "vkEndCommandBuffer");

    VkSubmitInfo submitInfo{};
    submitInfo.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    submitInfo.commandBufferCount = 1;
    submitInfo.pCommandBuffers = &commandBuffer;
    const auto synchronizationStart = Clock::now();
    requireVk(queueSubmit(queue, 1, &submitInfo, VK_NULL_HANDLE), "vkQueueSubmit");
    requireVk(queueWaitIdle(queue), "vkQueueWaitIdle");
    const double synchronizationMilliseconds =
        elapsedMilliseconds(synchronizationStart);

    VkExportMetalIOSurfaceInfoEXT surfaceInfo{};
    surfaceInfo.sType = VK_STRUCTURE_TYPE_EXPORT_METAL_IO_SURFACE_INFO_EXT;
    surfaceInfo.image = state.image;
    VkExportMetalObjectsInfoEXT exportInfo{};
    exportInfo.sType = VK_STRUCTURE_TYPE_EXPORT_METAL_OBJECTS_INFO_EXT;
    exportInfo.pNext = &surfaceInfo;
    const auto exportStart = Clock::now();
    exportMetalObjects(state.device, &exportInfo);
    const double exportMilliseconds = elapsedMilliseconds(exportStart);
    if (surfaceInfo.ioSurface == nullptr) {
        throw std::runtime_error("vkExportMetalObjectsEXT returned a null IOSurface");
    }

    IOSurfaceRef exportedSurface = surfaceInfo.ioSurface;
    CFRetain(exportedSurface);
    const IOSurfaceID surfaceId = IOSurfaceGetID(exportedSurface);
    const mach_port_t surfacePort = IOSurfaceCreateMachPort(exportedSurface);
    if (surfaceId == 0 || surfacePort == MACH_PORT_NULL) {
        if (surfacePort != MACH_PORT_NULL) {
            mach_port_deallocate(mach_task_self(), surfacePort);
        }
        CFRelease(exportedSurface);
        throw std::runtime_error("IOSurface did not provide both ID and Mach port handles");
    }

    const PixelReadResult producerPixel = readPixelWithMetal(exportedSurface);
    const bool producerPixelPassed = pixelMatches(producerPixel.bgra, kExpectedBgra);

    std::cout << "PROBE producer architecture=" << architectureName()
              << " translated=" << (isTranslated() ? "true" : "false")
              << " moltenvk_path=\"" << moltenVKPath << "\""
              << " instance_api=" << vkVersion(supportedInstanceVersion)
              << " device=\"" << deviceProperties.deviceName << "\""
              << " device_api=" << vkVersion(deviceProperties.apiVersion)
              << " driver_version=" << vkVersion(deviceProperties.driverVersion)
              << " metal_objects=true"
              << " iosurface_id=" << surfaceId
              << " width=" << IOSurfaceGetWidth(exportedSurface)
              << " height=" << IOSurfaceGetHeight(exportedSurface)
              << " bytes_per_row=" << IOSurfaceGetBytesPerRow(exportedSurface)
              << " gpu_submit_wait_ms=" << synchronizationMilliseconds
              << " export_ms=" << exportMilliseconds
              << " local_device_setup_ms="
              << producerPixel.deviceSetupMilliseconds
              << " local_texture_import_ms="
              << producerPixel.textureImportMilliseconds
              << " local_blit_wait_ms="
              << producerPixel.blitWaitMilliseconds
              << " local_import_read_ms=" << producerPixel.importAndReadMilliseconds
              << " mach_port=true ";
    printPixel("local_actual_bgra", producerPixel.bgra);
    std::cout << ' ';
    printPixel("expected_bgra", kExpectedBgra);
    std::cout << " local_result=" << (producerPixelPassed ? "pass" : "fail") << '\n';

    const auto childStart = Clock::now();
    const int childExitCode =
        spawnArm64Consumer(surfaceId, kImageWidth, kImageHeight, kExpectedBgra);
    const double childMilliseconds = elapsedMilliseconds(childStart);

    mach_port_deallocate(mach_task_self(), surfacePort);
    CFRelease(exportedSurface);

    const bool passed = producerPixelPassed && childExitCode == 0;
    std::cout << "PROBE summary child_exit=" << childExitCode
              << " child_total_ms=" << childMilliseconds
              << " result=" << (passed ? "pass" : "fail") << '\n';
    return passed ? 0 : 3;
}

}  // namespace

int main(int argumentCount, char **arguments) {
    @autoreleasepool {
        try {
            if (argumentCount >= 2 &&
                std::string_view(arguments[1]) == "--consume-iosurface") {
                return runConsumer(argumentCount, arguments);
            }

            std::string_view moltenVKPath = kDefaultMoltenVKPath;
            if (argumentCount == 3 &&
                std::string_view(arguments[1]) == "--moltenvk") {
                moltenVKPath = arguments[2];
            } else if (argumentCount != 1) {
                throw std::runtime_error(
                    "usage: moltenvk_iosurface_probe [--moltenvk PATH]");
            }
            return runProducer(moltenVKPath);
        } catch (const std::exception &error) {
            std::cerr << "PROBE error architecture=" << architectureName()
                      << " message=\"" << error.what() << "\"\n";
            return 1;
        }
    }
}
