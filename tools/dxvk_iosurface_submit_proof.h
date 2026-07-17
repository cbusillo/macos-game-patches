#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>

#include <array>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>

namespace alvr_probe {

using SubmitProofLogFunction = void (*)(const char *, ...);

struct SubmitProofSample {
    uint32_t x = 0;
    uint32_t y = 0;
    std::array<uint8_t, 4> bgra{};
};

struct SubmitProofPose {
    uint64_t timestampNs = 0;
    uint64_t generation = 0;
    uint64_t sourceSessionId = 0;
    uint64_t sourceGeneration = 0;
    std::array<float, 12> matrix{};
    bool fallback = false;
};

class DxvkIosurfaceSubmitProof {
  public:
    explicit DxvkIosurfaceSubmitProof(SubmitProofLogFunction logFunction);
    ~DxvkIosurfaceSubmitProof();

    DxvkIosurfaceSubmitProof(const DxvkIosurfaceSubmitProof &) = delete;
    DxvkIosurfaceSubmitProof &operator=(
        const DxvkIosurfaceSubmitProof &) = delete;

    bool pending();
    bool usesPool();
    void shutdown();

    void capturePoolFrame(ID3D11Texture2D *sourceTexture,
                          uint64_t submitSequence,
                          uint32_t eye,
                          uint64_t videoTimestampNs,
                          const SubmitProofPose &pose);

    void capturePoolFramePair(ID3D11Texture2D *leftTexture,
                              ID3D11Texture2D *rightTexture,
                              uint64_t submitSequence,
                              uint64_t videoTimestampNs,
                              const SubmitProofPose &pose);

    void captureOnce(ID3D11Texture2D *sourceTexture,
                     uint64_t submitSequence,
                     uint32_t eye,
                     const SubmitProofSample &sample,
                     uint64_t videoTimestampNs = 0);

  private:
    struct ProofState;
    struct WorkerContext;
    struct PoolState;
    struct PoolInitializationState;
    struct PoolInitializationContext;
    struct PoolWorkerContext;

    static void runWorker(std::shared_ptr<ProofState> state,
                          SubmitProofLogFunction logFunction);
    static DWORD WINAPI workerEntry(void *parameter);
    void startPoolInitialization(
        ID3D11Texture2D *sourceTexture,
        const D3D11_TEXTURE2D_DESC &sourceDescription);
    void collectPoolInitialization();
    static void runPoolInitialization(PoolInitializationContext *context);
    static DWORD WINAPI poolInitializationEntry(void *parameter);
    void capturePoolSources(ID3D11Texture2D *leftTexture,
                            ID3D11Texture2D *rightTexture,
                            uint64_t submitSequence,
                            uint32_t eye,
                            uint64_t videoTimestampNs,
                            const SubmitProofPose &pose);
    static std::shared_ptr<PoolState> initializePool(
        ID3D11Texture2D *sourceTexture,
        const D3D11_TEXTURE2D_DESC &sourceDescription,
        uint32_t outputWidth,
        uint32_t outputHeight,
        const std::string &serviceName,
        uint64_t sessionNonce,
        SubmitProofLogFunction logFunction);
    static void runPoolWorker(PoolWorkerContext *context);
    static DWORD WINAPI poolWorkerEntry(void *parameter);

    SubmitProofLogFunction logFunction_;
    std::mutex mutex_;
    std::condition_variable poolInitializationCondition_;
    bool attempted_ = false;
    bool poolConfigured_ = false;
    bool poolInitializationFailed_ = false;
    bool shutdownInProgress_ = false;
    bool shutdownComplete_ = false;
    uint64_t poolNonce_ = 0;
    uint32_t poolWidth_ = 0;
    uint32_t poolHeight_ = 0;
    std::string poolService_;
    std::shared_ptr<PoolState> poolState_;
    std::shared_ptr<PoolInitializationState> poolInitialization_;
};

}  // namespace alvr_probe
