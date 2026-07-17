// Fake app-local OpenVR DLL used only to smoke-test the submit shim ABI.
//
// Build from this repo root on macOS with:
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -static-libgcc \
//     -static-libstdc++ -shared tools/fake_openvr_real.cpp \
//     -I$HOME/Developer/alvr/openvr/headers \
//     -I$HOME/Developer/alvr/alvr/server_openvr/cpp \
//     -o $PROBE_OUT/fake_openvr_real.dll

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <openvr.h>

#include "shared/alvr_shm_protocol.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdlib>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

namespace {

constexpr size_t kSystemSlots = 46;
constexpr size_t kLegacySystem011Slots = 46;
constexpr size_t kLegacySystem019Slots = 47;
constexpr size_t kCompositorSlots = 51;
constexpr size_t kLegacyCompositor013Slots = 27;
constexpr size_t kLegacyCompositor014Slots = 29;
constexpr size_t kLegacyCompositor016Slots = 35;
constexpr size_t kChaperoneSlots = 9;
constexpr size_t kChaperoneSetupSlots = 20;
constexpr size_t kOverlaySlots = 90;
constexpr size_t kLegacyOverlay013Slots = 72;
constexpr size_t kRenderModelsSlots = 19;
constexpr size_t kScreenshotsSlots = 7;
constexpr size_t kApplicationsSlots = 30;
constexpr size_t kLegacyApplications004Slots = 23;
constexpr size_t kSettingsSlots = 11;
constexpr size_t kLegacySettings001Slots = 12;
constexpr size_t kLegacyInput005Slots = 25;
constexpr size_t kLegacyInput006Slots = 26;
constexpr uint32_t kVREventPrefixSize = offsetof(vr::VREvent_t, data);
constexpr const char* kFnTablePrefix = "FnTable:";
constexpr const char* kLegacySystem011 = "IVRSystem_011";
constexpr const char* kLegacySystem012 = "IVRSystem_012";
constexpr const char* kLegacySystem019 = "IVRSystem_019";
constexpr const char* kLegacyCompositor013 = "IVRCompositor_013";
constexpr const char* kLegacyCompositor014 = "IVRCompositor_014";
constexpr const char* kLegacyCompositor016 = "IVRCompositor_016";
constexpr const char* kLegacyCompositor022 = "IVRCompositor_022";
constexpr const char* kLegacyChaperone003 = "IVRChaperone_003";
constexpr const char* kLegacyChaperoneSetup005 = "IVRChaperoneSetup_005";
constexpr const char* kLegacyOverlay010 = "IVROverlay_010";
constexpr const char* kLegacyOverlay013 = "IVROverlay_013";
constexpr const char* kLegacyOverlay018 = "IVROverlay_018";
constexpr const char* kLegacyOverlay019 = "IVROverlay_019";
constexpr const char* kLegacyRenderModels004 = "IVRRenderModels_004";
constexpr const char* kLegacyRenderModels006 = "IVRRenderModels_006";
constexpr const char* kLegacyScreenshots001 = "IVRScreenshots_001";
constexpr const char* kLegacyApplications004 = "IVRApplications_004";
constexpr const char* kLegacyApplications005 = "IVRApplications_005";
constexpr const char* kLegacySettings001 = "IVRSettings_001";
constexpr const char* kLegacyInput005 = "IVRInput_005";
constexpr const char* kLegacyInput006 = "IVRInput_006";
constexpr uint64_t kFakeRefreshRate = 90;
constexpr double kFakeRefreshHz = 90.0;
constexpr char kPacingSummaryPrefix[] = "fake pacing summary";
constexpr int64_t kMicrosecondsPerSecond = 1'000'000;
constexpr int64_t kRunningStartLeadUs = 3'000;
constexpr int64_t kSpinThresholdUs = 200;
constexpr int64_t kYieldThresholdUs = 1'000;
constexpr uint64_t kMaxBridgeHeartbeatAgeNs = 5'000'000'000ULL;
constexpr uint64_t kBridgeHeartbeatFutureToleranceNs = 250'000'000ULL;
constexpr uint64_t kMaxControllerMotionAgeNs = 1'000'000'000ULL;
constexpr auto kSceneFocusChangedEvent = static_cast<vr::EVREventType>(405);

static_assert(sizeof(vr::InputDigitalActionData_t) == 24);
static_assert(offsetof(vr::InputDigitalActionData_t, activeOrigin) == 8);
static_assert(sizeof(vr::InputAnalogActionData_t) == 48);
static_assert(offsetof(vr::InputAnalogActionData_t, activeOrigin) == 8);
static_assert(sizeof(vr::InputPoseActionData_t) == 96);
static_assert(offsetof(vr::InputPoseActionData_t, activeOrigin) == 8);

struct LegacyCompositorFrameTiming {
    uint32_t m_nSize;
    uint32_t m_nFrameIndex;
    uint32_t m_nNumFramePresents;
    uint32_t m_nNumDroppedFrames;
    double m_flSystemTimeInSeconds;
    float m_flSceneRenderGpuMs;
    float m_flTotalRenderGpuMs;
    float m_flCompositorRenderGpuMs;
    float m_flCompositorRenderCpuMs;
    float m_flCompositorIdleCpuMs;
    float m_flClientFrameIntervalMs;
    float m_flPresentCallCpuMs;
    float m_flWaitForPresentCpuMs;
    float m_flSubmitFrameMs;
    float m_flWaitGetPosesCalledMs;
    float m_flNewPosesReadyMs;
    float m_flNewFrameReadyMs;
    float m_flCompositorUpdateStartMs;
    float m_flCompositorUpdateEndMs;
    float m_flCompositorRenderStartMs;
    vr::TrackedDevicePose_t m_HmdPose;
    int32_t m_nFidelityLevel;
};

struct FakeEventSeed {
    vr::EVREventType type;
    vr::TrackedDeviceIndex_t device;
};

constexpr FakeEventSeed kStartupEvents[] = {
    { vr::VREvent_TrackedDeviceActivated, vr::k_unTrackedDeviceIndex_Hmd },
    { vr::VREvent_TrackedDeviceActivated, 1 },
    { vr::VREvent_TrackedDeviceActivated, 2 },
    { vr::VREvent_SceneApplicationChanged, vr::k_unTrackedDeviceIndex_Hmd },
    { kSceneFocusChangedEvent, vr::k_unTrackedDeviceIndex_Hmd },
    { vr::VREvent_InputFocusChanged, vr::k_unTrackedDeviceIndex_Hmd },
};

LONG g_fake_event_index = 0;
LONG g_logged_compute_distortion = 0;
LONG g_logged_legacy_compute_distortion = 0;
LONG g_logged_poll_next_event_empty = 0;
std::atomic<uint64_t> g_pose_generation { 0 };
std::atomic<uint64_t> g_shared_pose_reads { 0 };
std::atomic<uint32_t> g_tracking_space_origin { static_cast<uint32_t>(vr::TrackingUniverseStanding) };
LONG g_logged_controller_ready[ALVR_NUM_CONTROLLERS] = {};
LONG g_logged_controller_query[ALVR_NUM_CONTROLLERS] = {};
LONG g_logged_input_action_query[ALVR_NUM_CONTROLLERS] = {};
LONG g_logged_controller_button[ALVR_NUM_CONTROLLERS] = {};
LONG g_logged_controller_thumbstick[ALVR_NUM_CONTROLLERS] = {};
LONG g_logged_controller_trigger[ALVR_NUM_CONTROLLERS] = {};
HANDLE g_shared_memory_file = INVALID_HANDLE_VALUE;
HANDLE g_shared_memory_mapping = nullptr;
AlvrSharedMemory* g_shared_memory = nullptr;

struct FakeClockState {
    bool uses_qpc = false;
    int64_t frequency = 1'000;
    int64_t qpc_origin_ticks = 0;
    uint64_t tick_count_origin_ms = 0;
};

struct FakePacerState {
    bool active = false;
    bool initialized = false;
    bool fixed_sleep_override = false;
    uint32_t fixed_sleep_ms = 0;
    int64_t epoch_vsync_ticks = 0;
    uint64_t last_released_frame = 0;
    bool has_released_frame = false;
    uint64_t wait_calls = 0;
    uint64_t skipped_frames = 0;
    uint64_t deadline_misses = 0;
    uint64_t clock_regressions = 0;
    int64_t total_wait_ticks = 0;
    int64_t max_wait_ticks = 0;
    int64_t max_lateness_ticks = 0;
};

struct FakePacingReport {
    bool fixed_sleep = false;
    bool should_log = false;
    uint32_t fixed_sleep_ms = 0;
    uint64_t call = 0;
    uint64_t frame = 0;
    uint64_t skipped_frames = 0;
    uint64_t deadline_misses = 0;
    uint64_t clock_regressions = 0;
    int64_t frequency = 1;
    int64_t average_wait_ticks = 0;
    int64_t max_wait_ticks = 0;
    int64_t max_lateness_ticks = 0;
};

INIT_ONCE g_clock_once = INIT_ONCE_STATIC_INIT;
FakeClockState g_clock_state;
SRWLOCK g_pacer_wait_lock = SRWLOCK_INIT;
SRWLOCK g_pacer_state_lock = SRWLOCK_INIT;
FakePacerState g_pacer_state;
SRWLOCK g_log_lock = SRWLOCK_INIT;
FILE* g_log_file = nullptr;
bool g_log_open_attempted = false;
uint64_t g_log_line_count = 0;
char g_log_buffer[1024 * 1024] = {};

void log_line(const char* text);
void flush_log();

enum class FakeActionKind : uint8_t {
    Unknown,
    HeadsetOnHead,
    Trigger,
    Grip,
    TouchpadClick,
    AButton,
    BButton,
    Squeeze,
    Teleport,
    Pose,
    Thumbstick,
    ThumbstickTouch,
    GripBinary,
    GripAnalog,
    MenuButton,
    PadTouch,
    PadDirectional,
    PadClick,
    PrimaryButton,
    PrimaryButtonTouch,
    SecondaryButton,
    SecondaryButtonTouch,
    Haptic,
    ExternalCamera,
};

struct FakeActionHandleEntry {
    uint64_t handle;
    FakeActionKind kind;
};

FakeActionHandleEntry g_fake_action_handles[32] = {};
LONG g_fake_action_handle_count = 0;
bool g_logged_fake_input_mode = false;

BOOL CALLBACK initialize_fake_clock(PINIT_ONCE, PVOID, PVOID*) {
    LARGE_INTEGER frequency = {};
    LARGE_INTEGER counter = {};
    if (QueryPerformanceFrequency(&frequency) && frequency.QuadPart > 0
        && QueryPerformanceCounter(&counter)) {
        g_clock_state.uses_qpc = true;
        g_clock_state.frequency = frequency.QuadPart;
        g_clock_state.qpc_origin_ticks = counter.QuadPart;
        g_clock_state.tick_count_origin_ms = GetTickCount64();
    } else {
        g_clock_state.uses_qpc = false;
        g_clock_state.frequency = 1'000;
    }

    return TRUE;
}

void ensure_fake_clock() {
    InitOnceExecuteOnce(&g_clock_once, initialize_fake_clock, nullptr, nullptr);
}

int64_t fake_clock_now_ticks() {
    ensure_fake_clock();
    if (!g_clock_state.uses_qpc) {
        return static_cast<int64_t>(GetTickCount64());
    }

    LARGE_INTEGER counter = {};
    if (QueryPerformanceCounter(&counter)) {
        return counter.QuadPart;
    }

    uint64_t elapsed_ms = GetTickCount64() - g_clock_state.tick_count_origin_ms;
    return g_clock_state.qpc_origin_ticks
        + static_cast<int64_t>(
            elapsed_ms * static_cast<uint64_t>(g_clock_state.frequency) / 1'000);
}

int64_t fake_clock_frequency() {
    ensure_fake_clock();
    return g_clock_state.frequency;
}

double ticks_to_seconds(int64_t ticks, int64_t frequency) {
    return static_cast<double>(ticks) / static_cast<double>(frequency);
}

double perf_seconds() {
    return ticks_to_seconds(fake_clock_now_ticks(), fake_clock_frequency());
}

void log_matrix34(const char* label, const vr::HmdMatrix34_t& matrix) {
    char message[512] = {};
    std::snprintf(
        message,
        sizeof(message),
        "%s=[%.6f %.6f %.6f %.6f; %.6f %.6f %.6f %.6f; %.6f %.6f %.6f %.6f]",
        label,
        matrix.m[0][0],
        matrix.m[0][1],
        matrix.m[0][2],
        matrix.m[0][3],
        matrix.m[1][0],
        matrix.m[1][1],
        matrix.m[1][2],
        matrix.m[1][3],
        matrix.m[2][0],
        matrix.m[2][1],
        matrix.m[2][2],
        matrix.m[2][3]
    );
    log_line(message);
}

void log_projection_raw_values(
    const char* label,
    vr::EVREye eye,
    float left,
    float right,
    float top,
    float bottom,
    bool shared_view
) {
    char message[256] = {};
    std::snprintf(
        message,
        sizeof(message),
        "%s eye=%d source=%s raw=[%.6f %.6f %.6f %.6f]",
        label,
        eye,
        shared_view ? "shared-view" : "fallback",
        left,
        right,
        top,
        bottom
    );
    log_line(message);
}

bool env_string_equals(const char* name, const char* expected) {
    char value[64] = {};
    DWORD len = GetEnvironmentVariableA(name, value, sizeof(value));
    return len > 0 && len < sizeof(value) && _stricmp(value, expected) == 0;
}

uint32_t env_u32(const char* name, uint32_t fallback) {
    char value[32] = {};
    DWORD len = GetEnvironmentVariableA(name, value, sizeof(value));
    if (len == 0 || len >= sizeof(value)) {
        return fallback;
    }
    char* end = nullptr;
    unsigned long parsed = std::strtoul(value, &end, 10);
    if (!end || *end != '\0' || parsed > 60000UL) {
        return fallback;
    }
    return static_cast<uint32_t>(parsed);
}

bool env_optional_u32(const char* name, uint32_t* output) {
    char value[32] = {};
    DWORD len = GetEnvironmentVariableA(name, value, sizeof(value));
    if (len == 0 || len >= sizeof(value)) {
        return false;
    }

    char* end = nullptr;
    unsigned long parsed = std::strtoul(value, &end, 10);
    if (!end || *end != '\0' || parsed > 60000UL) {
        return false;
    }
    if (output) {
        *output = static_cast<uint32_t>(parsed);
    }
    return true;
}

int64_t ticks_for_frames(uint64_t frame_count, int64_t frequency) {
    uint64_t whole_seconds = frame_count / kFakeRefreshRate;
    uint64_t remaining_frames = frame_count % kFakeRefreshRate;
    return static_cast<int64_t>(whole_seconds * static_cast<uint64_t>(frequency))
        + static_cast<int64_t>(
            remaining_frames * static_cast<uint64_t>(frequency) / kFakeRefreshRate);
}

uint64_t frames_for_ticks(int64_t ticks, int64_t frequency) {
    if (ticks <= 0 || frequency <= 0) {
        return 0;
    }

    uint64_t positive_ticks = static_cast<uint64_t>(ticks) + 1;
    uint64_t whole_seconds = positive_ticks / static_cast<uint64_t>(frequency);
    uint64_t remaining_ticks = positive_ticks % static_cast<uint64_t>(frequency);
    uint64_t ceiling_frames = whole_seconds * kFakeRefreshRate
        + (remaining_ticks * kFakeRefreshRate + static_cast<uint64_t>(frequency) - 1)
            / static_cast<uint64_t>(frequency);
    return ceiling_frames == 0 ? 0 : ceiling_frames - 1;
}

int64_t running_start_lead_ticks(int64_t frequency) {
    return frequency * kRunningStartLeadUs / kMicrosecondsPerSecond;
}

void ensure_pacer_initialized_locked(int64_t now_ticks) {
    if (g_pacer_state.initialized) {
        return;
    }

    int64_t frequency = fake_clock_frequency();
    g_pacer_state.epoch_vsync_ticks = now_ticks + running_start_lead_ticks(frequency);
    g_pacer_state.initialized = true;
}

int64_t frame_vsync_ticks_locked(uint64_t frame, int64_t frequency) {
    return g_pacer_state.epoch_vsync_ticks + ticks_for_frames(frame, frequency);
}

int64_t frame_running_start_ticks_locked(uint64_t frame, int64_t frequency) {
    return frame_vsync_ticks_locked(frame, frequency) - running_start_lead_ticks(frequency);
}

uint64_t frame_at_or_before_running_start_locked(int64_t now_ticks, int64_t frequency) {
    int64_t first_running_start = frame_running_start_ticks_locked(0, frequency);
    return frames_for_ticks(now_ticks - first_running_start, frequency);
}

FakePacingReport pacer_report_locked(uint64_t frame) {
    FakePacingReport report;
    report.fixed_sleep = g_pacer_state.fixed_sleep_override;
    report.fixed_sleep_ms = g_pacer_state.fixed_sleep_ms;
    report.call = g_pacer_state.wait_calls;
    report.frame = frame;
    report.skipped_frames = g_pacer_state.skipped_frames;
    report.deadline_misses = g_pacer_state.deadline_misses;
    report.clock_regressions = g_pacer_state.clock_regressions;
    report.frequency = fake_clock_frequency();
    report.average_wait_ticks = g_pacer_state.wait_calls == 0
        ? 0
        : g_pacer_state.total_wait_ticks / static_cast<int64_t>(g_pacer_state.wait_calls);
    report.max_wait_ticks = g_pacer_state.max_wait_ticks;
    report.max_lateness_ticks = g_pacer_state.max_lateness_ticks;
    report.should_log = report.call == 1 || report.call % 300 == 0;
    return report;
}

void activate_pacer() {
    AcquireSRWLockExclusive(&g_pacer_wait_lock);
    AcquireSRWLockExclusive(&g_pacer_state_lock);
    if (!g_pacer_state.active) {
        FakePacerState state;
        state.active = true;
        state.fixed_sleep_override =
            env_optional_u32("ALVR_FAKE_WAIT_GET_POSES_SLEEP_MS", &state.fixed_sleep_ms);
        g_pacer_state = state;
        g_shared_pose_reads.store(0, std::memory_order_relaxed);
    }
    ReleaseSRWLockExclusive(&g_pacer_state_lock);
    ReleaseSRWLockExclusive(&g_pacer_wait_lock);
}

void wait_until_ticks(int64_t target_ticks, int64_t frequency) {
    while (true) {
        int64_t remaining_ticks = target_ticks - fake_clock_now_ticks();
        if (remaining_ticks <= 0) {
            return;
        }

        int64_t remaining_us = remaining_ticks * kMicrosecondsPerSecond / frequency;
        if (remaining_us > kYieldThresholdUs * 2) {
            DWORD sleep_ms = static_cast<DWORD>(
                std::max<int64_t>(1, (remaining_us - kYieldThresholdUs) / 1'000));
            Sleep(sleep_ms);
        } else if (remaining_us > kSpinThresholdUs) {
            SwitchToThread();
        } else {
            YieldProcessor();
        }
    }
}

FakePacingReport wait_for_running_start() {
    bool active = false;
    AcquireSRWLockShared(&g_pacer_state_lock);
    active = g_pacer_state.active;
    ReleaseSRWLockShared(&g_pacer_state_lock);
    if (!active) {
        activate_pacer();
    }

    AcquireSRWLockExclusive(&g_pacer_wait_lock);
    int64_t frequency = fake_clock_frequency();
    int64_t call_start_ticks = fake_clock_now_ticks();
    uint64_t target_frame = 0;
    int64_t target_ticks = call_start_ticks;
    bool fixed_sleep_override = false;
    uint32_t fixed_sleep_ms = 0;

    AcquireSRWLockExclusive(&g_pacer_state_lock);
    if (!g_pacer_state.has_released_frame && g_pacer_state.wait_calls == 0) {
        g_pacer_state.epoch_vsync_ticks =
            call_start_ticks + running_start_lead_ticks(frequency);
        g_pacer_state.initialized = true;
    } else {
        ensure_pacer_initialized_locked(call_start_ticks);
    }
    fixed_sleep_override = g_pacer_state.fixed_sleep_override;
    fixed_sleep_ms = g_pacer_state.fixed_sleep_ms;
    target_frame = g_pacer_state.has_released_frame ? g_pacer_state.last_released_frame + 1 : 0;
    target_ticks = frame_running_start_ticks_locked(target_frame, frequency);
    if (!fixed_sleep_override && call_start_ticks > target_ticks) {
        uint64_t newest_frame = frame_at_or_before_running_start_locked(call_start_ticks, frequency);
        if (newest_frame > target_frame) {
            g_pacer_state.skipped_frames += newest_frame - target_frame;
            target_frame = newest_frame;
            target_ticks = frame_running_start_ticks_locked(target_frame, frequency);
        }
        g_pacer_state.deadline_misses += 1;
    }
    ReleaseSRWLockExclusive(&g_pacer_state_lock);

    if (fixed_sleep_override) {
        if (fixed_sleep_ms != 0) {
            Sleep(fixed_sleep_ms);
        }
    } else if (target_ticks > call_start_ticks) {
        wait_until_ticks(target_ticks, frequency);
    }

    int64_t release_ticks = fake_clock_now_ticks();
    AcquireSRWLockExclusive(&g_pacer_state_lock);
    if (release_ticks < call_start_ticks) {
        g_pacer_state.clock_regressions += 1;
        g_pacer_state.epoch_vsync_ticks =
            release_ticks + running_start_lead_ticks(frequency);
        target_frame = 0;
        target_ticks = release_ticks;
    } else if (!fixed_sleep_override) {
        uint64_t newest_frame = frame_at_or_before_running_start_locked(release_ticks, frequency);
        if (newest_frame > target_frame) {
            g_pacer_state.skipped_frames += newest_frame - target_frame;
            target_frame = newest_frame;
            target_ticks = frame_running_start_ticks_locked(target_frame, frequency);
        }
    }

    int64_t wait_ticks = std::max<int64_t>(0, release_ticks - call_start_ticks);
    int64_t lateness_ticks = fixed_sleep_override
        ? 0
        : std::max<int64_t>(0, release_ticks - target_ticks);
    g_pacer_state.has_released_frame = true;
    g_pacer_state.last_released_frame = target_frame;
    g_pacer_state.wait_calls += 1;
    g_pacer_state.total_wait_ticks += wait_ticks;
    g_pacer_state.max_wait_ticks = std::max(g_pacer_state.max_wait_ticks, wait_ticks);
    g_pacer_state.max_lateness_ticks =
        std::max(g_pacer_state.max_lateness_ticks, lateness_ticks);
    FakePacingReport report = pacer_report_locked(target_frame);
    ReleaseSRWLockExclusive(&g_pacer_state_lock);
    ReleaseSRWLockExclusive(&g_pacer_wait_lock);

    return report;
}

double pacing_ticks_to_microseconds(int64_t ticks, int64_t frequency) {
    return static_cast<double>(ticks) * static_cast<double>(kMicrosecondsPerSecond)
        / static_cast<double>(frequency);
}

void log_pacing_report(const FakePacingReport& report) {
    char message[512] = {};
    std::snprintf(
        message,
        sizeof(message),
        "fake pacing summary mode=%s fixed_sleep_ms=%u calls=%llu frame=%llu skipped_frames=%llu deadline_misses=%llu clock_regressions=%llu avg_wait_us=%.3f max_wait_us=%.3f max_lateness_us=%.3f shared_pose_reads=%llu clock=%s frequency=%lld",
        report.fixed_sleep ? "fixed-sleep" : "deadline",
        report.fixed_sleep_ms,
        static_cast<unsigned long long>(report.call),
        static_cast<unsigned long long>(report.frame),
        static_cast<unsigned long long>(report.skipped_frames),
        static_cast<unsigned long long>(report.deadline_misses),
        static_cast<unsigned long long>(report.clock_regressions),
        pacing_ticks_to_microseconds(report.average_wait_ticks, report.frequency),
        pacing_ticks_to_microseconds(report.max_wait_ticks, report.frequency),
        pacing_ticks_to_microseconds(report.max_lateness_ticks, report.frequency),
        static_cast<unsigned long long>(g_shared_pose_reads.load(std::memory_order_relaxed)),
        g_clock_state.uses_qpc ? "qpc" : "tick-count",
        static_cast<long long>(report.frequency));
    log_line(message);
}

void deactivate_pacer() {
    AcquireSRWLockExclusive(&g_pacer_wait_lock);
    AcquireSRWLockExclusive(&g_pacer_state_lock);
    FakePacingReport report = pacer_report_locked(g_pacer_state.last_released_frame);
    bool should_log = g_pacer_state.active && g_pacer_state.wait_calls != 0;
    g_pacer_state.active = false;
    g_pacer_state.initialized = false;
    ReleaseSRWLockExclusive(&g_pacer_state_lock);
    ReleaseSRWLockExclusive(&g_pacer_wait_lock);
    if (should_log) {
        log_pacing_report(report);
    }
}

uint64_t fake_frame_counter() {
    int64_t now_ticks = fake_clock_now_ticks();
    int64_t frequency = fake_clock_frequency();
    AcquireSRWLockExclusive(&g_pacer_state_lock);
    ensure_pacer_initialized_locked(now_ticks);
    uint64_t frame = frame_at_or_before_running_start_locked(now_ticks, frequency);
    ReleaseSRWLockExclusive(&g_pacer_state_lock);
    return frame;
}

double fake_session_elapsed_seconds() {
    int64_t now_ticks = fake_clock_now_ticks();
    int64_t frequency = fake_clock_frequency();
    AcquireSRWLockExclusive(&g_pacer_state_lock);
    ensure_pacer_initialized_locked(now_ticks);
    int64_t first_running_start = frame_running_start_ticks_locked(0, frequency);
    ReleaseSRWLockExclusive(&g_pacer_state_lock);
    return ticks_to_seconds(std::max<int64_t>(0, now_ticks - first_running_start), frequency);
}

double fake_frame_vsync_seconds(uint64_t frame) {
    int64_t now_ticks = fake_clock_now_ticks();
    int64_t frequency = fake_clock_frequency();
    AcquireSRWLockExclusive(&g_pacer_state_lock);
    ensure_pacer_initialized_locked(now_ticks);
    int64_t vsync_ticks = frame_vsync_ticks_locked(frame, frequency);
    ReleaseSRWLockExclusive(&g_pacer_state_lock);
    return ticks_to_seconds(vsync_ticks, frequency);
}

float fake_frame_time_remaining_seconds() {
    int64_t now_ticks = fake_clock_now_ticks();
    int64_t frequency = fake_clock_frequency();
    AcquireSRWLockExclusive(&g_pacer_state_lock);
    ensure_pacer_initialized_locked(now_ticks);
    uint64_t current_frame = frame_at_or_before_running_start_locked(now_ticks, frequency);
    int64_t next_running_start = frame_running_start_ticks_locked(current_frame + 1, frequency);
    ReleaseSRWLockExclusive(&g_pacer_state_lock);
    return static_cast<float>(ticks_to_seconds(
        std::max<int64_t>(0, next_running_start - now_ticks), frequency));
}

uint32_t render_target_dimension(const char* name, uint32_t fallback) {
    uint32_t value = env_u32(name, fallback);
    return value >= 320 ? value : fallback;
}

struct SharedViewConfig {
    float fov[2][4] = {};
    float eye_x_m[2] = {};
};

struct SharedHmdPose {
    uint64_t timestamp_ns = 0;
    float matrix[3][4] = {
        { 1.0f, 0.0f, 0.0f, 0.0f },
        { 0.0f, 1.0f, 0.0f, 0.0f },
        { 0.0f, 0.0f, 1.0f, 0.0f },
    };
};

struct SharedControllerSnapshot {
    uint32_t packet_number = 0;
    uint64_t tracking_timestamp_ns = 0;
    uint64_t motion_update_wall_ns = 0;
    uint64_t input_update_wall_ns = 0;
    float matrix[3][4] = {};
    float linear_velocity[3] = {};
    float angular_velocity[3] = {};
    uint64_t buttons_pressed = 0;
    uint64_t buttons_touched = 0;
    float axes[5][2] = {};
};

struct FakeInputSnapshotState {
    bool current_synthetic_active = false;
    bool previous_synthetic_active = false;
    bool current_synthetic_pressed = false;
    bool previous_synthetic_pressed = false;
    bool current_valid[ALVR_NUM_CONTROLLERS] = {};
    bool previous_valid[ALVR_NUM_CONTROLLERS] = {};
    SharedControllerSnapshot current[ALVR_NUM_CONTROLLERS] = {};
    SharedControllerSnapshot previous[ALVR_NUM_CONTROLLERS] = {};
};

SRWLOCK g_fake_input_state_lock = SRWLOCK_INIT;
FakeInputSnapshotState g_fake_input_state;

std::string wine_shared_memory_path() {
    std::string path = "Z:" ALVR_SHM_PATH;
    for (char& ch : path) {
        if (ch == '/') {
            ch = '\\';
        }
    }
    return path;
}

AlvrSharedMemory* shared_memory() {
    if (g_shared_memory) {
        return g_shared_memory;
    }

    std::string path = wine_shared_memory_path();
    g_shared_memory_file = CreateFileA(
        path.c_str(),
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );
    if (g_shared_memory_file == INVALID_HANDLE_VALUE) {
        return nullptr;
    }

    g_shared_memory_mapping =
        CreateFileMappingA(g_shared_memory_file, nullptr, PAGE_READWRITE, 0, 0, nullptr);
    if (!g_shared_memory_mapping) {
        CloseHandle(g_shared_memory_file);
        g_shared_memory_file = INVALID_HANDLE_VALUE;
        return nullptr;
    }

    g_shared_memory = static_cast<AlvrSharedMemory*>(MapViewOfFile(
        g_shared_memory_mapping, FILE_MAP_WRITE | FILE_MAP_READ, 0, 0, sizeof(AlvrSharedMemory)
    ));
    if (!g_shared_memory) {
        CloseHandle(g_shared_memory_mapping);
        CloseHandle(g_shared_memory_file);
        g_shared_memory_mapping = nullptr;
        g_shared_memory_file = INVALID_HANDLE_VALUE;
    }

    return g_shared_memory;
}

bool valid_fov_angle(float value) {
    return std::isfinite(value) && std::fabs(value) > 0.001f
        && std::fabs(value) < 1.5707963f;
}

bool valid_shared_view_config(const SharedViewConfig& config) {
    if (!(config.eye_x_m[0] < config.eye_x_m[1])) {
        return false;
    }
    for (int eye = 0; eye < 2; ++eye) {
        if (!std::isfinite(config.eye_x_m[eye]) || std::fabs(config.eye_x_m[eye]) > 0.2f) {
            return false;
        }
        for (int index = 0; index < 4; ++index) {
            if (!valid_fov_angle(config.fov[eye][index])) {
                return false;
            }
        }
        if (!(config.fov[eye][0] < 0.0f && config.fov[eye][1] > 0.0f
                && config.fov[eye][2] > 0.0f && config.fov[eye][3] < 0.0f)) {
            return false;
        }
    }
    return true;
}

uint64_t unix_time_ns() {
    FILETIME file_time;
    GetSystemTimePreciseAsFileTime(&file_time);
    ULARGE_INTEGER value;
    value.LowPart = file_time.dwLowDateTime;
    value.HighPart = file_time.dwHighDateTime;
    return (value.QuadPart - 116444736000000000ULL) * 100ULL;
}

bool bridge_heartbeat_live(const AlvrSharedMemory* shm) {
    if (!shm || shm->bridge_session_id == 0 || shm->bridge_heartbeat_ns == 0) {
        return false;
    }

    uint64_t now = unix_time_ns();
    uint64_t heartbeat = shm->bridge_heartbeat_ns;
    return (heartbeat <= now && now - heartbeat <= kMaxBridgeHeartbeatAgeNs)
        || (heartbeat > now && heartbeat - now <= kBridgeHeartbeatFutureToleranceNs);
}

bool read_shared_view_config(SharedViewConfig* config) {
    if (!config) {
        return false;
    }

    auto* shm = shared_memory();
    if (!shm) {
        return false;
    }

    bool ok = shm->magic == ALVR_SHM_MAGIC && shm->version == ALVR_SHM_VERSION
        && shm->initialized != 0 && shm->shutdown == 0 && bridge_heartbeat_live(shm);
    if (!ok) {
        return false;
    }

    for (int attempt = 0; attempt < 3; ++attempt) {
        uint32_t before = shm->hmd_pose_sequence;
        if ((before & 1U) != 0) {
            continue;
        }

        std::atomic_thread_fence(std::memory_order_acquire);
        for (int eye = 0; eye < 2; ++eye) {
            for (int index = 0; index < 4; ++index) {
                config->fov[eye][index] = shm->view_fov[eye][index];
            }
            config->eye_x_m[eye] = shm->view_eye_x_m[eye];
        }
        std::atomic_thread_fence(std::memory_order_acquire);

        uint32_t after = shm->hmd_pose_sequence;
        if (before != after || (after & 1U) != 0 || shm->view_config_set == 0
            || shm->initialized == 0 || shm->shutdown != 0) {
            continue;
        }

        return valid_shared_view_config(*config);
    }

    return false;
}

bool valid_pose_matrix(const float matrix[3][4]) {
    for (int row = 0; row < 3; ++row) {
        for (int col = 0; col < 4; ++col) {
            if (!std::isfinite(matrix[row][col]) || std::fabs(matrix[row][col]) > 1000.0f) {
                return false;
            }
        }
    }

    auto row_len_sq = [&](int row) {
        return matrix[row][0] * matrix[row][0] + matrix[row][1] * matrix[row][1]
            + matrix[row][2] * matrix[row][2];
    };
    auto row_dot = [&](int a, int b) {
        return matrix[a][0] * matrix[b][0] + matrix[a][1] * matrix[b][1]
            + matrix[a][2] * matrix[b][2];
    };

    return row_len_sq(0) >= 0.5f && row_len_sq(0) <= 1.5f && row_len_sq(1) >= 0.5f
        && row_len_sq(1) <= 1.5f && row_len_sq(2) >= 0.5f && row_len_sq(2) <= 1.5f
        && std::fabs(row_dot(0, 1)) <= 0.2f && std::fabs(row_dot(0, 2)) <= 0.2f
        && std::fabs(row_dot(1, 2)) <= 0.2f;
}

bool read_pose_seqlock(
    const volatile uint32_t* sequence,
    const uint64_t* timestamp_ns,
    const float matrix[3][4],
    SharedHmdPose* pose
) {
    for (int attempt = 0; attempt < 3; ++attempt) {
        uint32_t before = *sequence;
        if (before == 0 || (before & 1U) != 0) {
            continue;
        }

        std::atomic_thread_fence(std::memory_order_acquire);
        uint64_t timestamp = *timestamp_ns;
        float local_matrix[3][4] = {};
        std::memcpy(local_matrix, matrix, sizeof(local_matrix));
        std::atomic_thread_fence(std::memory_order_acquire);

        uint32_t after = *sequence;
        if (before != after || (after & 1U) != 0) {
            continue;
        }
        if (!valid_pose_matrix(local_matrix)) {
            return false;
        }

        pose->timestamp_ns = timestamp != 0 ? timestamp : 1;
        std::memcpy(pose->matrix, local_matrix, sizeof(pose->matrix));
        return true;
    }

    return false;
}

bool read_shared_hmd_pose(SharedHmdPose* pose) {
    if (!pose) {
        return false;
    }

    auto* shm = shared_memory();
    if (!shm) {
        return false;
    }

    bool ok = shm->magic == ALVR_SHM_MAGIC && shm->version == ALVR_SHM_VERSION
        && shm->initialized != 0 && shm->shutdown == 0 && shm->hmd_pose_set != 0
        && bridge_heartbeat_live(shm);
    if (ok) {
        ok = read_pose_seqlock(
            &shm->hmd_pose_sequence, &shm->hmd_pose_timestamp_ns, shm->hmd_pose, pose
        );
        ok = ok && shm->hmd_pose_set != 0 && shm->initialized != 0 && shm->shutdown == 0;
    }

    return ok;
}

int shared_controller_index(vr::TrackedDeviceIndex_t device) {
    if (device == 1) {
        return 0;
    }
    if (device == 2) {
        return 1;
    }
    return -1;
}

bool read_shared_controller_state(
    vr::TrackedDeviceIndex_t device, SharedControllerSnapshot* snapshot
) {
    if (!snapshot) {
        return false;
    }
    int controller_index = shared_controller_index(device);
    if (controller_index < 0) {
        return false;
    }

    auto* shm = shared_memory();
    if (!shm || shm->magic != ALVR_SHM_MAGIC || shm->version != ALVR_SHM_VERSION
        || shm->initialized == 0 || shm->shutdown != 0 || !bridge_heartbeat_live(shm)) {
        return false;
    }

    const AlvrControllerState* controller = &shm->controllers[controller_index];
    for (int attempt = 0; attempt < 3; ++attempt) {
        uint32_t before = controller->sequence;
        if (before == 0 || (before & 1U) != 0) {
            continue;
        }

        std::atomic_thread_fence(std::memory_order_acquire);
        uint32_t connected = controller->connected;
        SharedControllerSnapshot local;
        local.packet_number = controller->packet_number;
        local.tracking_timestamp_ns = controller->tracking_timestamp_ns;
        local.motion_update_wall_ns = controller->motion_update_wall_ns;
        local.input_update_wall_ns = controller->input_update_wall_ns;
        std::memcpy(local.matrix, controller->pose, sizeof(local.matrix));
        std::memcpy(
            local.linear_velocity, controller->linear_velocity, sizeof(local.linear_velocity)
        );
        std::memcpy(
            local.angular_velocity, controller->angular_velocity, sizeof(local.angular_velocity)
        );
        local.buttons_pressed = controller->buttons_pressed;
        local.buttons_touched = controller->buttons_touched;
        std::memcpy(local.axes, controller->axes, sizeof(local.axes));
        std::atomic_thread_fence(std::memory_order_acquire);

        uint32_t after = controller->sequence;
        if (before != after || (after & 1U) != 0 || connected == 0
            || controller->connected == 0) {
            continue;
        }

        uint64_t now = unix_time_ns();
        bool fresh = local.motion_update_wall_ns != 0
            && ((local.motion_update_wall_ns <= now
                    && now - local.motion_update_wall_ns <= kMaxControllerMotionAgeNs)
                || (local.motion_update_wall_ns > now
                    && local.motion_update_wall_ns - now
                        <= kBridgeHeartbeatFutureToleranceNs));
        if (!fresh || !valid_pose_matrix(local.matrix)) {
            return false;
        }
        for (int axis = 0; axis < 3; ++axis) {
            if (!std::isfinite(local.linear_velocity[axis])
                || !std::isfinite(local.angular_velocity[axis])) {
                return false;
            }
        }
        for (int axis = 0; axis < 5; ++axis) {
            if (!std::isfinite(local.axes[axis][0]) || !std::isfinite(local.axes[axis][1])) {
                return false;
            }
        }

        *snapshot = local;
        if (InterlockedCompareExchange(
                &g_logged_controller_ready[controller_index], 1, 0
            ) == 0) {
            char message[320] = {};
            std::snprintf(
                message,
                sizeof(message),
                "shared controller ready hand=%s packet=%u tracking_timestamp_ns=%llu position=[%.4f,%.4f,%.4f]",
                controller_index == 0 ? "left" : "right",
                local.packet_number,
                static_cast<unsigned long long>(local.tracking_timestamp_ns),
                local.matrix[0][3],
                local.matrix[1][3],
                local.matrix[2][3]
            );
            log_line(message);
        }
        return true;
    }

    return false;
}

uint64_t publish_frame_pose_snapshot(const SharedHmdPose* pose) {
    if (!pose || pose->timestamp_ns == 0 || !valid_pose_matrix(pose->matrix)) {
        return 0;
    }

    auto* shm = shared_memory();
    if (!shm) {
        return 0;
    }

    uint64_t generation = 0;
    if (shm->magic == ALVR_SHM_MAGIC && shm->version == ALVR_SHM_VERSION
        && shm->initialized != 0 && shm->shutdown == 0 && bridge_heartbeat_live(shm)) {
        generation = g_pose_generation.fetch_add(1, std::memory_order_relaxed) + 1;
        uint32_t sequence = shm->frame_pose_sequence;
        uint32_t write_sequence = ((sequence & 1U) == 0) ? sequence + 1U : sequence + 2U;
        shm->frame_pose_sequence = write_sequence;
        std::atomic_thread_fence(std::memory_order_release);
        std::memcpy(shm->frame_pose, pose->matrix, sizeof(shm->frame_pose));
        shm->frame_pose_timestamp_ns = pose->timestamp_ns;
        std::atomic_thread_fence(std::memory_order_release);
        shm->frame_pose_sequence = write_sequence + 1U;
    }

    return generation;
}

bool shared_eye_raw(vr::EVREye eye, float* left, float* right, float* top, float* bottom) {
    SharedViewConfig config;
    if (!read_shared_view_config(&config)) {
        return false;
    }

    int eye_index = eye == vr::Eye_Right ? 1 : 0;
    if (left) {
        *left = std::tan(config.fov[eye_index][0]);
    }
    if (right) {
        *right = std::tan(config.fov[eye_index][1]);
    }
    if (top) {
        *top = std::tan(config.fov[eye_index][3]);
    }
    if (bottom) {
        *bottom = std::tan(config.fov[eye_index][2]);
    }

    return true;
}

bool shared_eye_x(vr::EVREye eye, float* eye_x_m) {
    SharedViewConfig config;
    if (!eye_x_m || !read_shared_view_config(&config)) {
        return false;
    }
    *eye_x_m = config.eye_x_m[eye == vr::Eye_Right ? 1 : 0];
    return true;
}

bool is_system_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRSystem_Version) == 0
            || std::strcmp(version, kLegacySystem011) == 0
            || std::strcmp(version, kLegacySystem012) == 0
            || std::strcmp(version, kLegacySystem019) == 0);
}

bool is_legacy_system011_or_012_interface(const char* version) {
    return version && (std::strcmp(version, kLegacySystem011) == 0 || std::strcmp(version, kLegacySystem012) == 0);
}

bool is_legacy_system019_interface(const char* version) {
    return version && std::strcmp(version, kLegacySystem019) == 0;
}

bool is_compositor_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRCompositor_Version) == 0
            || std::strcmp(version, kLegacyCompositor013) == 0
            || std::strcmp(version, kLegacyCompositor014) == 0
            || std::strcmp(version, kLegacyCompositor016) == 0
            || std::strcmp(version, kLegacyCompositor022) == 0);
}

bool is_legacy_compositor013_interface(const char* version) {
    return version && std::strcmp(version, kLegacyCompositor013) == 0;
}

bool is_legacy_compositor014_interface(const char* version) {
    return version && std::strcmp(version, kLegacyCompositor014) == 0;
}

bool is_legacy_compositor016_interface(const char* version) {
    return version && std::strcmp(version, kLegacyCompositor016) == 0;
}

bool is_chaperone_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRChaperone_Version) == 0
            || std::strcmp(version, kLegacyChaperone003) == 0);
}

bool is_chaperone_setup_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRChaperoneSetup_Version) == 0
            || std::strcmp(version, kLegacyChaperoneSetup005) == 0);
}

bool is_legacy_chaperone_setup005_interface(const char* version) {
    return version && std::strcmp(version, kLegacyChaperoneSetup005) == 0;
}

bool is_overlay_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVROverlay_Version) == 0
            || std::strcmp(version, kLegacyOverlay010) == 0
            || std::strcmp(version, kLegacyOverlay013) == 0
            || std::strcmp(version, kLegacyOverlay018) == 0
            || std::strcmp(version, kLegacyOverlay019) == 0);
}

bool is_legacy_overlay013_interface(const char* version) {
    return version && std::strcmp(version, kLegacyOverlay013) == 0;
}

bool is_legacy_input005_interface(const char* version) {
    return version && std::strcmp(version, kLegacyInput005) == 0;
}

bool is_legacy_input006_interface(const char* version) {
    return version && std::strcmp(version, kLegacyInput006) == 0;
}

bool is_render_models_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRRenderModels_Version) == 0
            || std::strcmp(version, kLegacyRenderModels004) == 0
            || std::strcmp(version, kLegacyRenderModels006) == 0);
}

bool is_screenshots_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRScreenshots_Version) == 0
            || std::strcmp(version, kLegacyScreenshots001) == 0);
}

bool is_applications_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRApplications_Version) == 0
            || std::strcmp(version, kLegacyApplications004) == 0
            || std::strcmp(version, kLegacyApplications005) == 0);
}

bool is_legacy_applications004_or_005_interface(const char* version) {
    return version && (std::strcmp(version, kLegacyApplications004) == 0
        || std::strcmp(version, kLegacyApplications005) == 0);
}

bool is_settings_interface(const char* version) {
    return version
        && (std::strcmp(version, vr::IVRSettings_Version) == 0
            || std::strcmp(version, kLegacySettings001) == 0);
}

bool is_legacy_settings001_interface(const char* version) {
    return version && std::strcmp(version, kLegacySettings001) == 0;
}

void log_line(const char* text) {
    AcquireSRWLockExclusive(&g_log_lock);
    if (!g_log_open_attempted) {
        g_log_open_attempted = true;
        g_log_file = std::fopen("Z:\\tmp\\fake_openvr_real.log", "ab");
        if (g_log_file) {
            std::setvbuf(g_log_file, g_log_buffer, _IOFBF, sizeof(g_log_buffer));
            std::fprintf(g_log_file, "fake logger mode=persistent-buffered flush_interval=256\n");
        }
    }
    if (g_log_file) {
        std::fprintf(g_log_file, "%s\n", text);
        ++g_log_line_count;
        if (g_log_line_count % 256 == 0
            || std::strncmp(
                text,
                kPacingSummaryPrefix,
                sizeof(kPacingSummaryPrefix) - 1) == 0) {
            std::fflush(g_log_file);
        }
    }
    ReleaseSRWLockExclusive(&g_log_lock);
}

void flush_log() {
    AcquireSRWLockExclusive(&g_log_lock);
    if (g_log_file) {
        std::fflush(g_log_file);
    }
    ReleaseSRWLockExclusive(&g_log_lock);
}

void log_call(const char* name) {
    char message[128] = {};
    std::snprintf(message, sizeof(message), "fake call %s", name);
    log_line(message);
}

void log_call_value(const char* name, const char* value) {
    char message[512] = {};
    std::snprintf(message, sizeof(message), "fake call %s %s", name, value ? value : "<null>");
    log_line(message);
}

void log_call_u32(const char* name, uint32_t value) {
    char message[192] = {};
    std::snprintf(message, sizeof(message), "fake call %s %u", name, value);
    log_line(message);
}

void log_call_u32_pair(const char* name, uint32_t first, uint32_t second) {
    char message[224] = {};
    std::snprintf(message, sizeof(message), "fake call %s %u %u", name, first, second);
    log_line(message);
}

void log_device_property(const char* name, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop) {
    char message[224] = {};
    std::snprintf(message, sizeof(message), "fake call %s device=%u prop=%u", name, device, static_cast<uint32_t>(prop));
    log_line(message);
}

void log_eye_call(const char* name, vr::EVREye eye) {
    char message[160] = {};
    std::snprintf(message, sizeof(message), "fake call %s eye=%u", name, static_cast<uint32_t>(eye));
    log_line(message);
}

void log_interface(const char* kind, const char* interface_version) {
    char message[256] = {};
    std::snprintf(
        message,
        sizeof(message),
        "fake VR_GetGenericInterface %s %s",
        kind,
        interface_version ? interface_version : "<null>"
    );
    log_line(message);
}

vr::HmdMatrix34_t identity34(float x = 0.0f) {
    vr::HmdMatrix34_t m = {};
    m.m[0][0] = 1.0f;
    m.m[1][1] = 1.0f;
    m.m[2][2] = 1.0f;
    m.m[0][3] = x;
    return m;
}

vr::HmdMatrix44_t identity44() {
    vr::HmdMatrix44_t m = {};
    m.m[0][0] = 1.0f;
    m.m[1][1] = 1.0f;
    m.m[2][2] = 1.0f;
    m.m[3][3] = 1.0f;
    return m;
}

bool is_fake_hmd(vr::TrackedDeviceIndex_t device);
bool is_fake_controller(vr::TrackedDeviceIndex_t device);
bool is_fake_tracked_device(vr::TrackedDeviceIndex_t device);
vr::ETrackedControllerRole fake_controller_role(vr::TrackedDeviceIndex_t device);

void fill_pose_with_snapshot(
    vr::TrackedDevicePose_t* pose,
    vr::TrackedDeviceIndex_t device,
    const SharedHmdPose* shared_hmd_pose,
    const SharedControllerSnapshot* shared_controller
) {
    if (!pose) {
        return;
    }
    std::memset(pose, 0, sizeof(*pose));
    if (device == vr::k_unTrackedDeviceIndex_Hmd) {
        if (shared_hmd_pose) {
            std::memcpy(
                pose->mDeviceToAbsoluteTracking.m,
                shared_hmd_pose->matrix,
                sizeof(shared_hmd_pose->matrix)
            );
        } else {
            pose->mDeviceToAbsoluteTracking = identity34();
        }
        pose->eTrackingResult = vr::TrackingResult_Running_OK;
        pose->bPoseIsValid = true;
        pose->bDeviceIsConnected = true;
    } else if (shared_controller) {
        std::memcpy(
            pose->mDeviceToAbsoluteTracking.m,
            shared_controller->matrix,
            sizeof(shared_controller->matrix)
        );
        for (int axis = 0; axis < 3; ++axis) {
            pose->vVelocity.v[axis] = shared_controller->linear_velocity[axis];
            pose->vAngularVelocity.v[axis] = shared_controller->angular_velocity[axis];
        }
        pose->eTrackingResult = vr::TrackingResult_Running_OK;
        pose->bPoseIsValid = true;
        pose->bDeviceIsConnected = true;
    } else {
        pose->mDeviceToAbsoluteTracking = identity34();
        pose->eTrackingResult = vr::TrackingResult_Uninitialized;
        pose->bPoseIsValid = false;
        pose->bDeviceIsConnected = shared_controller_index(device) >= 0;
    }
}

void fill_pose(vr::TrackedDevicePose_t* pose, vr::TrackedDeviceIndex_t device) {
    SharedHmdPose shared_hmd_pose;
    SharedControllerSnapshot shared_controller;
    fill_pose_with_snapshot(
        pose,
        device,
        device == vr::k_unTrackedDeviceIndex_Hmd && read_shared_hmd_pose(&shared_hmd_pose)
            ? &shared_hmd_pose
            : nullptr,
        shared_controller_index(device) >= 0
                && read_shared_controller_state(device, &shared_controller)
            ? &shared_controller
            : nullptr
    );
}

void fill_poses_with_snapshot(
    vr::TrackedDevicePose_t* poses, uint32_t count, const SharedHmdPose* shared_hmd_pose
) {
    if (!poses) {
        return;
    }
    for (uint32_t index = 0; index < count; ++index) {
        SharedControllerSnapshot shared_controller;
        const SharedControllerSnapshot* controller_snapshot =
            read_shared_controller_state(index, &shared_controller) ? &shared_controller : nullptr;
        fill_pose_with_snapshot(
            &poses[index], index, index == vr::k_unTrackedDeviceIndex_Hmd ? shared_hmd_pose : nullptr,
            controller_snapshot
        );
    }
}

void fill_poses(vr::TrackedDevicePose_t* poses, uint32_t count) {
    SharedHmdPose shared_pose;
    const SharedHmdPose* pose_snapshot = read_shared_hmd_pose(&shared_pose) ? &shared_pose : nullptr;
    fill_poses_with_snapshot(poses, count, pose_snapshot);
}

void copy_string(const char* value, char* buffer, uint32_t buffer_size) {
    if (!buffer || buffer_size == 0) {
        return;
    }
    std::snprintf(buffer, buffer_size, "%s", value);
}

const char* render_model_name(uint32_t index) {
    switch (index) {
    case 0:
        return "fake_hmd";
    case 1:
        return "vr_controller_vive_1_5";
    case 2:
        return "vr_controller_vive_1_5";
    default:
        return nullptr;
    }
}

bool is_fake_controller_model(const char* model_name) {
    return model_name
        && (std::strcmp(model_name, "fake_controller_left") == 0
            || std::strcmp(model_name, "fake_controller_right") == 0
            || std::strcmp(model_name, "vr_controller_vive_1_5") == 0);
}

bool is_fake_model(const char* model_name) {
    return model_name
        && (std::strcmp(model_name, "fake_hmd") == 0 || is_fake_controller_model(model_name));
}

const char* render_model_component_name(uint32_t index) {
    static const char* names[] = {
        vr::k_pch_Controller_Component_Base,
        vr::k_pch_Controller_Component_Tip,
        "handgrip",
        "body",
        "status",
    };
    if (index >= sizeof(names) / sizeof(names[0])) {
        return nullptr;
    }
    return names[index];
}

bool is_render_model_component(const char* component_name) {
    if (!component_name) {
        return false;
    }
    for (uint32_t index = 0;; ++index) {
        const char* name = render_model_component_name(index);
        if (!name) {
            return false;
        }
        if (std::strcmp(component_name, name) == 0) {
            return true;
        }
    }
}

const char* render_model_error_name(vr::EVRRenderModelError error) {
    switch (error) {
    case vr::VRRenderModelError_None:
        return "VRRenderModelError_None";
    case vr::VRRenderModelError_Loading:
        return "VRRenderModelError_Loading";
    case vr::VRRenderModelError_NotSupported:
        return "VRRenderModelError_NotSupported";
    case vr::VRRenderModelError_InvalidArg:
        return "VRRenderModelError_InvalidArg";
    case vr::VRRenderModelError_InvalidModel:
        return "VRRenderModelError_InvalidModel";
    case vr::VRRenderModelError_InvalidTexture:
        return "VRRenderModelError_InvalidTexture";
    default:
        return "VRRenderModelError_Unknown";
    }
}

vr::RenderModel_Vertex_t g_render_model_vertices[] = {
    {{{-0.03f, -0.03f, 0.0f}}, {{0.0f, 0.0f, -1.0f}}, {0.0f, 1.0f}},
    {{{0.03f, -0.03f, 0.0f}}, {{0.0f, 0.0f, -1.0f}}, {1.0f, 1.0f}},
    {{{0.0f, 0.04f, 0.0f}}, {{0.0f, 0.0f, -1.0f}}, {0.5f, 0.0f}},
};
uint16_t g_render_model_indices[] = { 0, 1, 2 };
uint8_t g_render_texture_data[] = { 255, 255, 255, 255 };
vr::RenderModel_TextureMap_t g_render_texture = {
    1,
    1,
    g_render_texture_data,
    vr::VRRenderModelTextureFormat_RGBA8_SRGB,
};
vr::RenderModel_t g_render_model = {
    g_render_model_vertices,
    static_cast<uint32_t>(sizeof(g_render_model_vertices) / sizeof(g_render_model_vertices[0])),
    g_render_model_indices,
    1,
    1,
};

bool is_fake_hmd(vr::TrackedDeviceIndex_t device) {
    return device == vr::k_unTrackedDeviceIndex_Hmd;
}

bool is_fake_controller(vr::TrackedDeviceIndex_t device) {
    return device == 1 || device == 2;
}

bool is_fake_tracked_device(vr::TrackedDeviceIndex_t device) {
    return is_fake_hmd(device) || is_fake_controller(device);
}

vr::ETrackedControllerRole fake_controller_role(vr::TrackedDeviceIndex_t device) {
    if (device == 1) {
        return vr::TrackedControllerRole_LeftHand;
    }
    if (device == 2) {
        return vr::TrackedControllerRole_RightHand;
    }
    return vr::TrackedControllerRole_Invalid;
}

uint64_t fake_supported_controller_buttons() {
    return vr::ButtonMaskFromId(vr::k_EButton_System)
        | vr::ButtonMaskFromId(vr::k_EButton_ApplicationMenu)
        | vr::ButtonMaskFromId(vr::k_EButton_Grip)
        | vr::ButtonMaskFromId(vr::k_EButton_A)
        | vr::ButtonMaskFromId(vr::k_EButton_SteamVR_Touchpad)
        | vr::ButtonMaskFromId(vr::k_EButton_SteamVR_Trigger);
}

void* __thiscall fake_ret0(void*) { return nullptr; }
void* __stdcall fake_c_ret0() { return nullptr; }

void __thiscall fake_get_recommended_render_target_size(void*, uint32_t* width, uint32_t* height) {
    log_call("IVRSystem::GetRecommendedRenderTargetSize");
    if (width) {
        *width = render_target_dimension("ALVR_FAKE_RENDER_TARGET_WIDTH", 1280);
    }
    if (height) {
        *height = render_target_dimension("ALVR_FAKE_RENDER_TARGET_HEIGHT", 720);
    }
    char message[128] = {};
    std::snprintf(
        message,
        sizeof(message),
        "IVRSystem::GetRecommendedRenderTargetSize return width=%u height=%u",
        width ? *width : 0,
        height ? *height : 0
    );
    log_line(message);
}

void __stdcall fake_c_get_recommended_render_target_size(uint32_t* width, uint32_t* height) {
    fake_get_recommended_render_target_size(nullptr, width, height);
}

void normalize_clip_planes(float* near_z, float* far_z) {
    if (*near_z <= 0.0f) {
        *near_z = 0.01f;
    }
    if (*far_z <= *near_z) {
        *far_z = *near_z + 1000.0f;
    }
}

vr::HmdMatrix44_t projection_matrix_from_raw(
    float left, float right, float top, float bottom, float near_z, float far_z
) {
    normalize_clip_planes(&near_z, &far_z);

    vr::HmdMatrix44_t m = {};
    float inv_width = 1.0f / (right - left);
    float inv_height = 1.0f / (bottom - top);
    m.m[0][0] = 2.0f * inv_width;
    m.m[0][2] = (right + left) * inv_width;
    m.m[1][1] = 2.0f * inv_height;
    m.m[1][2] = (bottom + top) * inv_height;
    m.m[2][2] = far_z / (near_z - far_z);
    m.m[2][3] = (far_z * near_z) / (near_z - far_z);
    m.m[3][2] = -1.0f;
    return m;
}

vr::HmdMatrix44_t fake_projection_matrix(float near_z, float far_z) {
    if (near_z <= 0.0f) {
        near_z = 0.01f;
    }
    if (far_z <= near_z) {
        far_z = near_z + 1000.0f;
    }

    vr::HmdMatrix44_t m = {};
    m.m[0][0] = 1.0f;
    m.m[1][1] = 1.0f;
    m.m[2][2] = far_z / (near_z - far_z);
    m.m[2][3] = (far_z * near_z) / (near_z - far_z);
    m.m[3][2] = -1.0f;
    return m;
}

vr::HmdMatrix44_t fake_projection_matrix(vr::EVREye eye, float near_z, float far_z) {
    float left = 0.0f;
    float right = 0.0f;
    float top = 0.0f;
    float bottom = 0.0f;
    if (shared_eye_raw(eye, &left, &right, &top, &bottom)) {
        log_eye_call("IVRSystem::GetProjectionMatrix using shared view", eye);
        return projection_matrix_from_raw(left, right, top, bottom, near_z, far_z);
    }
    return fake_projection_matrix(near_z, far_z);
}

vr::HmdMatrix44_t __thiscall fake_get_projection_matrix(void*, vr::EVREye eye, float near_z, float far_z) {
    log_eye_call("IVRSystem::GetProjectionMatrix", eye);
    return fake_projection_matrix(eye, near_z, far_z);
}

vr::HmdMatrix44_t __thiscall fake_legacy_get_projection_matrix(
    void*, vr::EVREye eye, float near_z, float far_z, int32_t
) {
    log_eye_call("IVRSystem_011::GetProjectionMatrix", eye);
    return fake_projection_matrix(eye, near_z, far_z);
}

vr::HmdMatrix44_t* __thiscall fake_cpp_legacy_get_projection_matrix(
    void*, vr::HmdMatrix44_t* output, vr::EVREye eye, float near_z, float far_z, int32_t
) {
    log_eye_call("IVRSystem_011::GetProjectionMatrix", eye);
    if (output) {
        *output = fake_projection_matrix(eye, near_z, far_z);
    }
    return output;
}

vr::HmdMatrix44_t* __thiscall fake_cpp_get_projection_matrix(
    void*, vr::HmdMatrix44_t* output, vr::EVREye eye, float near_z, float far_z
) {
    log_eye_call("IVRSystem::GetProjectionMatrix", eye);
    if (output) {
        *output = fake_projection_matrix(eye, near_z, far_z);
    }
    return output;
}

vr::HmdMatrix44_t __stdcall fake_c_get_projection_matrix(vr::EVREye eye, float near_z, float far_z) {
    return fake_get_projection_matrix(nullptr, eye, near_z, far_z);
}

vr::HmdMatrix44_t __stdcall fake_c_legacy_get_projection_matrix(vr::EVREye eye, float near_z, float far_z, int32_t convention) {
    return fake_legacy_get_projection_matrix(nullptr, eye, near_z, far_z, convention);
}

void __thiscall fake_get_projection_raw(
    void*, vr::EVREye eye, float* left, float* right, float* top, float* bottom
) {
    log_eye_call("IVRSystem::GetProjectionRaw", eye);
    bool shared_view = shared_eye_raw(eye, left, right, top, bottom);
    if (shared_view) {
        log_eye_call("IVRSystem::GetProjectionRaw using shared view", eye);
    } else if (left) {
        *left = -1.0f;
    }
    if (!shared_view && right) {
        *right = 1.0f;
    }
    if (!shared_view && top) {
        *top = -1.0f;
    }
    if (!shared_view && bottom) {
        *bottom = 1.0f;
    }
    log_projection_raw_values(
        "IVRSystem::GetProjectionRaw return",
        eye,
        left ? *left : 0.0f,
        right ? *right : 0.0f,
        top ? *top : 0.0f,
        bottom ? *bottom : 0.0f,
        shared_view
    );
}

void __stdcall fake_c_get_projection_raw(vr::EVREye eye, float* left, float* right, float* top, float* bottom) {
    fake_get_projection_raw(nullptr, eye, left, right, top, bottom);
}

bool __thiscall fake_compute_distortion(
    void*, vr::EVREye, float u, float v, vr::DistortionCoordinates_t* coordinates
) {
    if (InterlockedCompareExchange(&g_logged_compute_distortion, 1, 0) == 0) {
        log_call("IVRSystem::ComputeDistortion");
    }
    if (!coordinates) {
        return false;
    }
    coordinates->rfRed[0] = u;
    coordinates->rfRed[1] = v;
    coordinates->rfGreen[0] = u;
    coordinates->rfGreen[1] = v;
    coordinates->rfBlue[0] = u;
    coordinates->rfBlue[1] = v;
    return true;
}

bool __stdcall fake_c_compute_distortion(vr::EVREye eye, float u, float v, vr::DistortionCoordinates_t* coordinates) {
    return fake_compute_distortion(nullptr, eye, u, v, coordinates);
}

vr::DistortionCoordinates_t __thiscall fake_legacy_compute_distortion(void*, vr::EVREye, float u, float v) {
    if (InterlockedCompareExchange(&g_logged_legacy_compute_distortion, 1, 0) == 0) {
        log_call("IVRSystem_011::ComputeDistortion");
    }
    vr::DistortionCoordinates_t coordinates = {};
    coordinates.rfRed[0] = u;
    coordinates.rfRed[1] = v;
    coordinates.rfGreen[0] = u;
    coordinates.rfGreen[1] = v;
    coordinates.rfBlue[0] = u;
    coordinates.rfBlue[1] = v;
    return coordinates;
}

vr::DistortionCoordinates_t* __thiscall fake_cpp_legacy_compute_distortion(
    void*, vr::DistortionCoordinates_t* output, vr::EVREye eye, float u, float v
) {
    if (InterlockedCompareExchange(&g_logged_legacy_compute_distortion, 1, 0) == 0) {
        log_eye_call("IVRSystem_011::ComputeDistortion", eye);
    }
    if (output) {
        output->rfRed[0] = u;
        output->rfRed[1] = v;
        output->rfGreen[0] = u;
        output->rfGreen[1] = v;
        output->rfBlue[0] = u;
        output->rfBlue[1] = v;
    }
    return output;
}

vr::DistortionCoordinates_t __stdcall fake_c_legacy_compute_distortion(vr::EVREye eye, float u, float v) {
    return fake_legacy_compute_distortion(nullptr, eye, u, v);
}

vr::HmdMatrix34_t __thiscall fake_get_eye_to_head_transform(void*, vr::EVREye eye) {
    log_eye_call("IVRSystem::GetEyeToHeadTransform", eye);
    float eye_x_m = eye == vr::Eye_Left ? -0.032f : 0.032f;
    if (shared_eye_x(eye, &eye_x_m)) {
        log_eye_call("IVRSystem::GetEyeToHeadTransform using shared view", eye);
    }
    vr::HmdMatrix34_t result = identity34(eye_x_m);
    char message[128] = {};
    std::snprintf(
        message, sizeof(message), "IVRSystem::GetEyeToHeadTransform return eye=%d eye_x_m=%.6f", eye, eye_x_m
    );
    log_line(message);
    log_matrix34("IVRSystem::GetEyeToHeadTransform matrix", result);
    return result;
}

vr::HmdMatrix34_t* __thiscall fake_cpp_get_eye_to_head_transform(void*, vr::HmdMatrix34_t* output, vr::EVREye eye) {
    log_eye_call("IVRSystem::GetEyeToHeadTransform", eye);
    if (output) {
        float eye_x_m = eye == vr::Eye_Left ? -0.032f : 0.032f;
        if (shared_eye_x(eye, &eye_x_m)) {
            log_eye_call("IVRSystem::GetEyeToHeadTransform using shared view", eye);
        }
        *output = identity34(eye_x_m);
        char message[128] = {};
        std::snprintf(
            message, sizeof(message), "IVRSystem::GetEyeToHeadTransform return eye=%d eye_x_m=%.6f", eye, eye_x_m
        );
        log_line(message);
        log_matrix34("IVRSystem::GetEyeToHeadTransform matrix", *output);
    }
    return output;
}

vr::HmdMatrix34_t __stdcall fake_c_get_eye_to_head_transform(vr::EVREye eye) {
    return fake_get_eye_to_head_transform(nullptr, eye);
}

bool __thiscall fake_get_time_since_last_vsync(void*, float* seconds, uint64_t* frame_counter) {
    log_call("IVRSystem::GetTimeSinceLastVsync");
    int64_t now_ticks = fake_clock_now_ticks();
    int64_t frequency = fake_clock_frequency();
    uint64_t frame = 0;
    int64_t last_vsync_ticks = 0;
    AcquireSRWLockExclusive(&g_pacer_state_lock);
    ensure_pacer_initialized_locked(now_ticks);
    if (now_ticks >= g_pacer_state.epoch_vsync_ticks) {
        frame = frames_for_ticks(now_ticks - g_pacer_state.epoch_vsync_ticks, frequency);
        last_vsync_ticks = frame_vsync_ticks_locked(frame, frequency);
    } else {
        last_vsync_ticks =
            g_pacer_state.epoch_vsync_ticks - ticks_for_frames(1, frequency);
    }
    ReleaseSRWLockExclusive(&g_pacer_state_lock);
    if (seconds) {
        *seconds = static_cast<float>(ticks_to_seconds(
            std::max<int64_t>(0, now_ticks - last_vsync_ticks), frequency));
    }
    if (frame_counter) {
        *frame_counter = frame;
    }
    return true;
}

bool __stdcall fake_c_get_time_since_last_vsync(float* seconds, uint64_t* frame_counter) {
    return fake_get_time_since_last_vsync(nullptr, seconds, frame_counter);
}

int32_t __thiscall fake_get_d3d9_adapter_index(void*) { return -1; }
int32_t __stdcall fake_c_get_d3d9_adapter_index() { return -1; }

void __thiscall fake_get_dxgi_output_info(void*, int32_t* adapter_index) {
    log_call("IVRSystem::GetDXGIOutputInfo");
    if (adapter_index) {
        *adapter_index = 0;
    }
}

void __stdcall fake_c_get_dxgi_output_info(int32_t* adapter_index) {
    fake_get_dxgi_output_info(nullptr, adapter_index);
}

void __thiscall fake_get_output_device(void*, uint64_t* device, vr::ETextureType, VkInstance_T*) {
    log_call("IVRSystem::GetOutputDevice");
    if (device) {
        *device = 0;
    }
}

void __stdcall fake_c_get_output_device(uint64_t* device, vr::ETextureType texture_type, VkInstance_T* instance) {
    fake_get_output_device(nullptr, device, texture_type, instance);
}

bool __thiscall fake_true(void*) { return true; }
bool __stdcall fake_c_true() { return true; }
bool __thiscall fake_false(void*) { return false; }
bool __stdcall fake_c_false() { return false; }

bool __thiscall fake_is_display_on_desktop(void*) {
    log_call("IVRSystem::IsDisplayOnDesktop -> false");
    return false;
}

bool __stdcall fake_c_is_display_on_desktop() {
    log_call("FnTable:IVRSystem::IsDisplayOnDesktop -> false");
    return false;
}

bool __thiscall fake_set_display_visibility(void*, bool visible) {
    log_call_u32("IVRSystem::SetDisplayVisibility", visible ? 1 : 0);
    return true;
}

bool __stdcall fake_c_set_display_visibility(bool visible) {
    log_call_u32("FnTable:IVRSystem::SetDisplayVisibility", visible ? 1 : 0);
    return true;
}

bool __thiscall fake_capture_input_focus(void*) {
    log_call("IVRSystem::CaptureInputFocus -> true");
    return true;
}

bool __stdcall fake_c_capture_input_focus() {
    log_call("FnTable:IVRSystem::CaptureInputFocus -> true");
    return true;
}

void __thiscall fake_release_input_focus(void*) {
    log_call("IVRSystem::ReleaseInputFocus");
}

void __stdcall fake_c_release_input_focus() {
    log_call("FnTable:IVRSystem::ReleaseInputFocus");
}

bool __thiscall fake_is_input_focus_captured_by_another_process(void*) {
    log_call("IVRSystem::IsInputFocusCapturedByAnotherProcess -> false");
    return false;
}

bool __stdcall fake_c_is_input_focus_captured_by_another_process() {
    log_call("FnTable:IVRSystem::IsInputFocusCapturedByAnotherProcess -> false");
    return false;
}

bool __thiscall fake_is_input_available(void*) {
    log_call("IVRSystem::IsInputAvailable -> true");
    return true;
}

bool __stdcall fake_c_is_input_available() {
    log_call("FnTable:IVRSystem::IsInputAvailable -> true");
    return true;
}

bool __thiscall fake_is_steamvr_drawing_controllers(void*) {
    log_call("IVRSystem::IsSteamVRDrawingControllers -> false");
    return false;
}

bool __stdcall fake_c_is_steamvr_drawing_controllers() {
    log_call("FnTable:IVRSystem::IsSteamVRDrawingControllers -> false");
    return false;
}

bool __thiscall fake_should_application_pause(void*) {
    log_call("IVRSystem::ShouldApplicationPause -> false");
    return false;
}

bool __stdcall fake_c_should_application_pause() {
    log_call("FnTable:IVRSystem::ShouldApplicationPause -> false");
    return false;
}

bool __thiscall fake_should_application_reduce_rendering_work(void*) {
    log_call("IVRSystem::ShouldApplicationReduceRenderingWork -> false");
    return false;
}

bool __stdcall fake_c_should_application_reduce_rendering_work() {
    log_call("FnTable:IVRSystem::ShouldApplicationReduceRenderingWork -> false");
    return false;
}

void __thiscall fake_get_tracking_pose(
    void*, vr::ETrackingUniverseOrigin, float, vr::TrackedDevicePose_t* poses, uint32_t count
) {
    log_call("IVRSystem::GetDeviceToAbsoluteTrackingPose");
    fill_poses(poses, count);
}

void __stdcall fake_c_get_tracking_pose(
    vr::ETrackingUniverseOrigin origin, float predicted_seconds, vr::TrackedDevicePose_t* poses, uint32_t count
) {
    fake_get_tracking_pose(nullptr, origin, predicted_seconds, poses, count);
}

vr::HmdMatrix34_t __thiscall fake_identity34(void*) { return identity34(); }
vr::HmdMatrix34_t* __thiscall fake_cpp_identity34(void*, vr::HmdMatrix34_t* output) {
    if (output) {
        *output = identity34();
    }
    return output;
}
vr::HmdMatrix34_t __stdcall fake_c_identity34() { return identity34(); }
void __thiscall fake_reset_seated_zero_pose(void*) { log_call("IVRSystem::ResetSeatedZeroPose"); }
void __stdcall fake_c_reset_seated_zero_pose() {}

uint32_t __thiscall fake_get_sorted_tracked_device_indices(
    void*, vr::ETrackedDeviceClass device_class, vr::TrackedDeviceIndex_t* indices, uint32_t count, vr::TrackedDeviceIndex_t
) {
    log_call("IVRSystem::GetSortedTrackedDeviceIndicesOfClass");
    if (device_class == vr::TrackedDeviceClass_HMD) {
        if (indices && count > 0) {
            indices[0] = vr::k_unTrackedDeviceIndex_Hmd;
        }
        return 1;
    }
    if (device_class == vr::TrackedDeviceClass_Controller) {
        if (indices && count > 0) {
            indices[0] = 1;
        }
        if (indices && count > 1) {
            indices[1] = 2;
        }
        return 2;
    }
    return 0;
}

uint32_t __stdcall fake_c_get_sorted_tracked_device_indices(
    vr::ETrackedDeviceClass device_class, vr::TrackedDeviceIndex_t* indices, uint32_t count, vr::TrackedDeviceIndex_t relative_to
) {
    return fake_get_sorted_tracked_device_indices(nullptr, device_class, indices, count, relative_to);
}

vr::EDeviceActivityLevel __thiscall fake_get_activity_level(void*, vr::TrackedDeviceIndex_t) {
    log_call("IVRSystem::GetTrackedDeviceActivityLevel");
    return vr::k_EDeviceActivityLevel_UserInteraction;
}

vr::EDeviceActivityLevel __stdcall fake_c_get_activity_level(vr::TrackedDeviceIndex_t device) {
    return fake_get_activity_level(nullptr, device);
}

void __thiscall fake_apply_transform(
    void*, vr::TrackedDevicePose_t* output, const vr::TrackedDevicePose_t* pose, const vr::HmdMatrix34_t* transform
) {
    log_call("IVRSystem::ApplyTransform");
    if (output && pose) {
        *output = *pose;
        if (transform) {
            output->mDeviceToAbsoluteTracking = *transform;
        }
    }
}

void __stdcall fake_c_apply_transform(
    vr::TrackedDevicePose_t* output, vr::TrackedDevicePose_t* pose, vr::HmdMatrix34_t* transform
) {
    fake_apply_transform(nullptr, output, pose, transform);
}

vr::TrackedDeviceIndex_t __thiscall fake_invalid_device_index(void*, vr::ETrackedControllerRole role) {
    log_call("IVRSystem::GetTrackedDeviceIndexForControllerRole");
    if (role == vr::TrackedControllerRole_LeftHand) {
        return 1;
    }
    if (role == vr::TrackedControllerRole_RightHand) {
        return 2;
    }
    return vr::k_unTrackedDeviceIndexInvalid;
}

vr::TrackedDeviceIndex_t __stdcall fake_c_invalid_device_index(vr::ETrackedControllerRole role) {
    return fake_invalid_device_index(nullptr, role);
}

vr::ETrackedControllerRole __thiscall fake_invalid_controller_role(void*, vr::TrackedDeviceIndex_t device) {
    log_call("IVRSystem::GetControllerRoleForTrackedDeviceIndex");
    return fake_controller_role(device);
}

vr::ETrackedControllerRole __stdcall fake_c_invalid_controller_role(vr::TrackedDeviceIndex_t device) {
    return fake_invalid_controller_role(nullptr, device);
}

vr::ETrackedDeviceClass __thiscall fake_get_tracked_device_class(void*, vr::TrackedDeviceIndex_t device) {
    log_call("IVRSystem::GetTrackedDeviceClass");
    if (is_fake_hmd(device)) {
        return vr::TrackedDeviceClass_HMD;
    }
    if (is_fake_controller(device)) {
        return vr::TrackedDeviceClass_Controller;
    }
    return vr::TrackedDeviceClass_Invalid;
}

vr::ETrackedDeviceClass __stdcall fake_c_get_tracked_device_class(vr::TrackedDeviceIndex_t device) {
    return fake_get_tracked_device_class(nullptr, device);
}

bool __thiscall fake_is_tracked_device_connected(void*, vr::TrackedDeviceIndex_t device) {
    log_call_u32("IVRSystem::IsTrackedDeviceConnected device", device);
    return is_fake_tracked_device(device);
}

bool __stdcall fake_c_is_tracked_device_connected(vr::TrackedDeviceIndex_t device) {
    return fake_is_tracked_device_connected(nullptr, device);
}

void set_property_error(vr::ETrackedPropertyError* error, vr::ETrackedPropertyError value) {
    if (error) {
        *error = value;
    }
}

bool __thiscall fake_get_bool_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_device_property("IVRSystem::GetBoolTrackedDeviceProperty", device, prop);
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return false;
    }
    if (prop == vr::Prop_HasDisplayComponent_Bool) {
        set_property_error(error, vr::TrackedProp_Success);
        return is_fake_hmd(device);
    }
    if (prop == vr::Prop_HasControllerComponent_Bool) {
        set_property_error(error, vr::TrackedProp_Success);
        return is_fake_controller(device);
    }
    if (prop == vr::Prop_IsOnDesktop_Bool || prop == vr::Prop_DisplaySuppressed_Bool
        || prop == vr::Prop_NeverTracked_Bool) {
        set_property_error(error, vr::TrackedProp_Success);
        return false;
    }
    if (prop == vr::Prop_WillDriftInYaw_Bool || prop == vr::Prop_DeviceIsWireless_Bool
        || prop == vr::Prop_DeviceProvidesBatteryStatus_Bool) {
        set_property_error(error, vr::TrackedProp_Success);
        return is_fake_controller(device);
    }
    set_property_error(error, vr::TrackedProp_UnknownProperty);
    return false;
}

bool __stdcall fake_c_get_bool_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_bool_property(nullptr, device, prop, error);
}

float __thiscall fake_get_float_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_device_property("IVRSystem::GetFloatTrackedDeviceProperty", device, prop);
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return 0.0f;
    }
    if (prop == vr::Prop_DeviceBatteryPercentage_Float) {
        set_property_error(error, vr::TrackedProp_Success);
        return 1.0f;
    }
    if (!is_fake_hmd(device)) {
        set_property_error(error, vr::TrackedProp_UnknownProperty);
        return 0.0f;
    }
    if (prop == vr::Prop_DisplayFrequency_Float) {
        set_property_error(error, vr::TrackedProp_Success);
        return 90.0f;
    }
    if (prop == vr::Prop_SecondsFromVsyncToPhotons_Float) {
        set_property_error(error, vr::TrackedProp_Success);
        return 0.011f;
    }
    if (prop == vr::Prop_UserIpdMeters_Float) {
        set_property_error(error, vr::TrackedProp_Success);
        return 0.064f;
    }
    set_property_error(error, vr::TrackedProp_UnknownProperty);
    return 0.0f;
}

float __stdcall fake_c_get_float_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_float_property(nullptr, device, prop, error);
}

int32_t __thiscall fake_get_int_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_device_property("IVRSystem::GetInt32TrackedDeviceProperty", device, prop);
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return 0;
    }
    if (prop == vr::Prop_DeviceClass_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        if (is_fake_hmd(device)) {
            return vr::TrackedDeviceClass_HMD;
        }
        if (is_fake_controller(device)) {
            return vr::TrackedDeviceClass_Controller;
        }
        return vr::TrackedDeviceClass_Invalid;
    }
    if (prop == vr::Prop_ControllerRoleHint_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return static_cast<int32_t>(fake_controller_role(device));
    }
    if (prop == vr::Prop_ExpectedControllerCount_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return 2;
    }
    if (is_fake_controller(device) && prop == vr::Prop_Axis0Type_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return vr::k_eControllerAxis_TrackPad;
    }
    if (is_fake_controller(device) && prop == vr::Prop_Axis1Type_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return vr::k_eControllerAxis_Trigger;
    }
    if (is_fake_controller(device) && prop == vr::Prop_Axis2Type_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return vr::k_eControllerAxis_Trigger;
    }
    if (is_fake_controller(device) && prop >= vr::Prop_Axis3Type_Int32 && prop <= vr::Prop_Axis4Type_Int32) {
        set_property_error(error, vr::TrackedProp_Success);
        return vr::k_eControllerAxis_None;
    }
    set_property_error(error, vr::TrackedProp_UnknownProperty);
    return 0;
}

int32_t __stdcall fake_c_get_int_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_int_property(nullptr, device, prop, error);
}

uint64_t __thiscall fake_get_uint64_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_device_property("IVRSystem::GetUint64TrackedDeviceProperty", device, prop);
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return 0;
    }
    if (prop == vr::Prop_CurrentUniverseId_Uint64) {
        set_property_error(error, vr::TrackedProp_Success);
        return 1;
    }
    if (is_fake_controller(device) && prop == vr::Prop_SupportedButtons_Uint64) {
        set_property_error(error, vr::TrackedProp_Success);
        return fake_supported_controller_buttons();
    }
    set_property_error(error, vr::TrackedProp_UnknownProperty);
    return 0;
}

uint64_t __stdcall fake_c_get_uint64_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_uint64_property(nullptr, device, prop, error);
}

vr::HmdMatrix34_t __thiscall fake_get_matrix34_property(void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    log_device_property("IVRSystem::GetMatrix34TrackedDeviceProperty", device, prop);
    set_property_error(error, is_fake_tracked_device(device) ? vr::TrackedProp_UnknownProperty : vr::TrackedProp_InvalidDevice);
    return identity34();
}

vr::HmdMatrix34_t* __thiscall fake_cpp_get_matrix34_property(
    void*,
    vr::HmdMatrix34_t* output,
    vr::TrackedDeviceIndex_t device,
    vr::ETrackedDeviceProperty prop,
    vr::ETrackedPropertyError* error
) {
    log_device_property("IVRSystem::GetMatrix34TrackedDeviceProperty", device, prop);
    set_property_error(error, is_fake_tracked_device(device) ? vr::TrackedProp_UnknownProperty : vr::TrackedProp_InvalidDevice);
    if (output) {
        *output = identity34();
    }
    return output;
}

vr::HmdMatrix34_t __stdcall fake_c_get_matrix34_property(vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::ETrackedPropertyError* error) {
    return fake_get_matrix34_property(nullptr, device, prop, error);
}

uint32_t __thiscall fake_get_array_property(
    void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::PropertyTypeTag_t, void*, uint32_t, vr::ETrackedPropertyError* error
) {
    log_device_property("IVRSystem::GetArrayTrackedDeviceProperty", device, prop);
    set_property_error(error, is_fake_tracked_device(device) ? vr::TrackedProp_UnknownProperty : vr::TrackedProp_InvalidDevice);
    return 0;
}

uint32_t __stdcall fake_c_get_array_property(
    vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, vr::PropertyTypeTag_t type, void* buffer, uint32_t size, vr::ETrackedPropertyError* error
) {
    return fake_get_array_property(nullptr, device, prop, type, buffer, size, error);
}

uint32_t __thiscall fake_get_string_property(
    void*, vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, char* value, uint32_t value_size, vr::ETrackedPropertyError* error
) {
    log_device_property("IVRSystem::GetStringTrackedDeviceProperty", device, prop);
    if (!is_fake_tracked_device(device)) {
        set_property_error(error, vr::TrackedProp_InvalidDevice);
        return 0;
    }

    const char* text = nullptr;
    if (prop == vr::Prop_TrackingSystemName_String) {
        text = "FakeOpenVR";
    } else if (prop == vr::Prop_ModelNumber_String) {
        text = is_fake_hmd(device) ? "FakeOpenVR Null HMD" : "FakeOpenVR Controller";
    } else if (prop == vr::Prop_SerialNumber_String) {
        text = is_fake_hmd(device) ? "FAKEOPENVR0001" : (device == 1 ? "FAKEOPENVRCTRLLEFT" : "FAKEOPENVRCTRLRIGHT");
    } else if (prop == vr::Prop_ManufacturerName_String) {
        text = "FakeOpenVR";
    } else if (prop == vr::Prop_RenderModelName_String) {
        text = is_fake_hmd(device) ? "fake_hmd" : "vr_controller_vive_1_5";
    } else if (prop == vr::Prop_ControllerType_String) {
        text = is_fake_controller(device) ? "vive_controller" : "";
    } else if (prop == vr::Prop_RegisteredDeviceType_String) {
        text = is_fake_hmd(device) ? "fake/hmd" : (device == 1 ? "htc/vive_controller_left" : "htc/vive_controller_right");
    } else if (prop == vr::Prop_InputProfilePath_String) {
        text = is_fake_controller(device) ? "{htc}/input/vive_controller_profile.json" : "";
    }
    if (!text) {
        set_property_error(error, vr::TrackedProp_UnknownProperty);
        return 0;
    }

    set_property_error(error, vr::TrackedProp_Success);
    copy_string(text, value, value_size);
    return static_cast<uint32_t>(std::strlen(text) + 1);
}

uint32_t __stdcall fake_c_get_string_property(
    vr::TrackedDeviceIndex_t device, vr::ETrackedDeviceProperty prop, char* value, uint32_t value_size, vr::ETrackedPropertyError* error
) {
    return fake_get_string_property(nullptr, device, prop, value, value_size, error);
}

const char* __thiscall fake_get_prop_error_name(void*, vr::ETrackedPropertyError error) {
    log_call("IVRSystem::GetPropErrorNameFromEnum");
    switch (error) {
    case vr::TrackedProp_Success:
        return "TrackedProp_Success";
    case vr::TrackedProp_InvalidDevice:
        return "TrackedProp_InvalidDevice";
    case vr::TrackedProp_UnknownProperty:
        return "TrackedProp_UnknownProperty";
    default:
        return "TrackedProp_Error";
    }
}

const char* __stdcall fake_c_get_prop_error_name(vr::ETrackedPropertyError error) {
    return fake_get_prop_error_name(nullptr, error);
}

bool __thiscall fake_poll_next_event(void*, vr::VREvent_t* event, uint32_t event_size) {
    log_call_u32("IVRSystem::PollNextEvent size", event_size);
    if (!event || event_size < kVREventPrefixSize) {
        return false;
    }
    LONG index = InterlockedIncrement(&g_fake_event_index) - 1;
    if (index < 0 || static_cast<size_t>(index) >= (sizeof(kStartupEvents) / sizeof(kStartupEvents[0]))) {
        if (InterlockedCompareExchange(&g_logged_poll_next_event_empty, 1, 0) == 0) {
            log_call("IVRSystem::PollNextEvent empty");
        }
        return false;
    }

    const FakeEventSeed& seed = kStartupEvents[index];
    log_call_u32_pair("IVRSystem::PollNextEvent event", static_cast<uint32_t>(seed.type), seed.device);
    vr::VREvent_t output = {};
    output.eventType = seed.type;
    output.trackedDeviceIndex = seed.device;
    if (seed.type == vr::VREvent_InputFocusCaptured
        || seed.type == vr::VREvent_SceneApplicationChanged
        || seed.type == kSceneFocusChangedEvent
        || seed.type == vr::VREvent_InputFocusChanged) {
        output.data.process.pid = GetCurrentProcessId();
    }
    std::memcpy(event, &output, std::min<size_t>(event_size, sizeof(output)));
    return true;
}
bool __stdcall fake_c_poll_next_event(vr::VREvent_t* event, uint32_t event_size) {
    return fake_poll_next_event(nullptr, event, event_size);
}

bool __thiscall fake_poll_next_event_with_pose(
    void*, vr::ETrackingUniverseOrigin, vr::VREvent_t* event, uint32_t event_size, vr::TrackedDevicePose_t* pose
) {
    fill_pose(pose, vr::k_unTrackedDeviceIndex_Hmd);
    return fake_poll_next_event(nullptr, event, event_size);
}

bool __stdcall fake_c_poll_next_event_with_pose(
    vr::ETrackingUniverseOrigin origin, vr::VREvent_t* event, uint32_t event_size, vr::TrackedDevicePose_t* pose
) {
    return fake_poll_next_event_with_pose(nullptr, origin, event, event_size, pose);
}

const char* __thiscall fake_get_event_type_name(void*, vr::EVREventType) { return "VREvent_None"; }
const char* __stdcall fake_c_get_event_type_name(vr::EVREventType type) {
    return fake_get_event_type_name(nullptr, type);
}

vr::HiddenAreaMesh_t __thiscall fake_get_hidden_area_mesh(void*, vr::EVREye, vr::EHiddenAreaMeshType) {
    vr::HiddenAreaMesh_t mesh = {};
    return mesh;
}

vr::HiddenAreaMesh_t __thiscall fake_legacy_get_hidden_area_mesh(void*, vr::EVREye) {
    vr::HiddenAreaMesh_t mesh = {};
    return mesh;
}

vr::HiddenAreaMesh_t* __thiscall fake_cpp_get_hidden_area_mesh(
    void*, vr::HiddenAreaMesh_t* output, vr::EVREye, vr::EHiddenAreaMeshType
) {
    log_call("IVRSystem::GetHiddenAreaMesh");
    if (output) {
        std::memset(output, 0, sizeof(*output));
    }
    return output;
}

vr::HiddenAreaMesh_t* __thiscall fake_cpp_legacy_get_hidden_area_mesh(
    void*, vr::HiddenAreaMesh_t* output, vr::EVREye
) {
    log_call("IVRSystem_011::GetHiddenAreaMesh");
    if (output) {
        std::memset(output, 0, sizeof(*output));
    }
    return output;
}

vr::HiddenAreaMesh_t __stdcall fake_c_get_hidden_area_mesh(vr::EVREye eye, vr::EHiddenAreaMeshType type) {
    return fake_get_hidden_area_mesh(nullptr, eye, type);
}

vr::HiddenAreaMesh_t __stdcall fake_c_legacy_get_hidden_area_mesh(vr::EVREye eye) {
    return fake_legacy_get_hidden_area_mesh(nullptr, eye);
}

void log_input_action_query(vr::VRInputValueHandle_t restrict_to_device) {
    int first_controller = 0;
    int last_controller = ALVR_NUM_CONTROLLERS - 1;
    if (restrict_to_device == 1 || restrict_to_device == 2) {
        first_controller = restrict_to_device == 2 ? 1 : 0;
        last_controller = first_controller;
    } else if (restrict_to_device != vr::k_ulInvalidInputValueHandle) {
        return;
    }

    for (int controller_index = first_controller;
         controller_index <= last_controller;
         ++controller_index) {
        if (InterlockedCompareExchange(
                &g_logged_input_action_query[controller_index], 1, 0
            ) != 0) {
            continue;
        }
        char message[128] = {};
        std::snprintf(
            message,
            sizeof(message),
            "OpenVR input action queried hand=%s",
            controller_index == 0 ? "left" : "right"
        );
        log_line(message);
    }
}

void log_shared_controller_input_activity(
    int controller_index,
    const SharedControllerSnapshot& controller,
    const char* source
) {
    const char* hand = controller_index == 0 ? "left" : "right";
    if (controller.buttons_pressed != 0
        && InterlockedCompareExchange(
               &g_logged_controller_button[controller_index], 1, 0
           ) == 0) {
        char message[224] = {};
        std::snprintf(
            message,
            sizeof(message),
            "shared controller button hand=%s source=%s pressed=0x%llx touched=0x%llx",
            hand,
            source,
            static_cast<unsigned long long>(controller.buttons_pressed),
            static_cast<unsigned long long>(controller.buttons_touched)
        );
        log_line(message);
    }
    if ((std::fabs(controller.axes[0][0]) > 0.1f
            || std::fabs(controller.axes[0][1]) > 0.1f)
        && InterlockedCompareExchange(
               &g_logged_controller_thumbstick[controller_index], 1, 0
           ) == 0) {
        char message[224] = {};
        std::snprintf(
            message,
            sizeof(message),
            "shared controller thumbstick hand=%s source=%s x=%.3f y=%.3f",
            hand,
            source,
            controller.axes[0][0],
            controller.axes[0][1]
        );
        log_line(message);
    }
    if (controller.axes[1][0] > 0.1f
        && InterlockedCompareExchange(
               &g_logged_controller_trigger[controller_index], 1, 0
           ) == 0) {
        char message[192] = {};
        std::snprintf(
            message,
            sizeof(message),
            "shared controller trigger hand=%s source=%s value=%.3f",
            hand,
            source,
            controller.axes[1][0]
        );
        log_line(message);
    }
}

bool __thiscall fake_get_controller_state(void*, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, uint32_t state_size) {
    int controller_index = shared_controller_index(device);
    if (controller_index >= 0
        && InterlockedCompareExchange(&g_logged_controller_query[controller_index], 1, 0)
            == 0) {
        char message[128] = {};
        std::snprintf(
            message,
            sizeof(message),
            "OpenVR controller state queried hand=%s",
            controller_index == 0 ? "left" : "right"
        );
        log_line(message);
    }
    SharedControllerSnapshot controller;
    const bool connected = read_shared_controller_state(device, &controller);
    if (state && state_size > 0) {
        vr::VRControllerState_t output = {};
        if (connected) {
            output.unPacketNum = controller.packet_number;
            output.ulButtonPressed = controller.buttons_pressed;
            output.ulButtonTouched = controller.buttons_touched;
            for (int axis = 0; axis < 5; ++axis) {
                output.rAxis[axis].x = controller.axes[axis][0];
                output.rAxis[axis].y = controller.axes[axis][1];
            }
        }
        std::memcpy(state, &output, std::min<size_t>(state_size, sizeof(output)));
    }
    if (connected) {
        log_shared_controller_input_activity(controller_index, controller, "legacy-state");
    }
    return is_fake_controller(device);
}

bool __thiscall fake_legacy_get_controller_state(void*, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state) {
    return fake_get_controller_state(nullptr, device, state, sizeof(vr::VRControllerState_t));
}

bool __stdcall fake_c_get_controller_state(vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, uint32_t state_size) {
    return fake_get_controller_state(nullptr, device, state, state_size);
}

bool __stdcall fake_c_legacy_get_controller_state(vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state) {
    return fake_legacy_get_controller_state(nullptr, device, state);
}

bool __thiscall fake_get_controller_state_with_pose(
    void*, vr::ETrackingUniverseOrigin, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, uint32_t state_size, vr::TrackedDevicePose_t* pose
) {
    const bool ok = fake_get_controller_state(nullptr, device, state, state_size);
    if (is_fake_tracked_device(device)) {
        fill_pose(pose, device);
    } else if (pose) {
        std::memset(pose, 0, sizeof(*pose));
    }
    return ok;
}

bool __thiscall fake_legacy_get_controller_state_with_pose(
    void*, vr::ETrackingUniverseOrigin origin, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, vr::TrackedDevicePose_t* pose
) {
    return fake_get_controller_state_with_pose(nullptr, origin, device, state, sizeof(vr::VRControllerState_t), pose);
}

bool __stdcall fake_c_get_controller_state_with_pose(
    vr::ETrackingUniverseOrigin origin, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, uint32_t state_size, vr::TrackedDevicePose_t* pose
) {
    return fake_get_controller_state_with_pose(nullptr, origin, device, state, state_size, pose);
}

bool __stdcall fake_c_legacy_get_controller_state_with_pose(
    vr::ETrackingUniverseOrigin origin, vr::TrackedDeviceIndex_t device, vr::VRControllerState_t* state, vr::TrackedDevicePose_t* pose
) {
    return fake_legacy_get_controller_state_with_pose(nullptr, origin, device, state, pose);
}

const char* __thiscall fake_button_name(void*, vr::EVRButtonId) { return "Unknown"; }
const char* __stdcall fake_c_button_name(vr::EVRButtonId button) { return fake_button_name(nullptr, button); }
const char* __thiscall fake_axis_name(void*, vr::EVRControllerAxisType) { return "Unknown"; }
const char* __stdcall fake_c_axis_name(vr::EVRControllerAxisType axis) { return fake_axis_name(nullptr, axis); }
void __thiscall fake_trigger_haptic_pulse(void*, vr::TrackedDeviceIndex_t, uint32_t, unsigned short) {}
void __stdcall fake_c_trigger_haptic_pulse(vr::TrackedDeviceIndex_t, uint32_t, unsigned short) {}
uint32_t __thiscall fake_driver_debug_request(
    void*, vr::TrackedDeviceIndex_t, const char*, char* response, uint32_t response_size
) {
    copy_string("", response, response_size);
    return 1;
}
uint32_t __stdcall fake_c_driver_debug_request(
    vr::TrackedDeviceIndex_t device, const char* request, char* response, uint32_t response_size
) {
    return fake_driver_debug_request(nullptr, device, request, response, response_size);
}
vr::EVRFirmwareError __thiscall fake_firmware_update(void*, vr::TrackedDeviceIndex_t) { return vr::VRFirmwareError_None; }
vr::EVRFirmwareError __stdcall fake_c_firmware_update(vr::TrackedDeviceIndex_t device) {
    return fake_firmware_update(nullptr, device);
}
void __thiscall fake_acknowledge_quit(void*) {}
void __stdcall fake_c_acknowledge_quit() {}
void __thiscall fake_performance_test_enable_capture(void*, bool) {}
void __thiscall fake_performance_test_report_fidelity(void*, int) {}
void __stdcall fake_c_performance_test_enable_capture(bool) {}
void __stdcall fake_c_performance_test_report_fidelity(int) {}
uint32_t __thiscall fake_get_app_container_file_paths(void*, char* buffer, uint32_t size) {
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_get_app_container_file_paths(char* buffer, uint32_t size) {
    return fake_get_app_container_file_paths(nullptr, buffer, size);
}
const char* __thiscall fake_get_runtime_version(void*) { return "FakeOpenVR_022"; }
const char* __stdcall fake_c_get_runtime_version() { return "FakeOpenVR_022"; }

vr::ChaperoneCalibrationState __thiscall fake_get_chaperone_calibration_state(void*) {
    log_call("IVRChaperone::GetCalibrationState");
    return vr::ChaperoneCalibrationState_OK;
}

vr::ChaperoneCalibrationState __stdcall fake_c_get_chaperone_calibration_state() {
    log_call("FnTable:IVRChaperone::GetCalibrationState");
    return vr::ChaperoneCalibrationState_OK;
}

bool __thiscall fake_get_play_area_size(void*, float* size_x, float* size_z) {
    log_call("IVRChaperone::GetPlayAreaSize");
    if (size_x) {
        *size_x = 2.0f;
    }
    if (size_z) {
        *size_z = 2.0f;
    }
    return true;
}

bool __stdcall fake_c_get_play_area_size(float* size_x, float* size_z) {
    log_call("FnTable:IVRChaperone::GetPlayAreaSize");
    return fake_get_play_area_size(nullptr, size_x, size_z);
}

bool __thiscall fake_get_play_area_rect(void*, vr::HmdQuad_t* rect) {
    log_call("IVRChaperone::GetPlayAreaRect");
    if (rect) {
        std::memset(rect, 0, sizeof(*rect));
        rect->vCorners[0].v[0] = -1.0f;
        rect->vCorners[0].v[2] = -1.0f;
        rect->vCorners[1].v[0] = 1.0f;
        rect->vCorners[1].v[2] = -1.0f;
        rect->vCorners[2].v[0] = 1.0f;
        rect->vCorners[2].v[2] = 1.0f;
        rect->vCorners[3].v[0] = -1.0f;
        rect->vCorners[3].v[2] = 1.0f;
    }
    return true;
}

bool __stdcall fake_c_get_play_area_rect(vr::HmdQuad_t* rect) {
    log_call("FnTable:IVRChaperone::GetPlayAreaRect");
    return fake_get_play_area_rect(nullptr, rect);
}

void __thiscall fake_noop_void(void*) {}
void __stdcall fake_c_noop_void() {}
void __thiscall fake_noop_bool(void*, bool) {}
void __stdcall fake_c_noop_bool(bool) {}
void __thiscall fake_noop_color(void*, vr::HmdColor_t) {}
void __stdcall fake_c_noop_color(vr::HmdColor_t) {}

void __thiscall fake_get_bounds_color(
    void*, vr::HmdColor_t* colors, int count, float, vr::HmdColor_t* camera_color
) {
    log_call("IVRChaperone::GetBoundsColor");
    for (int index = 0; colors && index < count; ++index) {
        colors[index] = { 0.0f, 0.6f, 1.0f, 1.0f };
    }
    if (camera_color) {
        *camera_color = { 0.0f, 0.6f, 1.0f, 1.0f };
    }
}

void __stdcall fake_c_get_bounds_color(
    vr::HmdColor_t* colors, int count, float fade_distance, vr::HmdColor_t* camera_color
) {
    fake_get_bounds_color(nullptr, colors, count, fade_distance, camera_color);
}

bool __thiscall fake_commit_working_copy(void*, vr::EChaperoneConfigFile) { return true; }
bool __stdcall fake_c_commit_working_copy(vr::EChaperoneConfigFile) { return true; }
void __thiscall fake_reload_from_disk(void*, vr::EChaperoneConfigFile) {}
void __stdcall fake_c_reload_from_disk(vr::EChaperoneConfigFile) {}
bool __thiscall fake_get_pose34(void*, vr::HmdMatrix34_t* pose) {
    if (pose) {
        *pose = identity34();
    }
    return true;
}
bool __stdcall fake_c_get_pose34(vr::HmdMatrix34_t* pose) { return fake_get_pose34(nullptr, pose); }
bool __thiscall fake_collision_bounds(void*, vr::HmdQuad_t* buffer, uint32_t* count) {
    constexpr uint32_t bounds_count = 1;
    if (!count) {
        return false;
    }
    if (!buffer) {
        *count = bounds_count;
        return true;
    }

    uint32_t capacity = *count;
    *count = bounds_count;
    if (capacity < bounds_count) {
        return false;
    }

    return fake_get_play_area_rect(nullptr, buffer);
}
bool __stdcall fake_c_collision_bounds(vr::HmdQuad_t* buffer, uint32_t* count) {
    return fake_collision_bounds(nullptr, buffer, count);
}
void __thiscall fake_set_play_area_size(void*, float, float) {}
void __stdcall fake_c_set_play_area_size(float, float) {}
void __thiscall fake_set_collision_bounds(void*, vr::HmdQuad_t*, uint32_t) {}
void __stdcall fake_c_set_collision_bounds(vr::HmdQuad_t*, uint32_t) {}
void __thiscall fake_set_perimeter(void*, vr::HmdVector2_t*, uint32_t) {}
void __stdcall fake_c_set_perimeter(vr::HmdVector2_t*, uint32_t) {}
void __thiscall fake_set_pose34(void*, const vr::HmdMatrix34_t*) {}
void __stdcall fake_c_set_pose34(const vr::HmdMatrix34_t*) {}
void __thiscall fake_set_tags(void*, uint8_t*, uint32_t) {}
void __stdcall fake_c_set_tags(uint8_t*, uint32_t) {}
bool __thiscall fake_get_tags(void*, uint8_t*, uint32_t* count) {
    if (count) {
        *count = 0;
    }
    return true;
}
bool __stdcall fake_c_get_tags(uint8_t* buffer, uint32_t* count) { return fake_get_tags(nullptr, buffer, count); }
bool __thiscall fake_set_physical_bounds(void*, vr::HmdQuad_t*, uint32_t) { return true; }
bool __stdcall fake_c_set_physical_bounds(vr::HmdQuad_t*, uint32_t) { return true; }
bool __thiscall fake_export_buffer(void*, char* buffer, uint32_t* length) {
    if (length) {
        *length = 1;
    }
    if (buffer) {
        buffer[0] = '\0';
    }
    return true;
}
bool __stdcall fake_c_export_buffer(char* buffer, uint32_t* length) {
    return fake_export_buffer(nullptr, buffer, length);
}
bool __thiscall fake_import_buffer(void*, const char*, uint32_t) { return true; }
bool __stdcall fake_c_import_buffer(const char*, uint32_t) { return true; }

vr::EVROverlayError __thiscall fake_overlay_error(void*) { return vr::VROverlayError_UnknownOverlay; }
vr::EVROverlayError __stdcall fake_c_overlay_error() { return vr::VROverlayError_UnknownOverlay; }
vr::EVROverlayError __thiscall fake_overlay_find(void*, const char*, vr::VROverlayHandle_t* handle) {
    if (handle) {
        *handle = vr::k_ulOverlayHandleInvalid;
    }
    return vr::VROverlayError_UnknownOverlay;
}
vr::EVROverlayError __stdcall fake_c_overlay_find(const char*, vr::VROverlayHandle_t* handle) {
    return fake_overlay_find(nullptr, nullptr, handle);
}
vr::EVROverlayError __thiscall fake_overlay_create(void*, const char*, const char*, vr::VROverlayHandle_t* handle) {
    if (handle) {
        *handle = vr::k_ulOverlayHandleInvalid;
    }
    return vr::VROverlayError_UnknownOverlay;
}
vr::EVROverlayError __stdcall fake_c_overlay_create(const char*, const char*, vr::VROverlayHandle_t* handle) {
    return fake_overlay_create(nullptr, nullptr, nullptr, handle);
}
vr::VROverlayHandle_t __thiscall fake_overlay_handle(void*) { return vr::k_ulOverlayHandleInvalid; }
vr::VROverlayHandle_t __stdcall fake_c_overlay_handle() { return vr::k_ulOverlayHandleInvalid; }
const char* __thiscall fake_overlay_error_name(void*, vr::EVROverlayError) {
    log_call("IVROverlay::GetOverlayErrorNameFromEnum");
    return "FakeOverlay";
}
const char* __stdcall fake_c_overlay_error_name(vr::EVROverlayError error) {
    log_call("FnTable:IVROverlay::GetOverlayErrorNameFromEnum");
    return fake_overlay_error_name(nullptr, error);
}
uint32_t __thiscall fake_overlay_string(void*, vr::VROverlayHandle_t, char* buffer, uint32_t size, vr::EVROverlayError* error) {
    log_call("IVROverlay::GetOverlayString");
    if (error) {
        *error = vr::VROverlayError_UnknownOverlay;
    }
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_overlay_string(vr::VROverlayHandle_t handle, char* buffer, uint32_t size, vr::EVROverlayError* error) {
    log_call("FnTable:IVROverlay::GetOverlayString");
    return fake_overlay_string(nullptr, handle, buffer, size, error);
}
bool __thiscall fake_overlay_visible(void*, vr::VROverlayHandle_t) {
    log_call("IVROverlay::IsOverlayVisible");
    return false;
}
bool __stdcall fake_c_overlay_visible(vr::VROverlayHandle_t) {
    log_call("FnTable:IVROverlay::IsOverlayVisible");
    return false;
}
bool __thiscall fake_overlay_poll(void*, vr::VROverlayHandle_t, vr::VREvent_t*, uint32_t) {
    log_call("IVROverlay::PollNextOverlayEvent");
    return false;
}
bool __stdcall fake_c_overlay_poll(vr::VROverlayHandle_t, vr::VREvent_t*, uint32_t) {
    log_call("FnTable:IVROverlay::PollNextOverlayEvent");
    return false;
}
uint32_t __thiscall fake_overlay_pid(void*, vr::VROverlayHandle_t) {
    log_call("IVROverlay::GetOverlayRenderingPid");
    return 0;
}
uint32_t __stdcall fake_c_overlay_pid(vr::VROverlayHandle_t) {
    log_call("FnTable:IVROverlay::GetOverlayRenderingPid");
    return 0;
}

void __thiscall fake_set_tracking_space(void*, vr::ETrackingUniverseOrigin origin) {
    log_call_u32("IVRCompositor::SetTrackingSpace", static_cast<uint32_t>(origin));
    g_tracking_space_origin.store(static_cast<uint32_t>(origin), std::memory_order_relaxed);
}
void __stdcall fake_c_set_tracking_space(vr::ETrackingUniverseOrigin origin) {
    log_call_u32("FnTable:IVRCompositor::SetTrackingSpace", static_cast<uint32_t>(origin));
    g_tracking_space_origin.store(static_cast<uint32_t>(origin), std::memory_order_relaxed);
}
vr::ETrackingUniverseOrigin __thiscall fake_tracking_space(void*) {
    log_call("IVRCompositor::GetTrackingSpace");
    auto origin = static_cast<vr::ETrackingUniverseOrigin>(
        g_tracking_space_origin.load(std::memory_order_relaxed)
    );
    char message[128] = {};
    std::snprintf(
        message,
        sizeof(message),
        "IVRCompositor::GetTrackingSpace return origin=%d",
        origin
    );
    log_line(message);
    return origin;
}

vr::ETrackingUniverseOrigin __stdcall fake_c_tracking_space() {
    log_call("FnTable:IVRCompositor::GetTrackingSpace");
    auto origin = static_cast<vr::ETrackingUniverseOrigin>(
        g_tracking_space_origin.load(std::memory_order_relaxed)
    );
    char message[128] = {};
    std::snprintf(
        message,
        sizeof(message),
        "FnTable:IVRCompositor::GetTrackingSpace return origin=%d",
        origin
    );
    log_line(message);
    return origin;
}

vr::EVRCompositorError __thiscall fake_wait_get_poses(
    void*, vr::TrackedDevicePose_t* render_poses, uint32_t render_count, vr::TrackedDevicePose_t* game_poses, uint32_t game_count
) {
    FakePacingReport pacing = wait_for_running_start();
    if (pacing.call == 1) {
        log_call("IVRCompositor::WaitGetPoses");
        log_call_u32_pair("IVRCompositor::WaitGetPoses counts", render_count, game_count);
    }
    SharedHmdPose render_shared_pose;
    const SharedHmdPose* render_pose_snapshot =
        read_shared_hmd_pose(&render_shared_pose) ? &render_shared_pose : nullptr;
    if (render_pose_snapshot) {
        g_shared_pose_reads.fetch_add(1, std::memory_order_relaxed);
    }
    uint64_t pose_generation = publish_frame_pose_snapshot(render_pose_snapshot);
    fill_poses_with_snapshot(render_poses, render_count, render_pose_snapshot);
    SharedHmdPose game_shared_pose;
    const SharedHmdPose* game_pose_snapshot =
        read_shared_hmd_pose(&game_shared_pose) ? &game_shared_pose : nullptr;
    fill_poses_with_snapshot(game_poses, game_count, game_pose_snapshot);
    if (pacing.call == 1 && render_poses && render_count > 0) {
        char message[320] = {};
        std::snprintf(
            message,
            sizeof(message),
            "IVRCompositor::WaitGetPoses contract pose_generation=%llu pose_timestamp_ns=%llu hmd_valid=%u tracking_result=%u connected=%u source=%s",
            static_cast<unsigned long long>(pose_generation),
            static_cast<unsigned long long>(
                render_pose_snapshot ? render_pose_snapshot->timestamp_ns : 0
            ),
            render_poses[0].bPoseIsValid ? 1U : 0U,
            static_cast<uint32_t>(render_poses[0].eTrackingResult),
            render_poses[0].bDeviceIsConnected ? 1U : 0U,
            render_pose_snapshot ? "shared-hmd-pose" : "fallback-identity"
        );
        log_line(message);
        log_matrix34("IVRCompositor::WaitGetPoses hmd_pose", render_poses[0].mDeviceToAbsoluteTracking);
    }
    if (pacing.should_log) {
        log_pacing_report(pacing);
    }
    return vr::VRCompositorError_None;
}

vr::EVRCompositorError __thiscall fake_get_last_poses(
    void*, vr::TrackedDevicePose_t* render_poses, uint32_t render_count, vr::TrackedDevicePose_t* game_poses, uint32_t game_count
) {
    log_call("IVRCompositor::GetLastPoses");
    SharedHmdPose render_shared_pose;
    const SharedHmdPose* render_pose_snapshot =
        read_shared_hmd_pose(&render_shared_pose) ? &render_shared_pose : nullptr;
    uint64_t pose_generation = publish_frame_pose_snapshot(render_pose_snapshot);
    fill_poses_with_snapshot(render_poses, render_count, render_pose_snapshot);
    SharedHmdPose game_shared_pose;
    const SharedHmdPose* game_pose_snapshot =
        read_shared_hmd_pose(&game_shared_pose) ? &game_shared_pose : nullptr;
    fill_poses_with_snapshot(game_poses, game_count, game_pose_snapshot);
    if (render_poses && render_count > 0) {
        char message[320] = {};
        std::snprintf(
            message,
            sizeof(message),
            "IVRCompositor::GetLastPoses contract pose_generation=%llu pose_timestamp_ns=%llu hmd_valid=%u tracking_result=%u connected=%u source=%s",
            static_cast<unsigned long long>(pose_generation),
            static_cast<unsigned long long>(
                render_pose_snapshot ? render_pose_snapshot->timestamp_ns : 0
            ),
            render_poses[0].bPoseIsValid ? 1U : 0U,
            static_cast<uint32_t>(render_poses[0].eTrackingResult),
            render_poses[0].bDeviceIsConnected ? 1U : 0U,
            render_pose_snapshot ? "shared-hmd-pose" : "fallback-identity"
        );
        log_line(message);
        log_matrix34("IVRCompositor::GetLastPoses hmd_pose", render_poses[0].mDeviceToAbsoluteTracking);
    }
    return vr::VRCompositorError_None;
}

vr::EVRCompositorError __stdcall fake_c_wait_get_poses(
    vr::TrackedDevicePose_t* render_poses, uint32_t render_count, vr::TrackedDevicePose_t* game_poses, uint32_t game_count
) {
    return fake_wait_get_poses(nullptr, render_poses, render_count, game_poses, game_count);
}

vr::EVRCompositorError __stdcall fake_c_get_last_poses(
    vr::TrackedDevicePose_t* render_poses, uint32_t render_count, vr::TrackedDevicePose_t* game_poses, uint32_t game_count
) {
    return fake_get_last_poses(nullptr, render_poses, render_count, game_poses, game_count);
}

vr::EVRCompositorError __thiscall fake_last_pose(
    void*, vr::TrackedDeviceIndex_t device, vr::TrackedDevicePose_t* render_pose, vr::TrackedDevicePose_t* game_pose
) {
    log_call("IVRCompositor::GetLastPoseForTrackedDeviceIndex");
    bool hmd_pose = device == vr::k_unTrackedDeviceIndex_Hmd;
    SharedHmdPose render_shared_pose;
    const SharedHmdPose* render_pose_snapshot =
        hmd_pose && read_shared_hmd_pose(&render_shared_pose) ? &render_shared_pose : nullptr;
    SharedControllerSnapshot render_controller;
    const SharedControllerSnapshot* render_controller_snapshot =
        !hmd_pose && read_shared_controller_state(device, &render_controller)
        ? &render_controller
        : nullptr;
    uint64_t pose_generation = publish_frame_pose_snapshot(render_pose_snapshot);
    fill_pose_with_snapshot(render_pose, device, render_pose_snapshot, render_controller_snapshot);
    SharedHmdPose game_shared_pose;
    const SharedHmdPose* game_pose_snapshot =
        hmd_pose && read_shared_hmd_pose(&game_shared_pose) ? &game_shared_pose : nullptr;
    SharedControllerSnapshot game_controller;
    const SharedControllerSnapshot* game_controller_snapshot =
        !hmd_pose && read_shared_controller_state(device, &game_controller)
        ? &game_controller
        : nullptr;
    fill_pose_with_snapshot(game_pose, device, game_pose_snapshot, game_controller_snapshot);
    if (render_pose) {
        char message[384] = {};
        std::snprintf(
            message,
            sizeof(message),
            "IVRCompositor::GetLastPoseForTrackedDeviceIndex contract device=%u pose_generation=%llu pose_timestamp_ns=%llu hmd_valid=%u tracking_result=%u connected=%u source=%s",
            device,
            static_cast<unsigned long long>(pose_generation),
            static_cast<unsigned long long>(
                render_pose_snapshot ? render_pose_snapshot->timestamp_ns : 0
            ),
            render_pose->bPoseIsValid ? 1U : 0U,
            static_cast<uint32_t>(render_pose->eTrackingResult),
            render_pose->bDeviceIsConnected ? 1U : 0U,
            render_pose_snapshot ? "shared-hmd-pose" : "fallback-identity"
        );
        log_line(message);
        log_matrix34(
            "IVRCompositor::GetLastPoseForTrackedDeviceIndex hmd_pose",
            render_pose->mDeviceToAbsoluteTracking
        );
    }
    return vr::VRCompositorError_None;
}

vr::EVRCompositorError __stdcall fake_c_last_pose(
    vr::TrackedDeviceIndex_t device, vr::TrackedDevicePose_t* render_pose, vr::TrackedDevicePose_t* game_pose
) {
    return fake_last_pose(nullptr, device, render_pose, game_pose);
}

vr::EVRCompositorError __thiscall fake_submit(
    void*, vr::EVREye eye, const vr::Texture_t*, const vr::VRTextureBounds_t*, vr::EVRSubmitFlags
) {
    log_eye_call("IVRCompositor::Submit", eye);
    return vr::VRCompositorError_None;
}

vr::EVRCompositorError __stdcall fake_c_submit(
    vr::EVREye eye, vr::Texture_t*, vr::VRTextureBounds_t*, vr::EVRSubmitFlags
) {
    log_eye_call("FnTable:IVRCompositor::Submit", eye);
    return vr::VRCompositorError_None;
}

void __thiscall fake_post_present_handoff(void*) { log_call("IVRCompositor::<void>"); }
void __stdcall fake_c_post_present_handoff() { log_call("FnTable:IVRCompositor::<void>"); }
void __thiscall fake_clear_last_submitted_frame(void*) { log_call("IVRCompositor::ClearLastSubmittedFrame"); }
void __stdcall fake_c_clear_last_submitted_frame() { log_call("FnTable:IVRCompositor::ClearLastSubmittedFrame"); }

void fill_frame_timing(vr::Compositor_FrameTiming* timing, uint32_t size, uint64_t frame_counter) {
    if (!timing) {
        return;
    }
    uint32_t output_size = size ? std::min<uint32_t>(size, sizeof(*timing)) : sizeof(LegacyCompositorFrameTiming);
    double system_time = fake_frame_vsync_seconds(frame_counter);
    float frame_interval_ms = static_cast<float>(1000.0 / kFakeRefreshHz);

    if (output_size <= sizeof(LegacyCompositorFrameTiming)) {
        LegacyCompositorFrameTiming legacy = {};
        legacy.m_nSize = output_size;
        legacy.m_nFrameIndex = static_cast<uint32_t>(frame_counter);
        legacy.m_nNumFramePresents = 1;
        legacy.m_flSystemTimeInSeconds = system_time;
        legacy.m_flClientFrameIntervalMs = frame_interval_ms;
        legacy.m_flWaitGetPosesCalledMs = -1.0f;
        legacy.m_flNewPosesReadyMs = -0.5f;
        legacy.m_flNewFrameReadyMs = 0.0f;
        fill_pose(&legacy.m_HmdPose, vr::k_unTrackedDeviceIndex_Hmd);
        std::memcpy(timing, &legacy, output_size);
        return;
    }

    std::memset(timing, 0, output_size);
    timing->m_nSize = output_size;
    timing->m_nFrameIndex = static_cast<uint32_t>(frame_counter);
    timing->m_nNumFramePresents = 1;
    timing->m_flSystemTimeInSeconds = system_time;
    timing->m_flClientFrameIntervalMs = frame_interval_ms;
    timing->m_flWaitGetPosesCalledMs = -1.0f;
    timing->m_flNewPosesReadyMs = -0.5f;
    timing->m_flNewFrameReadyMs = 0.0f;
    timing->m_nNumVSyncsReadyForUse = 1;
    timing->m_nNumVSyncsToFirstView = 1;
    fill_pose(&timing->m_HmdPose, vr::k_unTrackedDeviceIndex_Hmd);
}

bool __thiscall fake_get_frame_timing(void*, vr::Compositor_FrameTiming* timing, uint32_t frames_ago) {
    if (timing) {
        uint64_t newest = fake_frame_counter();
        uint64_t frame_counter = newest >= frames_ago ? newest - frames_ago : 0;
        fill_frame_timing(timing, timing->m_nSize, frame_counter);
    }
    return true;
}
bool __stdcall fake_c_get_frame_timing(vr::Compositor_FrameTiming* timing, uint32_t frames_ago) {
    return fake_get_frame_timing(nullptr, timing, frames_ago);
}
uint32_t __thiscall fake_get_frame_timings(void*, vr::Compositor_FrameTiming* timings, uint32_t count) {
    if (!timings || count == 0) {
        return 0;
    }
    uint32_t size = timings[0].m_nSize ? timings[0].m_nSize : sizeof(vr::Compositor_FrameTiming);
    uint64_t newest = fake_frame_counter();
    uint64_t oldest = newest >= (count - 1) ? newest - (count - 1) : 0;
    for (uint32_t index = 0; index < count; ++index) {
        fill_frame_timing(&timings[index], size, oldest + index);
    }
    return count;
}
uint32_t __stdcall fake_c_get_frame_timings(vr::Compositor_FrameTiming* timings, uint32_t count) {
    return fake_get_frame_timings(nullptr, timings, count);
}
float __thiscall fake_get_frame_time_remaining(void*) { return fake_frame_time_remaining_seconds(); }
float __stdcall fake_c_get_frame_time_remaining() { return fake_frame_time_remaining_seconds(); }
void __thiscall fake_get_cumulative_stats(void*, vr::Compositor_CumulativeStats* stats, uint32_t stats_size) {
    if (stats) {
        std::memset(stats, 0, std::min<size_t>(sizeof(*stats), stats_size));
    }
}
void __stdcall fake_c_get_cumulative_stats(vr::Compositor_CumulativeStats* stats, uint32_t stats_size) {
    fake_get_cumulative_stats(nullptr, stats, stats_size);
}
void __thiscall fake_fade_to_color(void*, float, float, float, float, float, bool) {}
void __stdcall fake_c_fade_to_color(float, float, float, float, float, bool) {}
vr::HmdColor_t __thiscall fake_current_fade_color(void*, bool) { return { 0.0f, 0.0f, 0.0f, 0.0f }; }
vr::HmdColor_t* __thiscall fake_cpp_current_fade_color(void*, vr::HmdColor_t* output, bool) {
    log_call("IVRCompositor::GetCurrentFadeColor");
    if (output) {
        *output = { 0.0f, 0.0f, 0.0f, 0.0f };
    }
    return output;
}
vr::HmdColor_t __stdcall fake_c_current_fade_color(bool) { return { 0.0f, 0.0f, 0.0f, 0.0f }; }
void __thiscall fake_fade_grid(void*, float, bool) {}
void __stdcall fake_c_fade_grid(float, bool) {}
float __thiscall fake_grid_alpha(void*) { return 0.0f; }
float __stdcall fake_c_grid_alpha() { return 0.0f; }
vr::EVRCompositorError __thiscall fake_compositor_ok(void*) { return vr::VRCompositorError_None; }
vr::EVRCompositorError __stdcall fake_c_compositor_ok() { return vr::VRCompositorError_None; }
vr::EVRCompositorError __thiscall fake_compositor_request_failed(void*) { return vr::VRCompositorError_RequestFailed; }
vr::EVRCompositorError __stdcall fake_c_compositor_request_failed() { return vr::VRCompositorError_RequestFailed; }
vr::EVRCompositorError __thiscall fake_set_skybox_override(void*, const vr::Texture_t*, uint32_t) {
    log_call("IVRCompositor::SetSkyboxOverride");
    return vr::VRCompositorError_None;
}
vr::EVRCompositorError __stdcall fake_c_set_skybox_override(const vr::Texture_t*, uint32_t) {
    log_call("FnTable:IVRCompositor::SetSkyboxOverride");
    return vr::VRCompositorError_None;
}
uint32_t __thiscall fake_compositor_uint0(void*) { return 0; }
uint32_t __stdcall fake_c_compositor_uint0() { return 0; }
bool __thiscall fake_compositor_is_fullscreen(void*) {
    log_call("IVRCompositor::IsFullscreen -> false");
    return false;
}

bool __stdcall fake_c_compositor_is_fullscreen() {
    log_call("FnTable:IVRCompositor::IsFullscreen -> false");
    return false;
}

uint32_t __thiscall fake_get_current_scene_focus_process(void*) {
    uint32_t pid = GetCurrentProcessId();
    log_call_u32("IVRCompositor::GetCurrentSceneFocusProcess", pid);
    return pid;
}

uint32_t __stdcall fake_c_get_current_scene_focus_process() {
    uint32_t pid = GetCurrentProcessId();
    log_call_u32("FnTable:IVRCompositor::GetCurrentSceneFocusProcess", pid);
    return pid;
}

uint32_t __thiscall fake_get_last_frame_renderer(void*) {
    uint32_t pid = GetCurrentProcessId();
    log_call_u32("IVRCompositor::GetLastFrameRenderer", pid);
    return pid;
}

uint32_t __stdcall fake_c_get_last_frame_renderer() {
    uint32_t pid = GetCurrentProcessId();
    log_call_u32("FnTable:IVRCompositor::GetLastFrameRenderer", pid);
    return pid;
}

bool __thiscall fake_can_render_scene(void*) {
    log_call("IVRCompositor::CanRenderScene -> true");
    return true;
}

bool __stdcall fake_c_can_render_scene() {
    log_call("FnTable:IVRCompositor::CanRenderScene -> true");
    return true;
}

bool __thiscall fake_should_app_render_with_low_resources(void*) {
    log_call("IVRCompositor::ShouldAppRenderWithLowResources -> false");
    return false;
}

bool __stdcall fake_c_should_app_render_with_low_resources() {
    log_call("FnTable:IVRCompositor::ShouldAppRenderWithLowResources -> false");
    return false;
}
void __thiscall fake_show_mirror_window(void*) { log_call("IVRCompositor::ShowMirrorWindow"); }
void __stdcall fake_c_show_mirror_window() { log_call("FnTable:IVRCompositor::ShowMirrorWindow"); }
void __thiscall fake_hide_mirror_window(void*) { log_call("IVRCompositor::HideMirrorWindow"); }
void __stdcall fake_c_hide_mirror_window() { log_call("FnTable:IVRCompositor::HideMirrorWindow"); }
bool __thiscall fake_is_mirror_window_visible(void*) {
    log_call("IVRCompositor::IsMirrorWindowVisible -> false");
    return false;
}
bool __stdcall fake_c_is_mirror_window_visible() {
    log_call("FnTable:IVRCompositor::IsMirrorWindowVisible -> false");
    return false;
}
void __thiscall fake_compositor_dump_images(void*) { log_call("IVRCompositor::CompositorDumpImages"); }
void __stdcall fake_c_compositor_dump_images() { log_call("FnTable:IVRCompositor::CompositorDumpImages"); }
uint32_t __thiscall fake_compositor_string0(void*, char* buffer, uint32_t size) {
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_compositor_string0(char* buffer, uint32_t size) {
    return fake_compositor_string0(nullptr, buffer, size);
}
uint32_t __thiscall fake_compositor_device_string0(void*, VkPhysicalDevice_T*, char* buffer, uint32_t size) {
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_compositor_device_string0(VkPhysicalDevice_T* device, char* buffer, uint32_t size) {
    return fake_compositor_device_string0(nullptr, device, buffer, size);
}
bool __thiscall fake_release_shared_gl_texture(void*, vr::glUInt_t, vr::glSharedTextureHandle_t) { return false; }
bool __stdcall fake_c_release_shared_gl_texture(vr::glUInt_t, vr::glSharedTextureHandle_t) { return false; }
void __thiscall fake_gl_shared_texture_access(void*, vr::glSharedTextureHandle_t) {}
void __stdcall fake_c_gl_shared_texture_access(vr::glSharedTextureHandle_t) {}
void __thiscall fake_set_explicit_timing_mode(void*, vr::EVRCompositorTimingMode) {}
void __stdcall fake_c_set_explicit_timing_mode(vr::EVRCompositorTimingMode) {}

vr::EVRApplicationError __thiscall fake_app_ok(void*) {
    log_call("IVRApplications::<default ok>");
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_app_ok() { return fake_app_ok(nullptr); }
vr::EVRApplicationError __thiscall fake_app_ok_manifest(void*, const char* manifest, bool temporary) {
    log_call_value(temporary ? "IVRApplications::AddApplicationManifest temporary" : "IVRApplications::AddApplicationManifest", manifest);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_app_ok_manifest(const char* manifest, bool temporary) {
    return fake_app_ok_manifest(nullptr, manifest, temporary);
}
vr::EVRApplicationError __thiscall fake_app_ok_string(void*, const char* value) {
    log_call_value("IVRApplications::<ok string>", value);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_app_ok_string(const char* value) { return fake_app_ok_string(nullptr, value); }
vr::EVRApplicationError __thiscall fake_app_ok_two_strings(void*, const char* first, const char* second) {
    log_call_value("IVRApplications::<ok two strings> first", first);
    log_call_value("IVRApplications::<ok two strings> second", second);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_app_ok_two_strings(const char* first, const char* second) {
    return fake_app_ok_two_strings(nullptr, first, second);
}
vr::EVRApplicationError __thiscall fake_app_ok_template(
    void*, const char* template_key, const char* new_key, const vr::AppOverrideKeys_t*, uint32_t key_count
) {
    log_call_value("IVRApplications::LaunchTemplateApplication template", template_key);
    log_call_value("IVRApplications::LaunchTemplateApplication new", new_key);
    log_call_u32("IVRApplications::LaunchTemplateApplication key count", key_count);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_app_ok_template(
    const char* template_key, const char* new_key, const vr::AppOverrideKeys_t* keys, uint32_t key_count
) {
    return fake_app_ok_template(nullptr, template_key, new_key, keys, key_count);
}
vr::EVRApplicationError __thiscall fake_app_ok_string_bool(void*, const char* value, bool) {
    log_call_value("IVRApplications::<ok string bool>", value);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_app_ok_string_bool(const char* value, bool enabled) {
    return fake_app_ok_string_bool(nullptr, value, enabled);
}
vr::EVRApplicationError __thiscall fake_app_ok_internal_process(
    void*, const char* binary_path, const char*, const char*
) {
    log_call_value("IVRApplications::LaunchInternalProcess", binary_path);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_app_ok_internal_process(
    const char* binary_path, const char* arguments, const char* working_directory
) {
    return fake_app_ok_internal_process(nullptr, binary_path, arguments, working_directory);
}
vr::EVRApplicationError __thiscall fake_app_ok_identify(void*, uint32_t pid, const char* key) {
    log_call_u32("IVRApplications::IdentifyApplication pid", pid);
    log_call_value("IVRApplications::IdentifyApplication key", key);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_app_ok_identify(uint32_t pid, const char* key) {
    return fake_app_ok_identify(nullptr, pid, key);
}
bool __thiscall fake_app_true(void*, const char* key) {
    log_call_value("IVRApplications::IsApplicationInstalled", key);
    return true;
}
bool __stdcall fake_c_app_true(const char* key) { return fake_app_true(nullptr, key); }
bool __thiscall fake_app_false(void*, const char* key) {
    log_call_value("IVRApplications::<false string>", key);
    return false;
}
bool __stdcall fake_c_app_false(const char* key) { return fake_app_false(nullptr, key); }
uint32_t __thiscall fake_app_count(void*) {
    log_call("IVRApplications::GetApplicationCount");
    return 1;
}
uint32_t __stdcall fake_c_app_count() { return fake_app_count(nullptr); }
uint32_t __thiscall fake_app_pid(void*, const char* key) {
    log_call_value("IVRApplications::GetApplicationProcessId", key);
    return GetCurrentProcessId();
}
uint32_t __stdcall fake_c_app_pid(const char* key) { return fake_app_pid(nullptr, key); }
uint32_t __thiscall fake_app_current_scene_pid(void*) {
    log_call_u32("IVRApplications::GetCurrentSceneProcessId", GetCurrentProcessId());
    return GetCurrentProcessId();
}
uint32_t __stdcall fake_c_app_current_scene_pid() { return fake_app_current_scene_pid(nullptr); }
const char* __thiscall fake_app_error_name(void*, vr::EVRApplicationError error) {
    log_call_u32("IVRApplications::GetApplicationsErrorNameFromEnum", static_cast<uint32_t>(error));
    return error == vr::VRApplicationError_None ? "VRApplicationError_None" : "VRApplicationError";
}
const char* __stdcall fake_c_app_error_name(vr::EVRApplicationError error) { return fake_app_error_name(nullptr, error); }
vr::EVRApplicationError __thiscall fake_get_app_key(void*, uint32_t, char* buffer, uint32_t size) {
    log_call("IVRApplications::GetApplicationKeyByIndex/ProcessId");
    copy_string("application.generated.fake", buffer, size);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_get_app_key(uint32_t index, char* buffer, uint32_t size) {
    return fake_get_app_key(nullptr, index, buffer, size);
}
bool __thiscall fake_get_app_string_bool(void*, const char*, char* buffer, uint32_t size) {
    log_call("IVRApplications::<string bool>");
    copy_string("", buffer, size);
    return false;
}
bool __stdcall fake_c_get_app_string_bool(const char* key, char* buffer, uint32_t size) {
    return fake_get_app_string_bool(nullptr, key, buffer, size);
}
uint32_t __thiscall fake_get_app_string_count(void*, const char*, char* buffer, uint32_t size) {
    log_call("IVRApplications::<string count>");
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_get_app_string_count(const char* key, char* buffer, uint32_t size) {
    return fake_get_app_string_count(nullptr, key, buffer, size);
}
uint32_t __thiscall fake_get_app_launch_arguments(void*, uint32_t handle, char* buffer, uint32_t size) {
    log_call_u32("IVRApplications::GetApplicationLaunchArguments", handle);
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_get_app_launch_arguments(uint32_t handle, char* buffer, uint32_t size) {
    return fake_get_app_launch_arguments(nullptr, handle, buffer, size);
}
vr::EVRApplicationError __thiscall fake_get_starting_application(void*, char* buffer, uint32_t size) {
    log_call("IVRApplications::GetStartingApplication");
    copy_string("", buffer, size);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_get_starting_application(char* buffer, uint32_t size) {
    return fake_get_starting_application(nullptr, buffer, size);
}
uint32_t __thiscall fake_get_app_property_string(void*, const char*, vr::EVRApplicationProperty, char* buffer, uint32_t size, vr::EVRApplicationError* error) {
    log_call("IVRApplications::GetApplicationPropertyString");
    if (error) {
        *error = vr::VRApplicationError_None;
    }
    copy_string("", buffer, size);
    return 1;
}
uint32_t __stdcall fake_c_get_app_property_string(const char* key, vr::EVRApplicationProperty prop, char* buffer, uint32_t size, vr::EVRApplicationError* error) {
    return fake_get_app_property_string(nullptr, key, prop, buffer, size, error);
}
bool __thiscall fake_get_app_property_bool(void*, const char*, vr::EVRApplicationProperty, vr::EVRApplicationError* error) {
    log_call("IVRApplications::GetApplicationPropertyBool");
    if (error) {
        *error = vr::VRApplicationError_None;
    }
    return false;
}
bool __stdcall fake_c_get_app_property_bool(const char* key, vr::EVRApplicationProperty prop, vr::EVRApplicationError* error) {
    return fake_get_app_property_bool(nullptr, key, prop, error);
}
uint64_t __thiscall fake_get_app_property_uint64(void*, const char*, vr::EVRApplicationProperty, vr::EVRApplicationError* error) {
    log_call("IVRApplications::GetApplicationPropertyUint64");
    if (error) {
        *error = vr::VRApplicationError_None;
    }
    return 0;
}
uint64_t __stdcall fake_c_get_app_property_uint64(const char* key, vr::EVRApplicationProperty prop, vr::EVRApplicationError* error) {
    return fake_get_app_property_uint64(nullptr, key, prop, error);
}
vr::EVRSceneApplicationState __thiscall fake_scene_application_state(void*) {
    log_call("IVRApplications::GetSceneApplicationState");
    return vr::EVRSceneApplicationState_Running;
}
vr::EVRSceneApplicationState __stdcall fake_c_scene_application_state() { return vr::EVRSceneApplicationState_Running; }
vr::EVRApplicationError __thiscall fake_perform_application_prelaunch_check(void*, const char* key) {
    log_call_value("IVRApplications::PerformApplicationPrelaunchCheck", key);
    return vr::VRApplicationError_None;
}
vr::EVRApplicationError __stdcall fake_c_perform_application_prelaunch_check(const char* key) {
    return fake_perform_application_prelaunch_check(nullptr, key);
}
const char* __thiscall fake_scene_application_state_name(void*, vr::EVRSceneApplicationState state) {
    log_call_u32("IVRApplications::GetSceneApplicationStateNameFromEnum", static_cast<uint32_t>(state));
    return state == vr::EVRSceneApplicationState_Running ? "EVRSceneApplicationState_Running" : "EVRSceneApplicationState";
}
const char* __stdcall fake_c_scene_application_state_name(vr::EVRSceneApplicationState state) {
    return fake_scene_application_state_name(nullptr, state);
}

int32_t __thiscall fake_legacy_transition_state(void*) {
    log_call("IVRApplications_004::GetTransitionState");
    return 0;
}

int32_t __stdcall fake_c_legacy_transition_state() { return fake_legacy_transition_state(nullptr); }
const char* __thiscall fake_legacy_transition_state_name(void*, int32_t state) {
    log_call_u32("IVRApplications_004::GetApplicationsTransitionStateNameFromEnum", static_cast<uint32_t>(state));
    return state == 0 ? "VRApplicationTransition_None" : "VRApplicationTransition";
}
const char* __stdcall fake_c_legacy_transition_state_name(int32_t state) {
    return fake_legacy_transition_state_name(nullptr, state);
}

const char* __thiscall fake_settings_error_name(void*, vr::EVRSettingsError error) {
    log_call_u32("IVRSettings::GetSettingsErrorNameFromEnum", static_cast<uint32_t>(error));
    return error == vr::VRSettingsError_None ? "VRSettingsError_None" : "VRSettingsError";
}

const char* __stdcall fake_c_settings_error_name(vr::EVRSettingsError error) {
    return fake_settings_error_name(nullptr, error);
}

void set_settings_error(vr::EVRSettingsError* error) {
    if (error) {
        *error = vr::VRSettingsError_None;
    }
}

bool __thiscall fake_legacy_settings_sync(void*, bool, vr::EVRSettingsError* error) {
    log_call("IVRSettings_001::Sync");
    set_settings_error(error);
    return false;
}

bool __stdcall fake_c_legacy_settings_sync(bool force, vr::EVRSettingsError* error) {
    return fake_legacy_settings_sync(nullptr, force, error);
}

bool __thiscall fake_settings_get_bool(void*, const char*, const char*, vr::EVRSettingsError* error) {
    log_call("IVRSettings::GetBool");
    set_settings_error(error);
    return false;
}

bool __stdcall fake_c_settings_get_bool(const char* section, const char* key, vr::EVRSettingsError* error) {
    return fake_settings_get_bool(nullptr, section, key, error);
}

bool __thiscall fake_legacy_settings_get_bool(void*, const char*, const char*, bool default_value, vr::EVRSettingsError* error) {
    log_call("IVRSettings_001::GetBool");
    set_settings_error(error);
    return default_value;
}

bool __stdcall fake_c_legacy_settings_get_bool(const char* section, const char* key, bool default_value, vr::EVRSettingsError* error) {
    return fake_legacy_settings_get_bool(nullptr, section, key, default_value, error);
}

void __thiscall fake_settings_set_bool(void*, const char*, const char*, bool, vr::EVRSettingsError* error) {
    log_call("IVRSettings::SetBool");
    set_settings_error(error);
}

void __stdcall fake_c_settings_set_bool(const char* section, const char* key, bool value, vr::EVRSettingsError* error) {
    fake_settings_set_bool(nullptr, section, key, value, error);
}

int32_t __thiscall fake_settings_get_int(void*, const char*, const char*, vr::EVRSettingsError* error) {
    log_call("IVRSettings::GetInt32");
    set_settings_error(error);
    return 0;
}

int32_t __stdcall fake_c_settings_get_int(const char* section, const char* key, vr::EVRSettingsError* error) {
    return fake_settings_get_int(nullptr, section, key, error);
}

int32_t __thiscall fake_legacy_settings_get_int(void*, const char*, const char*, int32_t default_value, vr::EVRSettingsError* error) {
    log_call("IVRSettings_001::GetInt32");
    set_settings_error(error);
    return default_value;
}

int32_t __stdcall fake_c_legacy_settings_get_int(const char* section, const char* key, int32_t default_value, vr::EVRSettingsError* error) {
    return fake_legacy_settings_get_int(nullptr, section, key, default_value, error);
}

void __thiscall fake_settings_set_int(void*, const char*, const char*, int32_t, vr::EVRSettingsError* error) {
    log_call("IVRSettings::SetInt32");
    set_settings_error(error);
}

void __stdcall fake_c_settings_set_int(const char* section, const char* key, int32_t value, vr::EVRSettingsError* error) {
    fake_settings_set_int(nullptr, section, key, value, error);
}

float __thiscall fake_settings_get_float(void*, const char*, const char*, vr::EVRSettingsError* error) {
    log_call("IVRSettings::GetFloat");
    set_settings_error(error);
    return 0.0f;
}

float __stdcall fake_c_settings_get_float(const char* section, const char* key, vr::EVRSettingsError* error) {
    return fake_settings_get_float(nullptr, section, key, error);
}

float __thiscall fake_legacy_settings_get_float(void*, const char*, const char*, float default_value, vr::EVRSettingsError* error) {
    log_call("IVRSettings_001::GetFloat");
    set_settings_error(error);
    return default_value;
}

float __stdcall fake_c_legacy_settings_get_float(const char* section, const char* key, float default_value, vr::EVRSettingsError* error) {
    return fake_legacy_settings_get_float(nullptr, section, key, default_value, error);
}

void __thiscall fake_settings_set_float(void*, const char*, const char*, float, vr::EVRSettingsError* error) {
    log_call("IVRSettings::SetFloat");
    set_settings_error(error);
}

void __stdcall fake_c_settings_set_float(const char* section, const char* key, float value, vr::EVRSettingsError* error) {
    fake_settings_set_float(nullptr, section, key, value, error);
}

void __thiscall fake_settings_get_string(void*, const char*, const char*, char* value, uint32_t value_size, vr::EVRSettingsError* error) {
    log_call("IVRSettings::GetString");
    set_settings_error(error);
    copy_string("", value, value_size);
}

void __stdcall fake_c_settings_get_string(const char* section, const char* key, char* value, uint32_t value_size, vr::EVRSettingsError* error) {
    fake_settings_get_string(nullptr, section, key, value, value_size, error);
}

void __thiscall fake_legacy_settings_get_string(
    void*, const char*, const char*, char* value, uint32_t value_size, const char* default_value, vr::EVRSettingsError* error
) {
    log_call("IVRSettings_001::GetString");
    set_settings_error(error);
    copy_string(default_value ? default_value : "", value, value_size);
}

void __stdcall fake_c_legacy_settings_get_string(
    const char* section, const char* key, char* value, uint32_t value_size, const char* default_value, vr::EVRSettingsError* error
) {
    fake_legacy_settings_get_string(nullptr, section, key, value, value_size, default_value, error);
}

void __thiscall fake_settings_set_string(void*, const char*, const char*, const char*, vr::EVRSettingsError* error) {
    log_call("IVRSettings::SetString");
    set_settings_error(error);
}

void __stdcall fake_c_settings_set_string(const char* section, const char* key, const char* value, vr::EVRSettingsError* error) {
    fake_settings_set_string(nullptr, section, key, value, error);
}

void __thiscall fake_settings_remove_section(void*, const char*, vr::EVRSettingsError* error) {
    log_call("IVRSettings::RemoveSection");
    set_settings_error(error);
}

void __stdcall fake_c_settings_remove_section(const char* section, vr::EVRSettingsError* error) {
    fake_settings_remove_section(nullptr, section, error);
}

void __thiscall fake_settings_remove_key(void*, const char*, const char*, vr::EVRSettingsError* error) {
    log_call("IVRSettings::RemoveKeyInSection");
    set_settings_error(error);
}

void __stdcall fake_c_settings_remove_key(const char* section, const char* key, vr::EVRSettingsError* error) {
    fake_settings_remove_key(nullptr, section, key, error);
}

FakeActionKind action_kind_for_name(const char* name) {
    if (!name) {
        return FakeActionKind::Unknown;
    }
    if (_stricmp(name, "/actions/default/in/HeadsetOnHead") == 0) {
        return FakeActionKind::HeadsetOnHead;
    }
    if (_stricmp(name, "/actions/default/in/Trigger") == 0) {
        return FakeActionKind::Trigger;
    }
    if (_stricmp(name, "/actions/default/in/Grip") == 0) {
        return FakeActionKind::Grip;
    }
    if (_stricmp(name, "/actions/default/in/TouchpadClick") == 0) {
        return FakeActionKind::TouchpadClick;
    }
    if (_stricmp(name, "/actions/default/in/AButton") == 0) {
        return FakeActionKind::AButton;
    }
    if (_stricmp(name, "/actions/default/in/BButton") == 0) {
        return FakeActionKind::BButton;
    }
    if (_stricmp(name, "/actions/default/in/Squeeze") == 0) {
        return FakeActionKind::Squeeze;
    }
    if (_stricmp(name, "/actions/default/in/Teleport") == 0) {
        return FakeActionKind::Teleport;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/Pose") == 0) {
        return FakeActionKind::Pose;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_Trigger") == 0) {
        return FakeActionKind::Trigger;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_Thumbstick") == 0) {
        return FakeActionKind::Thumbstick;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_ThumbstickTouch") == 0) {
        return FakeActionKind::ThumbstickTouch;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_GripBinary") == 0) {
        return FakeActionKind::GripBinary;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_GripAnalog") == 0) {
        return FakeActionKind::GripAnalog;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_MenuButton") == 0) {
        return FakeActionKind::MenuButton;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_PadTouch") == 0) {
        return FakeActionKind::PadTouch;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_PadDirectional") == 0) {
        return FakeActionKind::PadDirectional;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_PadClick") == 0) {
        return FakeActionKind::PadClick;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_PrimaryButton") == 0) {
        return FakeActionKind::PrimaryButton;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_PrimaryButtonTouch") == 0) {
        return FakeActionKind::PrimaryButtonTouch;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_SecondaryButton") == 0) {
        return FakeActionKind::SecondaryButton;
    }
    if (_stricmp(name, "/actions/TiltBrush/in/RI_SecondaryButtonTouch") == 0) {
        return FakeActionKind::SecondaryButtonTouch;
    }
    if (_stricmp(name, "/actions/TiltBrush/out/Haptic") == 0) {
        return FakeActionKind::Haptic;
    }
    if (_stricmp(name, "/actions/mixedreality/in/ExternalCamera") == 0) {
        return FakeActionKind::ExternalCamera;
    }
    return FakeActionKind::Unknown;
}

const char* action_kind_name(FakeActionKind kind) {
    switch (kind) {
    case FakeActionKind::Unknown:
        return "unknown";
    case FakeActionKind::HeadsetOnHead:
        return "headset_on_head";
    case FakeActionKind::Trigger:
        return "trigger";
    case FakeActionKind::Grip:
        return "grip";
    case FakeActionKind::TouchpadClick:
        return "touchpad_click";
    case FakeActionKind::AButton:
        return "a_button";
    case FakeActionKind::BButton:
        return "b_button";
    case FakeActionKind::Squeeze:
        return "squeeze";
    case FakeActionKind::Teleport:
        return "teleport";
    case FakeActionKind::Pose:
        return "pose";
    case FakeActionKind::Thumbstick:
        return "thumbstick";
    case FakeActionKind::ThumbstickTouch:
        return "thumbstick_touch";
    case FakeActionKind::GripBinary:
        return "grip_binary";
    case FakeActionKind::GripAnalog:
        return "grip_analog";
    case FakeActionKind::MenuButton:
        return "menu_button";
    case FakeActionKind::PadTouch:
        return "pad_touch";
    case FakeActionKind::PadDirectional:
        return "pad_directional";
    case FakeActionKind::PadClick:
        return "pad_click";
    case FakeActionKind::PrimaryButton:
        return "primary_button";
    case FakeActionKind::PrimaryButtonTouch:
        return "primary_button_touch";
    case FakeActionKind::SecondaryButton:
        return "secondary_button";
    case FakeActionKind::SecondaryButtonTouch:
        return "secondary_button_touch";
    case FakeActionKind::Haptic:
        return "haptic";
    case FakeActionKind::ExternalCamera:
        return "external_camera";
    }
    return "unknown";
}

void remember_action_handle(uint64_t handle, FakeActionKind kind) {
    if (handle == 0 || kind == FakeActionKind::Unknown) {
        return;
    }
    for (LONG index = 0; index < g_fake_action_handle_count; ++index) {
        if (g_fake_action_handles[index].handle == handle) {
            g_fake_action_handles[index].kind = kind;
            return;
        }
    }
    LONG index = InterlockedIncrement(&g_fake_action_handle_count) - 1;
    if (index < 0 || static_cast<size_t>(index) >= (sizeof(g_fake_action_handles) / sizeof(g_fake_action_handles[0]))) {
        InterlockedDecrement(&g_fake_action_handle_count);
        return;
    }
    g_fake_action_handles[index] = { handle, kind };
}

FakeActionKind action_kind_for_handle(uint64_t handle) {
    for (LONG index = 0; index < g_fake_action_handle_count; ++index) {
        if (g_fake_action_handles[index].handle == handle) {
            return g_fake_action_handles[index].kind;
        }
    }
    return FakeActionKind::Unknown;
}

bool is_advance_action(FakeActionKind kind) {
    switch (kind) {
    case FakeActionKind::HeadsetOnHead:
    case FakeActionKind::Trigger:
    case FakeActionKind::Grip:
    case FakeActionKind::TouchpadClick:
    case FakeActionKind::AButton:
    case FakeActionKind::BButton:
    case FakeActionKind::Squeeze:
    case FakeActionKind::Teleport:
        return true;
    default:
        return false;
    }
}

bool fake_action_pressed(FakeActionKind kind) {
    if (kind == FakeActionKind::Unknown) {
        return false;
    }
    if (!g_logged_fake_input_mode) {
        if (env_string_equals("ALVR_FAKE_INPUT", "advance_pulse")) {
            log_line("fake input mode advance_pulse enabled");
        }
        g_logged_fake_input_mode = true;
    }
    if (!env_string_equals("ALVR_FAKE_INPUT", "advance_pulse") || !is_advance_action(kind)) {
        return false;
    }

    uint32_t period_ms = std::max<uint32_t>(1, env_u32("ALVR_FAKE_INPUT_PERIOD_MS", 2000));
    uint32_t down_ms = std::min(env_u32("ALVR_FAKE_INPUT_DOWN_MS", 250), period_ms);
    uint64_t elapsed_ms = static_cast<uint64_t>(fake_session_elapsed_seconds() * 1000.0);
    return (elapsed_ms % period_ms) < down_ms;
}

bool button_pressed(const SharedControllerSnapshot& controller, vr::EVRButtonId button) {
    return (controller.buttons_pressed & vr::ButtonMaskFromId(button)) != 0;
}

bool button_touched(const SharedControllerSnapshot& controller, vr::EVRButtonId button) {
    return (controller.buttons_touched & vr::ButtonMaskFromId(button)) != 0;
}

bool digital_action_state(FakeActionKind kind, const SharedControllerSnapshot* controller) {
    if (!controller) {
        return false;
    }

    switch (kind) {
    case FakeActionKind::Trigger:
        return button_pressed(*controller, vr::k_EButton_SteamVR_Trigger);
    case FakeActionKind::Grip:
    case FakeActionKind::Squeeze:
    case FakeActionKind::GripBinary:
        return button_pressed(*controller, vr::k_EButton_Grip);
    case FakeActionKind::TouchpadClick:
    case FakeActionKind::Teleport:
    case FakeActionKind::PadClick:
        return button_pressed(*controller, vr::k_EButton_SteamVR_Touchpad);
    case FakeActionKind::AButton:
    case FakeActionKind::PrimaryButton:
        return button_pressed(*controller, vr::k_EButton_A);
    case FakeActionKind::BButton:
    case FakeActionKind::MenuButton:
    case FakeActionKind::SecondaryButton:
        return button_pressed(*controller, vr::k_EButton_ApplicationMenu);
    case FakeActionKind::ThumbstickTouch:
    case FakeActionKind::PadTouch:
        return button_touched(*controller, vr::k_EButton_SteamVR_Touchpad);
    case FakeActionKind::PrimaryButtonTouch:
        return button_touched(*controller, vr::k_EButton_A);
    case FakeActionKind::SecondaryButtonTouch:
        return button_touched(*controller, vr::k_EButton_ApplicationMenu);
    default:
        return fake_action_pressed(kind);
    }
}

void analog_action_state(
    FakeActionKind kind,
    const SharedControllerSnapshot* controller,
    float* x,
    float* y,
    float* z
) {
    *x = 0.0f;
    *y = 0.0f;
    *z = 0.0f;
    if (!controller) {
        return;
    }

    switch (kind) {
    case FakeActionKind::Trigger:
        *x = controller->axes[1][0];
        break;
    case FakeActionKind::GripAnalog:
        *x = controller->axes[2][0];
        break;
    case FakeActionKind::Thumbstick:
    case FakeActionKind::PadDirectional:
        *x = controller->axes[0][0];
        *y = controller->axes[0][1];
        break;
    default:
        break;
    }
}

void refresh_fake_input_state() {
    bool synthetic_active = env_string_equals("ALVR_FAKE_INPUT", "advance_pulse");
    bool synthetic_pressed = synthetic_active && fake_action_pressed(FakeActionKind::Trigger);
    bool current_valid[ALVR_NUM_CONTROLLERS] = {};
    SharedControllerSnapshot current[ALVR_NUM_CONTROLLERS] = {};
    for (int controller_index = 0; controller_index < ALVR_NUM_CONTROLLERS; ++controller_index) {
        current_valid[controller_index] = read_shared_controller_state(
            static_cast<vr::TrackedDeviceIndex_t>(controller_index + 1),
            &current[controller_index]
        );
    }

    AcquireSRWLockExclusive(&g_fake_input_state_lock);
    g_fake_input_state.previous_synthetic_active =
        g_fake_input_state.current_synthetic_active;
    g_fake_input_state.previous_synthetic_pressed =
        g_fake_input_state.current_synthetic_pressed;
    g_fake_input_state.current_synthetic_active = synthetic_active;
    g_fake_input_state.current_synthetic_pressed = synthetic_pressed;
    for (int controller_index = 0; controller_index < ALVR_NUM_CONTROLLERS; ++controller_index) {
        g_fake_input_state.previous_valid[controller_index] =
            g_fake_input_state.current_valid[controller_index];
        g_fake_input_state.previous[controller_index] =
            g_fake_input_state.current[controller_index];
        g_fake_input_state.current_valid[controller_index] = current_valid[controller_index];
        g_fake_input_state.current[controller_index] = current[controller_index];
    }
    ReleaseSRWLockExclusive(&g_fake_input_state_lock);

    for (int controller_index = 0; controller_index < ALVR_NUM_CONTROLLERS; ++controller_index) {
        if (current_valid[controller_index]) {
            log_shared_controller_input_activity(
                controller_index, current[controller_index], "input-action"
            );
        }
    }
}

FakeInputSnapshotState fake_input_snapshot_state() {
    AcquireSRWLockShared(&g_fake_input_state_lock);
    FakeInputSnapshotState snapshot = g_fake_input_state;
    ReleaseSRWLockShared(&g_fake_input_state_lock);
    return snapshot;
}

int select_analog_controller(
    FakeActionKind kind,
    const FakeInputSnapshotState& snapshot
) {
    if (!snapshot.current_valid[0]) {
        return snapshot.current_valid[1] ? 1 : 0;
    }
    if (!snapshot.current_valid[1]) {
        return 0;
    }

    float left_x = 0.0f;
    float left_y = 0.0f;
    float left_z = 0.0f;
    float right_x = 0.0f;
    float right_y = 0.0f;
    float right_z = 0.0f;
    analog_action_state(kind, &snapshot.current[0], &left_x, &left_y, &left_z);
    analog_action_state(kind, &snapshot.current[1], &right_x, &right_y, &right_z);
    float left_magnitude = std::fabs(left_x) + std::fabs(left_y) + std::fabs(left_z);
    float right_magnitude = std::fabs(right_x) + std::fabs(right_y) + std::fabs(right_z);
    return right_magnitude > left_magnitude ? 1 : 0;
}

uint64_t stable_input_handle(const char* text, uint64_t salt) {
    uint64_t value = 1469598103934665603ULL ^ salt;
    for (const unsigned char* ch = reinterpret_cast<const unsigned char*>(text); ch && *ch; ++ch) {
        value ^= *ch;
        value *= 1099511628211ULL;
    }
    value &= 0x0000FFFFFFFFFFFFULL;
    return value ? value : salt;
}

vr::EVRInputError __stdcall fake_c_input_set_action_manifest_path(const char* path) {
    char message[640] = {};
    std::snprintf(
        message,
        sizeof(message),
        "IVRInput::SetActionManifestPath path=%s",
        path ? path : "<null>"
    );
    log_line(message);
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_action_set_handle(const char* name, vr::VRActionSetHandle_t* handle) {
    if (handle) {
        *handle = stable_input_handle(name, 0x1000ULL);
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_action_handle(const char* name, vr::VRActionHandle_t* handle) {
    if (handle) {
        *handle = stable_input_handle(name, 0x2000ULL);
        FakeActionKind kind = action_kind_for_name(name);
        remember_action_handle(*handle, kind);
        char message[640] = {};
        std::snprintf(
            message,
            sizeof(message),
            "IVRInput::GetActionHandle name=%s kind=%s handle=%llu",
            name ? name : "<null>",
            action_kind_name(kind),
            static_cast<unsigned long long>(*handle)
        );
        log_line(message);
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_source_handle(const char* path, vr::VRInputValueHandle_t* handle) {
    if (handle) {
        if (path && std::strstr(path, "/user/hand/right")) {
            *handle = 2;
        } else if (path && std::strstr(path, "/user/hand/left")) {
            *handle = 1;
        } else if (path && std::strstr(path, "/user/head")) {
            *handle = 3;
        } else if (path && std::strstr(path, "/user/gamepad")) {
            *handle = 4;
        } else if (path && std::strstr(path, "/user/treadmill")) {
            *handle = 5;
        } else {
            *handle = stable_input_handle(path, 0x3000ULL);
        }
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_update_action_state(vr::VRActiveActionSet_t*, uint32_t, uint32_t) {
    refresh_fake_input_state();
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_digital_action_data(
    vr::VRActionHandle_t action, vr::InputDigitalActionData_t* data, uint32_t size, vr::VRInputValueHandle_t restrict_to_device
) {
    if (!data || size < sizeof(*data)) {
        return vr::VRInputError_InvalidParam;
    }

    log_input_action_query(restrict_to_device);
    FakeInputSnapshotState snapshot = fake_input_snapshot_state();
    FakeActionKind kind = action_kind_for_handle(action);
    bool active = false;
    bool previous_active = false;
    bool state = false;
    bool previous_state = false;
    vr::VRInputValueHandle_t active_origin = 1;

    if (restrict_to_device == 1 || restrict_to_device == 2) {
        int controller_index = restrict_to_device == 2 ? 1 : 0;
        active = snapshot.current_valid[controller_index]
            || snapshot.current_synthetic_active;
        previous_active = snapshot.previous_valid[controller_index]
            || snapshot.previous_synthetic_active;
        state = snapshot.current_valid[controller_index]
            ? digital_action_state(kind, &snapshot.current[controller_index])
            : (snapshot.current_synthetic_pressed && is_advance_action(kind));
        previous_state = snapshot.previous_valid[controller_index]
            ? digital_action_state(kind, &snapshot.previous[controller_index])
            : (snapshot.previous_synthetic_pressed && is_advance_action(kind));
        active_origin = restrict_to_device;
    } else if (restrict_to_device == vr::k_ulInvalidInputValueHandle) {
        bool left_state = snapshot.current_valid[0]
            && digital_action_state(kind, &snapshot.current[0]);
        bool right_state = snapshot.current_valid[1]
            && digital_action_state(kind, &snapshot.current[1]);
        bool previous_left_state = snapshot.previous_valid[0]
            && digital_action_state(kind, &snapshot.previous[0]);
        bool previous_right_state = snapshot.previous_valid[1]
            && digital_action_state(kind, &snapshot.previous[1]);
        active = snapshot.current_valid[0] || snapshot.current_valid[1]
            || snapshot.current_synthetic_active;
        previous_active = snapshot.previous_valid[0] || snapshot.previous_valid[1]
            || snapshot.previous_synthetic_active;
        state = left_state || right_state
            || (snapshot.current_synthetic_pressed && is_advance_action(kind));
        previous_state = previous_left_state || previous_right_state
            || (snapshot.previous_synthetic_pressed && is_advance_action(kind));
        active_origin = right_state && !left_state
            ? 2
            : (snapshot.current_valid[0] ? 1 : 2);
    }

    std::memset(data, 0, sizeof(*data));
    data->bActive = active;
    data->activeOrigin = active_origin;
    data->bState = state;
    data->bChanged = active && previous_active && state != previous_state;
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_analog_action_data(
    vr::VRActionHandle_t action, vr::InputAnalogActionData_t* data, uint32_t size, vr::VRInputValueHandle_t restrict_to_device
) {
    if (!data || size < sizeof(*data)) {
        return vr::VRInputError_InvalidParam;
    }

    log_input_action_query(restrict_to_device);
    FakeInputSnapshotState snapshot = fake_input_snapshot_state();
    FakeActionKind kind = action_kind_for_handle(action);
    int controller_index = 0;
    if (restrict_to_device == 2) {
        controller_index = 1;
    } else if (restrict_to_device == vr::k_ulInvalidInputValueHandle) {
        controller_index = select_analog_controller(kind, snapshot);
    } else if (restrict_to_device != 1) {
        std::memset(data, 0, sizeof(*data));
        return vr::VRInputError_None;
    }

    bool active = snapshot.current_valid[controller_index]
        || snapshot.current_synthetic_active;
    bool previous_active = snapshot.previous_valid[controller_index]
        || snapshot.previous_synthetic_active;
    float x = 0.0f;
    float y = 0.0f;
    float z = 0.0f;
    float previous_x = 0.0f;
    float previous_y = 0.0f;
    float previous_z = 0.0f;
    analog_action_state(
        kind,
        snapshot.current_valid[controller_index] ? &snapshot.current[controller_index] : nullptr,
        &x,
        &y,
        &z
    );
    analog_action_state(
        kind,
        snapshot.previous_valid[controller_index] ? &snapshot.previous[controller_index] : nullptr,
        &previous_x,
        &previous_y,
        &previous_z
    );

    std::memset(data, 0, sizeof(*data));
    data->bActive = active;
    data->activeOrigin = static_cast<vr::VRInputValueHandle_t>(controller_index + 1);
    data->x = x;
    data->y = y;
    data->z = z;
    if (active && previous_active) {
        data->deltaX = x - previous_x;
        data->deltaY = y - previous_y;
        data->deltaZ = z - previous_z;
    }
    return vr::VRInputError_None;
}

vr::EVRInputError fill_input_pose_action_data(
    vr::InputPoseActionData_t* data,
    uint32_t size,
    vr::VRInputValueHandle_t restrict_to_device
) {
    if (!data || size < sizeof(*data)) {
        return vr::VRInputError_InvalidParam;
    }

    log_input_action_query(restrict_to_device);
    FakeInputSnapshotState snapshot = fake_input_snapshot_state();
    std::memset(data, 0, sizeof(*data));

    if (restrict_to_device == 3) {
        data->bActive = true;
        data->activeOrigin = 3;
        fill_pose(&data->pose, vr::k_unTrackedDeviceIndex_Hmd);
        return vr::VRInputError_None;
    }

    int controller_index = restrict_to_device == 2 ? 1 : 0;
    if (restrict_to_device == vr::k_ulInvalidInputValueHandle
        && !snapshot.current_valid[0] && snapshot.current_valid[1]) {
        controller_index = 1;
    } else if (restrict_to_device != vr::k_ulInvalidInputValueHandle
        && restrict_to_device != 1 && restrict_to_device != 2) {
        return vr::VRInputError_None;
    }

    data->bActive = snapshot.current_valid[controller_index]
        || snapshot.current_synthetic_active;
    data->activeOrigin = static_cast<vr::VRInputValueHandle_t>(controller_index + 1);
    fill_pose_with_snapshot(
        &data->pose,
        static_cast<vr::TrackedDeviceIndex_t>(controller_index + 1),
        nullptr,
        snapshot.current_valid[controller_index] ? &snapshot.current[controller_index] : nullptr
    );
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_pose_action_data(
    vr::VRActionHandle_t,
    vr::ETrackingUniverseOrigin,
    float,
    vr::InputPoseActionData_t* data,
    uint32_t size,
    vr::VRInputValueHandle_t restrict_to_device
) {
    return fill_input_pose_action_data(data, size, restrict_to_device);
}

vr::EVRInputError __stdcall fake_c_input_get_pose_action_data_for_next_frame(
    vr::VRActionHandle_t,
    vr::ETrackingUniverseOrigin,
    vr::InputPoseActionData_t* data,
    uint32_t size,
    vr::VRInputValueHandle_t restrict_to_device
) {
    return fill_input_pose_action_data(data, size, restrict_to_device);
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_action_data(
    vr::VRActionHandle_t, vr::InputSkeletalActionData_t* data, uint32_t size
) {
    if (data && size >= sizeof(*data)) {
        std::memset(data, 0, sizeof(*data));
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_bone_count(vr::VRActionHandle_t, uint32_t* count) {
    if (count) {
        *count = 0;
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_bone_hierarchy(vr::VRActionHandle_t, vr::BoneIndex_t*, uint32_t) {
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_bone_name(vr::VRActionHandle_t, vr::BoneIndex_t, char* name, uint32_t size) {
    copy_string("", name, size);
    return vr::VRInputError_InvalidBoneIndex;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_reference_transforms(
    vr::VRActionHandle_t,
    vr::EVRSkeletalTransformSpace,
    vr::EVRSkeletalReferencePose,
    vr::VRBoneTransform_t* transforms,
    uint32_t count
) {
    if (transforms) {
        std::memset(transforms, 0, static_cast<size_t>(count) * sizeof(*transforms));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_tracking_level(
    vr::VRActionHandle_t, vr::EVRSkeletalTrackingLevel* level
) {
    if (level) {
        *level = vr::VRSkeletalTracking_Estimated;
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_bone_data(
    vr::VRActionHandle_t,
    vr::EVRSkeletalTransformSpace,
    vr::EVRSkeletalMotionRange,
    vr::VRBoneTransform_t* transforms,
    uint32_t count
) {
    if (transforms) {
        std::memset(transforms, 0, static_cast<size_t>(count) * sizeof(*transforms));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_summary_data(vr::VRActionHandle_t, vr::VRSkeletalSummaryData_t* summary) {
    if (summary) {
        std::memset(summary, 0, sizeof(*summary));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_summary_data006(
    vr::VRActionHandle_t,
    vr::EVRSummaryType,
    vr::VRSkeletalSummaryData_t* summary
) {
    if (summary) {
        std::memset(summary, 0, sizeof(*summary));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_get_skeletal_bone_data_compressed(
    vr::VRActionHandle_t, vr::EVRSkeletalMotionRange, void*, uint32_t, uint32_t* required_size
) {
    if (required_size) {
        *required_size = 0;
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_decompress_skeletal_bone_data(
    const void*, uint32_t, vr::EVRSkeletalTransformSpace, vr::VRBoneTransform_t* transforms, uint32_t count
) {
    if (transforms) {
        std::memset(transforms, 0, static_cast<size_t>(count) * sizeof(*transforms));
    }
    return vr::VRInputError_NoData;
}

vr::EVRInputError __stdcall fake_c_input_trigger_haptic(
    vr::VRActionHandle_t, float, float, float, float, vr::VRInputValueHandle_t
) {
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_action_origins(
    vr::VRActionSetHandle_t, vr::VRActionHandle_t, vr::VRInputValueHandle_t* origins, uint32_t count
) {
    if (origins && count > 0) {
        origins[0] = 1;
        if (count > 1) {
            origins[1] = 2;
        }
        for (uint32_t index = 2; index < count; ++index) {
            origins[index] = 0;
        }
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_origin_localized_name(
    vr::VRInputValueHandle_t origin, char* name, uint32_t size, int32_t
) {
    copy_string(origin == 2 ? "Right Hand" : "Left Hand", name, size);
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_get_origin_tracked_device_info(
    vr::VRInputValueHandle_t origin, vr::InputOriginInfo_t* info, uint32_t size
) {
    if (info && size >= sizeof(*info)) {
        std::memset(info, 0, sizeof(*info));
        info->devicePath = origin;
        info->trackedDeviceIndex = origin == 2 ? 2 : 1;
    }
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_show_action_origins(vr::VRActionSetHandle_t, vr::VRActionHandle_t) {
    return vr::VRInputError_None;
}

vr::EVRInputError __stdcall fake_c_input_show_bindings_for_action_set(
    vr::VRActiveActionSet_t*, uint32_t, uint32_t, vr::VRInputValueHandle_t
) {
    return vr::VRInputError_None;
}

bool __stdcall fake_c_input_is_using_legacy_input() { return false; }

vr::EVRInputError __thiscall fake_input_set_action_manifest_path(void*, const char* path) {
    return fake_c_input_set_action_manifest_path(path);
}

vr::EVRInputError __thiscall fake_input_get_action_set_handle(
    void*, const char* name, vr::VRActionSetHandle_t* handle
) {
    return fake_c_input_get_action_set_handle(name, handle);
}

vr::EVRInputError __thiscall fake_input_get_action_handle(
    void*, const char* name, vr::VRActionHandle_t* handle
) {
    return fake_c_input_get_action_handle(name, handle);
}

vr::EVRInputError __thiscall fake_input_get_source_handle(
    void*, const char* path, vr::VRInputValueHandle_t* handle
) {
    return fake_c_input_get_source_handle(path, handle);
}

vr::EVRInputError __thiscall fake_input_update_action_state(
    void*, vr::VRActiveActionSet_t* sets, uint32_t set_size, uint32_t set_count
) {
    return fake_c_input_update_action_state(sets, set_size, set_count);
}

vr::EVRInputError __thiscall fake_input_get_digital_action_data(
    void*,
    vr::VRActionHandle_t action,
    vr::InputDigitalActionData_t* data,
    uint32_t size,
    vr::VRInputValueHandle_t restrict_to_device
) {
    return fake_c_input_get_digital_action_data(action, data, size, restrict_to_device);
}

vr::EVRInputError __thiscall fake_input_get_analog_action_data(
    void*,
    vr::VRActionHandle_t action,
    vr::InputAnalogActionData_t* data,
    uint32_t size,
    vr::VRInputValueHandle_t restrict_to_device
) {
    return fake_c_input_get_analog_action_data(action, data, size, restrict_to_device);
}

vr::EVRInputError __thiscall fake_input_get_pose_action_data(
    void*,
    vr::VRActionHandle_t action,
    vr::ETrackingUniverseOrigin origin,
    float predicted_seconds,
    vr::InputPoseActionData_t* data,
    uint32_t size,
    vr::VRInputValueHandle_t restrict_to_device
) {
    return fake_c_input_get_pose_action_data(
        action, origin, predicted_seconds, data, size, restrict_to_device
    );
}

vr::EVRInputError __thiscall fake_input_get_pose_action_data_for_next_frame(
    void*,
    vr::VRActionHandle_t action,
    vr::ETrackingUniverseOrigin origin,
    vr::InputPoseActionData_t* data,
    uint32_t size,
    vr::VRInputValueHandle_t restrict_to_device
) {
    return fake_c_input_get_pose_action_data_for_next_frame(
        action, origin, data, size, restrict_to_device
    );
}

vr::EVRInputError __thiscall fake_input_get_skeletal_action_data(
    void*, vr::VRActionHandle_t action, vr::InputSkeletalActionData_t* data, uint32_t size
) {
    return fake_c_input_get_skeletal_action_data(action, data, size);
}

vr::EVRInputError __thiscall fake_input_get_bone_count(
    void*, vr::VRActionHandle_t action, uint32_t* count
) {
    return fake_c_input_get_bone_count(action, count);
}

vr::EVRInputError __thiscall fake_input_get_bone_hierarchy(
    void*, vr::VRActionHandle_t action, vr::BoneIndex_t* parents, uint32_t count
) {
    return fake_c_input_get_bone_hierarchy(action, parents, count);
}

vr::EVRInputError __thiscall fake_input_get_bone_name(
    void*, vr::VRActionHandle_t action, vr::BoneIndex_t bone, char* name, uint32_t size
) {
    return fake_c_input_get_bone_name(action, bone, name, size);
}

vr::EVRInputError __thiscall fake_input_get_skeletal_reference_transforms(
    void*,
    vr::VRActionHandle_t action,
    vr::EVRSkeletalTransformSpace transform_space,
    vr::EVRSkeletalReferencePose reference_pose,
    vr::VRBoneTransform_t* transforms,
    uint32_t count
) {
    return fake_c_input_get_skeletal_reference_transforms(
        action, transform_space, reference_pose, transforms, count
    );
}

vr::EVRInputError __thiscall fake_input_get_skeletal_tracking_level(
    void*, vr::VRActionHandle_t action, vr::EVRSkeletalTrackingLevel* level
) {
    return fake_c_input_get_skeletal_tracking_level(action, level);
}

vr::EVRInputError __thiscall fake_input_get_skeletal_bone_data(
    void*,
    vr::VRActionHandle_t action,
    vr::EVRSkeletalTransformSpace transform_space,
    vr::EVRSkeletalMotionRange motion_range,
    vr::VRBoneTransform_t* transforms,
    uint32_t count
) {
    return fake_c_input_get_skeletal_bone_data(
        action, transform_space, motion_range, transforms, count
    );
}

vr::EVRInputError __thiscall fake_input_get_skeletal_summary_data006(
    void*,
    vr::VRActionHandle_t action,
    vr::EVRSummaryType summary_type,
    vr::VRSkeletalSummaryData_t* summary
) {
    return fake_c_input_get_skeletal_summary_data006(action, summary_type, summary);
}

vr::EVRInputError __thiscall fake_input_get_skeletal_bone_data_compressed(
    void*,
    vr::VRActionHandle_t action,
    vr::EVRSkeletalMotionRange motion_range,
    void* compressed_data,
    uint32_t compressed_size,
    uint32_t* required_size
) {
    return fake_c_input_get_skeletal_bone_data_compressed(
        action, motion_range, compressed_data, compressed_size, required_size
    );
}

vr::EVRInputError __thiscall fake_input_decompress_skeletal_bone_data(
    void*,
    const void* compressed_buffer,
    uint32_t compressed_size,
    vr::EVRSkeletalTransformSpace transform_space,
    vr::VRBoneTransform_t* transforms,
    uint32_t count
) {
    return fake_c_input_decompress_skeletal_bone_data(
        compressed_buffer, compressed_size, transform_space, transforms, count
    );
}

vr::EVRInputError __thiscall fake_input_trigger_haptic(
    void*,
    vr::VRActionHandle_t action,
    float start_seconds,
    float duration_seconds,
    float frequency,
    float amplitude,
    vr::VRInputValueHandle_t restrict_to_device
) {
    return fake_c_input_trigger_haptic(
        action,
        start_seconds,
        duration_seconds,
        frequency,
        amplitude,
        restrict_to_device
    );
}

vr::EVRInputError __thiscall fake_input_get_action_origins(
    void*,
    vr::VRActionSetHandle_t action_set,
    vr::VRActionHandle_t action,
    vr::VRInputValueHandle_t* origins,
    uint32_t count
) {
    return fake_c_input_get_action_origins(action_set, action, origins, count);
}

vr::EVRInputError __thiscall fake_input_get_origin_localized_name(
    void*, vr::VRInputValueHandle_t origin, char* name, uint32_t size, int32_t sections
) {
    return fake_c_input_get_origin_localized_name(origin, name, size, sections);
}

vr::EVRInputError __thiscall fake_input_get_origin_tracked_device_info(
    void*, vr::VRInputValueHandle_t origin, vr::InputOriginInfo_t* info, uint32_t size
) {
    return fake_c_input_get_origin_tracked_device_info(origin, info, size);
}

vr::EVRInputError __thiscall fake_input_show_action_origins(
    void*, vr::VRActionSetHandle_t action_set, vr::VRActionHandle_t action
) {
    return fake_c_input_show_action_origins(action_set, action);
}

vr::EVRInputError __thiscall fake_input_show_bindings_for_action_set(
    void*,
    vr::VRActiveActionSet_t* sets,
    uint32_t set_size,
    uint32_t set_count,
    vr::VRInputValueHandle_t origin
) {
    return fake_c_input_show_bindings_for_action_set(sets, set_size, set_count, origin);
}

bool __thiscall fake_input_is_using_legacy_input(void*) {
    return fake_c_input_is_using_legacy_input();
}

vr::EVRRenderModelError __stdcall fake_c_render_models_load_model(
    const char* model_name,
    vr::RenderModel_t** model
) {
    log_call("IVRRenderModels::LoadRenderModel_Async");
    if (model) {
        *model = nullptr;
    }
    if (!model_name || !model) {
        return vr::VRRenderModelError_InvalidArg;
    }
    if (!is_fake_model(model_name)) {
        return vr::VRRenderModelError_InvalidModel;
    }
    *model = &g_render_model;
    return vr::VRRenderModelError_None;
}

void __stdcall fake_c_render_models_free_model(vr::RenderModel_t*) {}

vr::EVRRenderModelError __stdcall fake_c_render_models_load_texture(
    vr::TextureID_t texture_id,
    vr::RenderModel_TextureMap_t** texture
) {
    log_call("IVRRenderModels::LoadTexture_Async");
    if (texture) {
        *texture = nullptr;
    }
    if (!texture) {
        return vr::VRRenderModelError_InvalidArg;
    }
    if (texture_id != 1) {
        return vr::VRRenderModelError_InvalidTexture;
    }
    *texture = &g_render_texture;
    return vr::VRRenderModelError_None;
}

void __stdcall fake_c_render_models_free_texture(vr::RenderModel_TextureMap_t*) {}

vr::EVRRenderModelError __stdcall fake_c_render_models_load_texture_d3d11(
    vr::TextureID_t texture_id,
    void*,
    void** texture
) {
    log_call("IVRRenderModels::LoadTextureD3D11_Async");
    if (texture) {
        *texture = nullptr;
    }
    return texture_id == vr::INVALID_TEXTURE_ID
        ? vr::VRRenderModelError_InvalidTexture
        : vr::VRRenderModelError_NotSupported;
}

vr::EVRRenderModelError __stdcall fake_c_render_models_load_into_texture_d3d11(
    vr::TextureID_t texture_id,
    void*
) {
    log_call("IVRRenderModels::LoadIntoTextureD3D11_Async");
    return texture_id == vr::INVALID_TEXTURE_ID
        ? vr::VRRenderModelError_InvalidTexture
        : vr::VRRenderModelError_NotSupported;
}

void __stdcall fake_c_render_models_free_texture_d3d11(void*) {}

uint32_t __stdcall fake_c_render_models_get_model_name(uint32_t index, char* name, uint32_t name_size) {
    const char* model_name = render_model_name(index);
    if (!model_name) {
        return 0;
    }
    copy_string(model_name, name, name_size);
    return static_cast<uint32_t>(std::strlen(model_name) + 1);
}

uint32_t __stdcall fake_c_render_models_get_model_count() { return 3; }

uint32_t __stdcall fake_c_render_models_get_component_count(const char* model_name) {
    return is_fake_controller_model(model_name) ? 5 : 0;
}

uint32_t __stdcall fake_c_render_models_get_component_name(
    const char* model_name, uint32_t index, char* component_name, uint32_t component_name_size
) {
    if (!is_fake_controller_model(model_name)) {
        return 0;
    }
    const char* name = render_model_component_name(index);
    if (!name) {
        return 0;
    }
    copy_string(name, component_name, component_name_size);
    return static_cast<uint32_t>(std::strlen(name) + 1);
}

uint64_t __stdcall fake_c_render_models_get_component_button_mask(const char*, const char*) {
    return 0;
}

uint32_t __stdcall fake_c_render_models_get_component_render_model_name(
    const char* model_name, const char* component_name, char* component_model_name, uint32_t component_model_name_size
) {
    if (!is_fake_controller_model(model_name) || !is_render_model_component(component_name)) {
        return 0;
    }
    copy_string(model_name, component_model_name, component_model_name_size);
    return static_cast<uint32_t>(std::strlen(model_name) + 1);
}

void fill_component_state(vr::RenderModel_ComponentState_t* state) {
    if (!state) {
        return;
    }
    std::memset(state, 0, sizeof(*state));
    state->mTrackingToComponentRenderModel = identity34();
    state->mTrackingToComponentLocal = identity34();
    state->uProperties = vr::VRComponentProperty_IsStatic | vr::VRComponentProperty_IsVisible;
}

bool __stdcall fake_c_render_models_get_component_state_for_device_path(
    const char* model_name,
    const char* component_name,
    vr::VRInputValueHandle_t,
    vr::RenderModel_ControllerMode_State_t*,
    vr::RenderModel_ComponentState_t* component_state
) {
    fill_component_state(component_state);
    return is_fake_controller_model(model_name) && is_render_model_component(component_name);
}

bool __stdcall fake_c_render_models_get_component_state(
    const char* model_name,
    const char* component_name,
    vr::VRControllerState_t*,
    vr::RenderModel_ControllerMode_State_t*,
    vr::RenderModel_ComponentState_t* component_state
) {
    fill_component_state(component_state);
    return is_fake_controller_model(model_name) && is_render_model_component(component_name);
}

bool __stdcall fake_c_render_models_has_component(const char* model_name, const char* component_name) {
    return is_fake_controller_model(model_name) && is_render_model_component(component_name);
}

uint32_t __stdcall fake_c_render_models_get_thumbnail_url(
    const char*, char* url, uint32_t url_size, vr::EVRRenderModelError* error
) {
    if (error) {
        *error = vr::VRRenderModelError_None;
    }
    copy_string("", url, url_size);
    return 1;
}

uint32_t __stdcall fake_c_render_models_get_original_path(
    const char* model_name, char* path, uint32_t path_size, vr::EVRRenderModelError* error
) {
    if (error) {
        *error = model_name ? vr::VRRenderModelError_None : vr::VRRenderModelError_InvalidArg;
    }
    if (!model_name) {
        return 0;
    }
    copy_string(model_name, path, path_size);
    return static_cast<uint32_t>(std::strlen(model_name) + 1);
}

const char* __stdcall fake_c_render_models_get_error_name(vr::EVRRenderModelError error) {
    return render_model_error_name(error);
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_request(
    vr::ScreenshotHandle_t* handle, vr::EVRScreenshotType, const char*, const char*
) {
    if (handle) {
        *handle = 1;
    }
    return vr::VRScreenshotError_RequestFailed;
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_hook(vr::EVRScreenshotType*, int) {
    return vr::VRScreenshotError_None;
}

vr::EVRScreenshotType __stdcall fake_c_screenshots_get_type(
    vr::ScreenshotHandle_t, vr::EVRScreenshotError* error
) {
    if (error) {
        *error = vr::VRScreenshotError_NotFound;
    }
    return vr::VRScreenshotType_None;
}

uint32_t __stdcall fake_c_screenshots_get_filename(
    vr::ScreenshotHandle_t,
    vr::EVRScreenshotPropertyFilenames,
    char* filename,
    uint32_t filename_size,
    vr::EVRScreenshotError* error
) {
    if (error) {
        *error = vr::VRScreenshotError_NotFound;
    }
    copy_string("", filename, filename_size);
    return 1;
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_update_progress(vr::ScreenshotHandle_t, float) {
    return vr::VRScreenshotError_NotFound;
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_take_stereo(
    vr::ScreenshotHandle_t* handle, const char*, const char*
) {
    if (handle) {
        *handle = 1;
    }
    return vr::VRScreenshotError_RequestFailed;
}

vr::EVRScreenshotError __stdcall fake_c_screenshots_submit(
    vr::ScreenshotHandle_t, vr::EVRScreenshotType, const char*, const char*
) {
    return vr::VRScreenshotError_NotFound;
}

void* g_system_vtable[kSystemSlots] = {};
void* g_system_fntable[kSystemSlots] = {};
void* g_system011_vtable[kLegacySystem011Slots] = {};
void* g_system011_fntable[kLegacySystem011Slots] = {};
void* g_system019_vtable[kLegacySystem019Slots] = {};
void* g_system019_fntable[kLegacySystem019Slots] = {};
void* g_compositor_vtable[kCompositorSlots] = {};
void* g_compositor_fntable[kCompositorSlots] = {};
void* g_compositor013_vtable[kLegacyCompositor013Slots] = {};
void* g_compositor013_fntable[kLegacyCompositor013Slots] = {};
void* g_compositor014_vtable[kLegacyCompositor014Slots] = {};
void* g_compositor014_fntable[kLegacyCompositor014Slots] = {};
void* g_compositor016_vtable[kLegacyCompositor016Slots] = {};
void* g_compositor016_fntable[kLegacyCompositor016Slots] = {};
void* g_chaperone_vtable[kChaperoneSlots] = {};
void* g_chaperone_fntable[kChaperoneSlots] = {};
void* g_chaperone_setup_vtable[kChaperoneSetupSlots] = {};
void* g_chaperone_setup_fntable[kChaperoneSetupSlots] = {};
void* g_chaperone_setup005_vtable[kChaperoneSetupSlots] = {};
void* g_chaperone_setup005_fntable[kChaperoneSetupSlots] = {};
void* g_overlay_vtable[kOverlaySlots] = {};
void* g_overlay_fntable[kOverlaySlots] = {};
void* g_overlay013_vtable[kLegacyOverlay013Slots] = {};
void* g_overlay013_fntable[kLegacyOverlay013Slots] = {};
void* g_render_models_fntable[kRenderModelsSlots] = {};
void* g_screenshots_fntable[kScreenshotsSlots] = {};
void* g_applications_vtable[kApplicationsSlots] = {};
void* g_applications_fntable[kApplicationsSlots] = {};
void* g_applications004_vtable[kLegacyApplications004Slots] = {};
void* g_applications004_fntable[kLegacyApplications004Slots] = {};
void* g_settings_vtable[kSettingsSlots] = {};
void* g_settings_fntable[kSettingsSlots] = {};
void* g_settings001_vtable[kLegacySettings001Slots] = {};
void* g_settings001_fntable[kLegacySettings001Slots] = {};
void* g_input005_fntable[kLegacyInput005Slots] = {};
void* g_input006_vtable[kLegacyInput006Slots] = {};
void* g_input006_fntable[kLegacyInput006Slots] = {};
bool g_tables_initialized = false;

struct FakeSystemObject {
    void** vtable;
};

struct FakeCompositorObject {
    void** vtable;
};

struct FakeChaperoneObject {
    void** vtable;
};

struct FakeChaperoneSetupObject {
    void** vtable;
};

struct FakeOverlayObject {
    void** vtable;
};

struct FakeApplicationsObject {
    void** vtable;
};

struct FakeSettingsObject {
    void** vtable;
};

struct FakeInputObject {
    void** vtable;
};

FakeSystemObject g_system = { g_system_vtable };
FakeSystemObject g_system011 = { g_system011_vtable };
FakeSystemObject g_system019 = { g_system019_vtable };
FakeCompositorObject g_compositor = { g_compositor_vtable };
FakeCompositorObject g_compositor013 = { g_compositor013_vtable };
FakeCompositorObject g_compositor014 = { g_compositor014_vtable };
FakeCompositorObject g_compositor016 = { g_compositor016_vtable };
FakeChaperoneObject g_chaperone = { g_chaperone_vtable };
FakeChaperoneSetupObject g_chaperone_setup = { g_chaperone_setup_vtable };
FakeChaperoneSetupObject g_chaperone_setup005 = { g_chaperone_setup005_vtable };
FakeOverlayObject g_overlay = { g_overlay_vtable };
FakeOverlayObject g_overlay013 = { g_overlay013_vtable };
FakeApplicationsObject g_applications = { g_applications_vtable };
FakeApplicationsObject g_applications004 = { g_applications004_vtable };
FakeSettingsObject g_settings = { g_settings_vtable };
FakeSettingsObject g_settings001 = { g_settings001_vtable };
FakeInputObject g_input006 = { g_input006_vtable };

void ensure_tables_initialized() {
    if (g_tables_initialized) {
        return;
    }

    for (size_t index = 0; index < kSystemSlots; ++index) {
        g_system_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_system_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_system_vtable[0] = reinterpret_cast<void*>(&fake_get_recommended_render_target_size);
    g_system_vtable[1] = reinterpret_cast<void*>(&fake_get_projection_matrix);
    g_system_vtable[2] = reinterpret_cast<void*>(&fake_get_projection_raw);
    g_system_vtable[3] = reinterpret_cast<void*>(&fake_compute_distortion);
    g_system_vtable[4] = reinterpret_cast<void*>(&fake_get_eye_to_head_transform);
    g_system_vtable[5] = reinterpret_cast<void*>(&fake_get_time_since_last_vsync);
    g_system_vtable[6] = reinterpret_cast<void*>(&fake_get_d3d9_adapter_index);
    g_system_vtable[7] = reinterpret_cast<void*>(&fake_get_dxgi_output_info);
    g_system_vtable[8] = reinterpret_cast<void*>(&fake_get_output_device);
    g_system_vtable[9] = reinterpret_cast<void*>(&fake_is_display_on_desktop);
    g_system_vtable[10] = reinterpret_cast<void*>(&fake_set_display_visibility);
    g_system_vtable[11] = reinterpret_cast<void*>(&fake_get_tracking_pose);
    g_system_vtable[12] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system_vtable[13] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system_vtable[14] = reinterpret_cast<void*>(&fake_get_sorted_tracked_device_indices);
    g_system_vtable[15] = reinterpret_cast<void*>(&fake_get_activity_level);
    g_system_vtable[16] = reinterpret_cast<void*>(&fake_apply_transform);
    g_system_vtable[17] = reinterpret_cast<void*>(&fake_invalid_device_index);
    g_system_vtable[18] = reinterpret_cast<void*>(&fake_invalid_controller_role);
    g_system_vtable[19] = reinterpret_cast<void*>(&fake_get_tracked_device_class);
    g_system_vtable[20] = reinterpret_cast<void*>(&fake_is_tracked_device_connected);
    g_system_vtable[21] = reinterpret_cast<void*>(&fake_get_bool_property);
    g_system_vtable[22] = reinterpret_cast<void*>(&fake_get_float_property);
    g_system_vtable[23] = reinterpret_cast<void*>(&fake_get_int_property);
    g_system_vtable[24] = reinterpret_cast<void*>(&fake_get_uint64_property);
    g_system_vtable[25] = reinterpret_cast<void*>(&fake_cpp_get_matrix34_property);
    g_system_vtable[26] = reinterpret_cast<void*>(&fake_get_array_property);
    g_system_vtable[27] = reinterpret_cast<void*>(&fake_get_string_property);
    g_system_vtable[28] = reinterpret_cast<void*>(&fake_get_prop_error_name);
    g_system_vtable[29] = reinterpret_cast<void*>(&fake_poll_next_event);
    g_system_vtable[30] = reinterpret_cast<void*>(&fake_poll_next_event_with_pose);
    g_system_vtable[31] = reinterpret_cast<void*>(&fake_get_event_type_name);
    g_system_vtable[32] = reinterpret_cast<void*>(&fake_cpp_get_hidden_area_mesh);
    g_system_vtable[33] = reinterpret_cast<void*>(&fake_get_controller_state);
    g_system_vtable[34] = reinterpret_cast<void*>(&fake_get_controller_state_with_pose);
    g_system_vtable[36] = reinterpret_cast<void*>(&fake_button_name);
    g_system_vtable[37] = reinterpret_cast<void*>(&fake_axis_name);
    g_system_vtable[38] = reinterpret_cast<void*>(&fake_is_input_available);
    g_system_vtable[39] = reinterpret_cast<void*>(&fake_is_steamvr_drawing_controllers);
    g_system_vtable[40] = reinterpret_cast<void*>(&fake_should_application_pause);
    g_system_vtable[41] = reinterpret_cast<void*>(&fake_should_application_reduce_rendering_work);
    g_system_vtable[42] = reinterpret_cast<void*>(&fake_firmware_update);
    g_system_vtable[44] = reinterpret_cast<void*>(&fake_get_app_container_file_paths);
    g_system_vtable[45] = reinterpret_cast<void*>(&fake_get_runtime_version);

    g_system_fntable[0] = reinterpret_cast<void*>(&fake_c_get_recommended_render_target_size);
    g_system_fntable[1] = reinterpret_cast<void*>(&fake_c_get_projection_matrix);
    g_system_fntable[2] = reinterpret_cast<void*>(&fake_c_get_projection_raw);
    g_system_fntable[3] = reinterpret_cast<void*>(&fake_c_compute_distortion);
    g_system_fntable[4] = reinterpret_cast<void*>(&fake_c_get_eye_to_head_transform);
    g_system_fntable[5] = reinterpret_cast<void*>(&fake_c_get_time_since_last_vsync);
    g_system_fntable[6] = reinterpret_cast<void*>(&fake_c_get_d3d9_adapter_index);
    g_system_fntable[7] = reinterpret_cast<void*>(&fake_c_get_dxgi_output_info);
    g_system_fntable[8] = reinterpret_cast<void*>(&fake_c_get_output_device);
    g_system_fntable[9] = reinterpret_cast<void*>(&fake_c_is_display_on_desktop);
    g_system_fntable[10] = reinterpret_cast<void*>(&fake_c_set_display_visibility);
    g_system_fntable[11] = reinterpret_cast<void*>(&fake_c_get_tracking_pose);
    g_system_fntable[12] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system_fntable[13] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system_fntable[14] = reinterpret_cast<void*>(&fake_c_get_sorted_tracked_device_indices);
    g_system_fntable[15] = reinterpret_cast<void*>(&fake_c_get_activity_level);
    g_system_fntable[16] = reinterpret_cast<void*>(&fake_c_apply_transform);
    g_system_fntable[17] = reinterpret_cast<void*>(&fake_c_invalid_device_index);
    g_system_fntable[18] = reinterpret_cast<void*>(&fake_c_invalid_controller_role);
    g_system_fntable[19] = reinterpret_cast<void*>(&fake_c_get_tracked_device_class);
    g_system_fntable[20] = reinterpret_cast<void*>(&fake_c_is_tracked_device_connected);
    g_system_fntable[21] = reinterpret_cast<void*>(&fake_c_get_bool_property);
    g_system_fntable[22] = reinterpret_cast<void*>(&fake_c_get_float_property);
    g_system_fntable[23] = reinterpret_cast<void*>(&fake_c_get_int_property);
    g_system_fntable[24] = reinterpret_cast<void*>(&fake_c_get_uint64_property);
    g_system_fntable[25] = reinterpret_cast<void*>(&fake_c_get_matrix34_property);
    g_system_fntable[26] = reinterpret_cast<void*>(&fake_c_get_array_property);
    g_system_fntable[27] = reinterpret_cast<void*>(&fake_c_get_string_property);
    g_system_fntable[28] = reinterpret_cast<void*>(&fake_c_get_prop_error_name);
    g_system_fntable[29] = reinterpret_cast<void*>(&fake_c_poll_next_event);
    g_system_fntable[30] = reinterpret_cast<void*>(&fake_c_poll_next_event_with_pose);
    g_system_fntable[31] = reinterpret_cast<void*>(&fake_c_get_event_type_name);
    g_system_fntable[32] = reinterpret_cast<void*>(&fake_c_get_hidden_area_mesh);
    g_system_fntable[33] = reinterpret_cast<void*>(&fake_c_get_controller_state);
    g_system_fntable[34] = reinterpret_cast<void*>(&fake_c_get_controller_state_with_pose);
    g_system_fntable[36] = reinterpret_cast<void*>(&fake_c_button_name);
    g_system_fntable[37] = reinterpret_cast<void*>(&fake_c_axis_name);
    g_system_fntable[38] = reinterpret_cast<void*>(&fake_c_is_input_available);
    g_system_fntable[39] = reinterpret_cast<void*>(&fake_c_is_steamvr_drawing_controllers);
    g_system_fntable[40] = reinterpret_cast<void*>(&fake_c_should_application_pause);
    g_system_fntable[41] = reinterpret_cast<void*>(&fake_c_should_application_reduce_rendering_work);
    g_system_fntable[42] = reinterpret_cast<void*>(&fake_c_firmware_update);
    g_system_fntable[44] = reinterpret_cast<void*>(&fake_c_get_app_container_file_paths);
    g_system_fntable[45] = reinterpret_cast<void*>(&fake_c_get_runtime_version);

    for (size_t index = 0; index < kLegacySystem011Slots; ++index) {
        g_system011_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_system011_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_system011_vtable[0] = reinterpret_cast<void*>(&fake_get_recommended_render_target_size);
    g_system011_vtable[1] = reinterpret_cast<void*>(&fake_cpp_legacy_get_projection_matrix);
    g_system011_vtable[2] = reinterpret_cast<void*>(&fake_get_projection_raw);
    g_system011_vtable[3] = reinterpret_cast<void*>(&fake_cpp_legacy_compute_distortion);
    g_system011_vtable[4] = reinterpret_cast<void*>(&fake_cpp_get_eye_to_head_transform);
    g_system011_vtable[5] = reinterpret_cast<void*>(&fake_get_time_since_last_vsync);
    g_system011_vtable[6] = reinterpret_cast<void*>(&fake_get_d3d9_adapter_index);
    g_system011_vtable[7] = reinterpret_cast<void*>(&fake_get_dxgi_output_info);
    g_system011_vtable[8] = reinterpret_cast<void*>(&fake_is_display_on_desktop);
    g_system011_vtable[9] = reinterpret_cast<void*>(&fake_set_display_visibility);
    g_system011_vtable[10] = reinterpret_cast<void*>(&fake_get_tracking_pose);
    g_system011_vtable[11] = reinterpret_cast<void*>(&fake_reset_seated_zero_pose);
    g_system011_vtable[12] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system011_vtable[13] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system011_vtable[14] = reinterpret_cast<void*>(&fake_get_sorted_tracked_device_indices);
    g_system011_vtable[15] = reinterpret_cast<void*>(&fake_get_activity_level);
    g_system011_vtable[16] = reinterpret_cast<void*>(&fake_apply_transform);
    g_system011_vtable[17] = reinterpret_cast<void*>(&fake_invalid_device_index);
    g_system011_vtable[18] = reinterpret_cast<void*>(&fake_invalid_controller_role);
    g_system011_vtable[19] = reinterpret_cast<void*>(&fake_get_tracked_device_class);
    g_system011_vtable[20] = reinterpret_cast<void*>(&fake_is_tracked_device_connected);
    g_system011_vtable[21] = reinterpret_cast<void*>(&fake_get_bool_property);
    g_system011_vtable[22] = reinterpret_cast<void*>(&fake_get_float_property);
    g_system011_vtable[23] = reinterpret_cast<void*>(&fake_get_int_property);
    g_system011_vtable[24] = reinterpret_cast<void*>(&fake_get_uint64_property);
    g_system011_vtable[25] = reinterpret_cast<void*>(&fake_cpp_get_matrix34_property);
    g_system011_vtable[26] = reinterpret_cast<void*>(&fake_get_string_property);
    g_system011_vtable[27] = reinterpret_cast<void*>(&fake_get_prop_error_name);
    g_system011_vtable[28] = reinterpret_cast<void*>(&fake_poll_next_event);
    g_system011_vtable[29] = reinterpret_cast<void*>(&fake_poll_next_event_with_pose);
    g_system011_vtable[30] = reinterpret_cast<void*>(&fake_get_event_type_name);
    g_system011_vtable[31] = reinterpret_cast<void*>(&fake_cpp_legacy_get_hidden_area_mesh);
    g_system011_vtable[32] = reinterpret_cast<void*>(&fake_legacy_get_controller_state);
    g_system011_vtable[33] = reinterpret_cast<void*>(&fake_legacy_get_controller_state_with_pose);
    g_system011_vtable[34] = reinterpret_cast<void*>(&fake_trigger_haptic_pulse);
    g_system011_vtable[35] = reinterpret_cast<void*>(&fake_button_name);
    g_system011_vtable[36] = reinterpret_cast<void*>(&fake_axis_name);
    g_system011_vtable[37] = reinterpret_cast<void*>(&fake_capture_input_focus);
    g_system011_vtable[38] = reinterpret_cast<void*>(&fake_release_input_focus);
    g_system011_vtable[39] = reinterpret_cast<void*>(&fake_is_input_focus_captured_by_another_process);
    g_system011_vtable[40] = reinterpret_cast<void*>(&fake_driver_debug_request);
    g_system011_vtable[41] = reinterpret_cast<void*>(&fake_firmware_update);
    g_system011_vtable[42] = reinterpret_cast<void*>(&fake_acknowledge_quit);
    g_system011_vtable[43] = reinterpret_cast<void*>(&fake_acknowledge_quit);
    g_system011_vtable[44] = reinterpret_cast<void*>(&fake_performance_test_enable_capture);
    g_system011_vtable[45] = reinterpret_cast<void*>(&fake_performance_test_report_fidelity);

    g_system011_fntable[0] = reinterpret_cast<void*>(&fake_c_get_recommended_render_target_size);
    g_system011_fntable[1] = reinterpret_cast<void*>(&fake_c_legacy_get_projection_matrix);
    g_system011_fntable[2] = reinterpret_cast<void*>(&fake_c_get_projection_raw);
    g_system011_fntable[3] = reinterpret_cast<void*>(&fake_c_legacy_compute_distortion);
    g_system011_fntable[4] = reinterpret_cast<void*>(&fake_c_get_eye_to_head_transform);
    g_system011_fntable[5] = reinterpret_cast<void*>(&fake_c_get_time_since_last_vsync);
    g_system011_fntable[6] = reinterpret_cast<void*>(&fake_c_get_d3d9_adapter_index);
    g_system011_fntable[7] = reinterpret_cast<void*>(&fake_c_get_dxgi_output_info);
    g_system011_fntable[8] = reinterpret_cast<void*>(&fake_c_is_display_on_desktop);
    g_system011_fntable[9] = reinterpret_cast<void*>(&fake_c_set_display_visibility);
    g_system011_fntable[10] = reinterpret_cast<void*>(&fake_c_get_tracking_pose);
    g_system011_fntable[11] = reinterpret_cast<void*>(&fake_c_reset_seated_zero_pose);
    g_system011_fntable[12] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system011_fntable[13] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system011_fntable[14] = reinterpret_cast<void*>(&fake_c_get_sorted_tracked_device_indices);
    g_system011_fntable[15] = reinterpret_cast<void*>(&fake_c_get_activity_level);
    g_system011_fntable[16] = reinterpret_cast<void*>(&fake_c_apply_transform);
    g_system011_fntable[17] = reinterpret_cast<void*>(&fake_c_invalid_device_index);
    g_system011_fntable[18] = reinterpret_cast<void*>(&fake_c_invalid_controller_role);
    g_system011_fntable[19] = reinterpret_cast<void*>(&fake_c_get_tracked_device_class);
    g_system011_fntable[20] = reinterpret_cast<void*>(&fake_c_is_tracked_device_connected);
    g_system011_fntable[21] = reinterpret_cast<void*>(&fake_c_get_bool_property);
    g_system011_fntable[22] = reinterpret_cast<void*>(&fake_c_get_float_property);
    g_system011_fntable[23] = reinterpret_cast<void*>(&fake_c_get_int_property);
    g_system011_fntable[24] = reinterpret_cast<void*>(&fake_c_get_uint64_property);
    g_system011_fntable[25] = reinterpret_cast<void*>(&fake_c_get_matrix34_property);
    g_system011_fntable[26] = reinterpret_cast<void*>(&fake_c_get_string_property);
    g_system011_fntable[27] = reinterpret_cast<void*>(&fake_c_get_prop_error_name);
    g_system011_fntable[28] = reinterpret_cast<void*>(&fake_c_poll_next_event);
    g_system011_fntable[29] = reinterpret_cast<void*>(&fake_c_poll_next_event_with_pose);
    g_system011_fntable[30] = reinterpret_cast<void*>(&fake_c_get_event_type_name);
    g_system011_fntable[31] = reinterpret_cast<void*>(&fake_c_legacy_get_hidden_area_mesh);
    g_system011_fntable[32] = reinterpret_cast<void*>(&fake_c_legacy_get_controller_state);
    g_system011_fntable[33] = reinterpret_cast<void*>(&fake_c_legacy_get_controller_state_with_pose);
    g_system011_fntable[34] = reinterpret_cast<void*>(&fake_c_trigger_haptic_pulse);
    g_system011_fntable[35] = reinterpret_cast<void*>(&fake_c_button_name);
    g_system011_fntable[36] = reinterpret_cast<void*>(&fake_c_axis_name);
    g_system011_fntable[37] = reinterpret_cast<void*>(&fake_c_capture_input_focus);
    g_system011_fntable[38] = reinterpret_cast<void*>(&fake_c_release_input_focus);
    g_system011_fntable[39] = reinterpret_cast<void*>(&fake_c_is_input_focus_captured_by_another_process);
    g_system011_fntable[40] = reinterpret_cast<void*>(&fake_c_driver_debug_request);
    g_system011_fntable[41] = reinterpret_cast<void*>(&fake_c_firmware_update);
    g_system011_fntable[42] = reinterpret_cast<void*>(&fake_c_acknowledge_quit);
    g_system011_fntable[43] = reinterpret_cast<void*>(&fake_c_acknowledge_quit);
    g_system011_fntable[44] = reinterpret_cast<void*>(&fake_c_performance_test_enable_capture);
    g_system011_fntable[45] = reinterpret_cast<void*>(&fake_c_performance_test_report_fidelity);

    for (size_t index = 0; index < kLegacySystem019Slots; ++index) {
        g_system019_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_system019_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_system019_vtable[0] = reinterpret_cast<void*>(&fake_get_recommended_render_target_size);
    g_system019_vtable[1] = reinterpret_cast<void*>(&fake_cpp_get_projection_matrix);
    g_system019_vtable[2] = reinterpret_cast<void*>(&fake_get_projection_raw);
    g_system019_vtable[3] = reinterpret_cast<void*>(&fake_compute_distortion);
    g_system019_vtable[4] = reinterpret_cast<void*>(&fake_cpp_get_eye_to_head_transform);
    g_system019_vtable[5] = reinterpret_cast<void*>(&fake_get_time_since_last_vsync);
    g_system019_vtable[6] = reinterpret_cast<void*>(&fake_get_d3d9_adapter_index);
    g_system019_vtable[7] = reinterpret_cast<void*>(&fake_get_dxgi_output_info);
    g_system019_vtable[8] = reinterpret_cast<void*>(&fake_get_output_device);
    g_system019_vtable[9] = reinterpret_cast<void*>(&fake_is_display_on_desktop);
    g_system019_vtable[10] = reinterpret_cast<void*>(&fake_set_display_visibility);
    g_system019_vtable[11] = reinterpret_cast<void*>(&fake_get_tracking_pose);
    g_system019_vtable[12] = reinterpret_cast<void*>(&fake_reset_seated_zero_pose);
    g_system019_vtable[13] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system019_vtable[14] = reinterpret_cast<void*>(&fake_cpp_identity34);
    g_system019_vtable[15] = reinterpret_cast<void*>(&fake_get_sorted_tracked_device_indices);
    g_system019_vtable[16] = reinterpret_cast<void*>(&fake_get_activity_level);
    g_system019_vtable[17] = reinterpret_cast<void*>(&fake_apply_transform);
    g_system019_vtable[18] = reinterpret_cast<void*>(&fake_invalid_device_index);
    g_system019_vtable[19] = reinterpret_cast<void*>(&fake_invalid_controller_role);
    g_system019_vtable[20] = reinterpret_cast<void*>(&fake_get_tracked_device_class);
    g_system019_vtable[21] = reinterpret_cast<void*>(&fake_is_tracked_device_connected);
    g_system019_vtable[22] = reinterpret_cast<void*>(&fake_get_bool_property);
    g_system019_vtable[23] = reinterpret_cast<void*>(&fake_get_float_property);
    g_system019_vtable[24] = reinterpret_cast<void*>(&fake_get_int_property);
    g_system019_vtable[25] = reinterpret_cast<void*>(&fake_get_uint64_property);
    g_system019_vtable[26] = reinterpret_cast<void*>(&fake_cpp_get_matrix34_property);
    g_system019_vtable[27] = reinterpret_cast<void*>(&fake_get_array_property);
    g_system019_vtable[28] = reinterpret_cast<void*>(&fake_get_string_property);
    g_system019_vtable[29] = reinterpret_cast<void*>(&fake_get_prop_error_name);
    g_system019_vtable[30] = reinterpret_cast<void*>(&fake_poll_next_event);
    g_system019_vtable[31] = reinterpret_cast<void*>(&fake_poll_next_event_with_pose);
    g_system019_vtable[32] = reinterpret_cast<void*>(&fake_get_event_type_name);
    g_system019_vtable[33] = reinterpret_cast<void*>(&fake_cpp_get_hidden_area_mesh);
    g_system019_vtable[34] = reinterpret_cast<void*>(&fake_get_controller_state);
    g_system019_vtable[35] = reinterpret_cast<void*>(&fake_get_controller_state_with_pose);
    g_system019_vtable[36] = reinterpret_cast<void*>(&fake_trigger_haptic_pulse);
    g_system019_vtable[37] = reinterpret_cast<void*>(&fake_button_name);
    g_system019_vtable[38] = reinterpret_cast<void*>(&fake_axis_name);
    g_system019_vtable[39] = reinterpret_cast<void*>(&fake_is_input_available);
    g_system019_vtable[40] = reinterpret_cast<void*>(&fake_is_steamvr_drawing_controllers);
    g_system019_vtable[41] = reinterpret_cast<void*>(&fake_should_application_pause);
    g_system019_vtable[42] = reinterpret_cast<void*>(&fake_should_application_reduce_rendering_work);
    g_system019_vtable[43] = reinterpret_cast<void*>(&fake_driver_debug_request);
    g_system019_vtable[44] = reinterpret_cast<void*>(&fake_firmware_update);
    g_system019_vtable[45] = reinterpret_cast<void*>(&fake_acknowledge_quit);
    g_system019_vtable[46] = reinterpret_cast<void*>(&fake_acknowledge_quit);

    g_system019_fntable[0] = reinterpret_cast<void*>(&fake_c_get_recommended_render_target_size);
    g_system019_fntable[1] = reinterpret_cast<void*>(&fake_c_get_projection_matrix);
    g_system019_fntable[2] = reinterpret_cast<void*>(&fake_c_get_projection_raw);
    g_system019_fntable[3] = reinterpret_cast<void*>(&fake_c_compute_distortion);
    g_system019_fntable[4] = reinterpret_cast<void*>(&fake_c_get_eye_to_head_transform);
    g_system019_fntable[5] = reinterpret_cast<void*>(&fake_c_get_time_since_last_vsync);
    g_system019_fntable[6] = reinterpret_cast<void*>(&fake_c_get_d3d9_adapter_index);
    g_system019_fntable[7] = reinterpret_cast<void*>(&fake_c_get_dxgi_output_info);
    g_system019_fntable[8] = reinterpret_cast<void*>(&fake_c_get_output_device);
    g_system019_fntable[9] = reinterpret_cast<void*>(&fake_c_is_display_on_desktop);
    g_system019_fntable[10] = reinterpret_cast<void*>(&fake_c_set_display_visibility);
    g_system019_fntable[11] = reinterpret_cast<void*>(&fake_c_get_tracking_pose);
    g_system019_fntable[12] = reinterpret_cast<void*>(&fake_c_reset_seated_zero_pose);
    g_system019_fntable[13] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system019_fntable[14] = reinterpret_cast<void*>(&fake_c_identity34);
    g_system019_fntable[15] = reinterpret_cast<void*>(&fake_c_get_sorted_tracked_device_indices);
    g_system019_fntable[16] = reinterpret_cast<void*>(&fake_c_get_activity_level);
    g_system019_fntable[17] = reinterpret_cast<void*>(&fake_c_apply_transform);
    g_system019_fntable[18] = reinterpret_cast<void*>(&fake_c_invalid_device_index);
    g_system019_fntable[19] = reinterpret_cast<void*>(&fake_c_invalid_controller_role);
    g_system019_fntable[20] = reinterpret_cast<void*>(&fake_c_get_tracked_device_class);
    g_system019_fntable[21] = reinterpret_cast<void*>(&fake_c_is_tracked_device_connected);
    g_system019_fntable[22] = reinterpret_cast<void*>(&fake_c_get_bool_property);
    g_system019_fntable[23] = reinterpret_cast<void*>(&fake_c_get_float_property);
    g_system019_fntable[24] = reinterpret_cast<void*>(&fake_c_get_int_property);
    g_system019_fntable[25] = reinterpret_cast<void*>(&fake_c_get_uint64_property);
    g_system019_fntable[26] = reinterpret_cast<void*>(&fake_c_get_matrix34_property);
    g_system019_fntable[27] = reinterpret_cast<void*>(&fake_c_get_array_property);
    g_system019_fntable[28] = reinterpret_cast<void*>(&fake_c_get_string_property);
    g_system019_fntable[29] = reinterpret_cast<void*>(&fake_c_get_prop_error_name);
    g_system019_fntable[30] = reinterpret_cast<void*>(&fake_c_poll_next_event);
    g_system019_fntable[31] = reinterpret_cast<void*>(&fake_c_poll_next_event_with_pose);
    g_system019_fntable[32] = reinterpret_cast<void*>(&fake_c_get_event_type_name);
    g_system019_fntable[33] = reinterpret_cast<void*>(&fake_c_get_hidden_area_mesh);
    g_system019_fntable[34] = reinterpret_cast<void*>(&fake_c_get_controller_state);
    g_system019_fntable[35] = reinterpret_cast<void*>(&fake_c_get_controller_state_with_pose);
    g_system019_fntable[36] = reinterpret_cast<void*>(&fake_c_trigger_haptic_pulse);
    g_system019_fntable[37] = reinterpret_cast<void*>(&fake_c_button_name);
    g_system019_fntable[38] = reinterpret_cast<void*>(&fake_c_axis_name);
    g_system019_fntable[39] = reinterpret_cast<void*>(&fake_c_is_input_available);
    g_system019_fntable[40] = reinterpret_cast<void*>(&fake_c_is_steamvr_drawing_controllers);
    g_system019_fntable[41] = reinterpret_cast<void*>(&fake_c_should_application_pause);
    g_system019_fntable[42] = reinterpret_cast<void*>(&fake_c_should_application_reduce_rendering_work);
    g_system019_fntable[43] = reinterpret_cast<void*>(&fake_c_driver_debug_request);
    g_system019_fntable[44] = reinterpret_cast<void*>(&fake_c_firmware_update);
    g_system019_fntable[45] = reinterpret_cast<void*>(&fake_c_acknowledge_quit);
    g_system019_fntable[46] = reinterpret_cast<void*>(&fake_c_acknowledge_quit);

    for (size_t index = 0; index < kCompositorSlots; ++index) {
        g_compositor_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_compositor_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_compositor_vtable[0] = reinterpret_cast<void*>(&fake_set_tracking_space);
    g_compositor_vtable[1] = reinterpret_cast<void*>(&fake_tracking_space);
    g_compositor_vtable[2] = reinterpret_cast<void*>(&fake_wait_get_poses);
    g_compositor_vtable[3] = reinterpret_cast<void*>(&fake_get_last_poses);
    g_compositor_vtable[4] = reinterpret_cast<void*>(&fake_last_pose);
    g_compositor_vtable[5] = reinterpret_cast<void*>(&fake_submit);
    g_compositor_vtable[6] = reinterpret_cast<void*>(&fake_clear_last_submitted_frame);
    g_compositor_vtable[7] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[8] = reinterpret_cast<void*>(&fake_get_frame_timing);
    g_compositor_vtable[9] = reinterpret_cast<void*>(&fake_get_frame_timings);
    g_compositor_vtable[10] = reinterpret_cast<void*>(&fake_get_frame_time_remaining);
    g_compositor_vtable[11] = reinterpret_cast<void*>(&fake_get_cumulative_stats);
    g_compositor_vtable[12] = reinterpret_cast<void*>(&fake_fade_to_color);
    g_compositor_vtable[13] = reinterpret_cast<void*>(&fake_cpp_current_fade_color);
    g_compositor_vtable[14] = reinterpret_cast<void*>(&fake_fade_grid);
    g_compositor_vtable[15] = reinterpret_cast<void*>(&fake_grid_alpha);
    g_compositor_vtable[16] = reinterpret_cast<void*>(&fake_compositor_ok);
    g_compositor_vtable[17] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[18] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[19] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[20] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[21] = reinterpret_cast<void*>(&fake_compositor_is_fullscreen);
    g_compositor_vtable[22] = reinterpret_cast<void*>(&fake_get_current_scene_focus_process);
    g_compositor_vtable[23] = reinterpret_cast<void*>(&fake_get_last_frame_renderer);
    g_compositor_vtable[24] = reinterpret_cast<void*>(&fake_can_render_scene);
    g_compositor_vtable[25] = reinterpret_cast<void*>(&fake_show_mirror_window);
    g_compositor_vtable[26] = reinterpret_cast<void*>(&fake_hide_mirror_window);
    g_compositor_vtable[27] = reinterpret_cast<void*>(&fake_is_mirror_window_visible);
    g_compositor_vtable[28] = reinterpret_cast<void*>(&fake_compositor_dump_images);
    g_compositor_vtable[29] = reinterpret_cast<void*>(&fake_should_app_render_with_low_resources);
    g_compositor_vtable[30] = reinterpret_cast<void*>(&fake_noop_bool);
    g_compositor_vtable[31] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor_vtable[32] = reinterpret_cast<void*>(&fake_noop_bool);
    g_compositor_vtable[33] = reinterpret_cast<void*>(&fake_compositor_request_failed);
    g_compositor_vtable[34] = reinterpret_cast<void*>(&fake_ret0);
    g_compositor_vtable[35] = reinterpret_cast<void*>(&fake_compositor_request_failed);
    g_compositor_vtable[36] = reinterpret_cast<void*>(&fake_release_shared_gl_texture);
    g_compositor_vtable[37] = reinterpret_cast<void*>(&fake_gl_shared_texture_access);
    g_compositor_vtable[38] = reinterpret_cast<void*>(&fake_gl_shared_texture_access);
    g_compositor_vtable[39] = reinterpret_cast<void*>(&fake_compositor_string0);
    g_compositor_vtable[40] = reinterpret_cast<void*>(&fake_compositor_device_string0);
    g_compositor_vtable[41] = reinterpret_cast<void*>(&fake_set_explicit_timing_mode);
    g_compositor_vtable[42] = reinterpret_cast<void*>(&fake_compositor_request_failed);

    g_compositor_fntable[0] = reinterpret_cast<void*>(&fake_c_set_tracking_space);
    g_compositor_fntable[1] = reinterpret_cast<void*>(&fake_c_tracking_space);
    g_compositor_fntable[2] = reinterpret_cast<void*>(&fake_c_wait_get_poses);
    g_compositor_fntable[3] = reinterpret_cast<void*>(&fake_c_get_last_poses);
    g_compositor_fntable[4] = reinterpret_cast<void*>(&fake_c_last_pose);
    g_compositor_fntable[5] = reinterpret_cast<void*>(&fake_c_submit);
    g_compositor_fntable[6] = reinterpret_cast<void*>(&fake_c_clear_last_submitted_frame);
    g_compositor_fntable[7] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[8] = reinterpret_cast<void*>(&fake_c_get_frame_timing);
    g_compositor_fntable[9] = reinterpret_cast<void*>(&fake_c_get_frame_timings);
    g_compositor_fntable[10] = reinterpret_cast<void*>(&fake_c_get_frame_time_remaining);
    g_compositor_fntable[11] = reinterpret_cast<void*>(&fake_c_get_cumulative_stats);
    g_compositor_fntable[12] = reinterpret_cast<void*>(&fake_c_fade_to_color);
    g_compositor_fntable[13] = reinterpret_cast<void*>(&fake_c_current_fade_color);
    g_compositor_fntable[14] = reinterpret_cast<void*>(&fake_c_fade_grid);
    g_compositor_fntable[15] = reinterpret_cast<void*>(&fake_c_grid_alpha);
    g_compositor_fntable[16] = reinterpret_cast<void*>(&fake_c_compositor_ok);
    g_compositor_fntable[17] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[18] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[19] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[20] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[21] = reinterpret_cast<void*>(&fake_c_compositor_is_fullscreen);
    g_compositor_fntable[22] = reinterpret_cast<void*>(&fake_c_get_current_scene_focus_process);
    g_compositor_fntable[23] = reinterpret_cast<void*>(&fake_c_get_last_frame_renderer);
    g_compositor_fntable[24] = reinterpret_cast<void*>(&fake_c_can_render_scene);
    g_compositor_fntable[25] = reinterpret_cast<void*>(&fake_c_show_mirror_window);
    g_compositor_fntable[26] = reinterpret_cast<void*>(&fake_c_hide_mirror_window);
    g_compositor_fntable[27] = reinterpret_cast<void*>(&fake_c_is_mirror_window_visible);
    g_compositor_fntable[28] = reinterpret_cast<void*>(&fake_c_compositor_dump_images);
    g_compositor_fntable[29] = reinterpret_cast<void*>(&fake_c_should_app_render_with_low_resources);
    g_compositor_fntable[30] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_compositor_fntable[31] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor_fntable[32] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_compositor_fntable[33] = reinterpret_cast<void*>(&fake_c_compositor_request_failed);
    g_compositor_fntable[34] = reinterpret_cast<void*>(&fake_c_ret0);
    g_compositor_fntable[35] = reinterpret_cast<void*>(&fake_c_compositor_request_failed);
    g_compositor_fntable[36] = reinterpret_cast<void*>(&fake_c_release_shared_gl_texture);
    g_compositor_fntable[37] = reinterpret_cast<void*>(&fake_c_gl_shared_texture_access);
    g_compositor_fntable[38] = reinterpret_cast<void*>(&fake_c_gl_shared_texture_access);
    g_compositor_fntable[39] = reinterpret_cast<void*>(&fake_c_compositor_string0);
    g_compositor_fntable[40] = reinterpret_cast<void*>(&fake_c_compositor_device_string0);
    g_compositor_fntable[41] = reinterpret_cast<void*>(&fake_c_set_explicit_timing_mode);
    g_compositor_fntable[42] = reinterpret_cast<void*>(&fake_c_compositor_request_failed);

    for (size_t index = 0; index < kLegacyCompositor013Slots; ++index) {
        g_compositor013_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_compositor013_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_compositor013_vtable[0] = reinterpret_cast<void*>(&fake_set_tracking_space);
    g_compositor013_vtable[1] = reinterpret_cast<void*>(&fake_tracking_space);
    g_compositor013_vtable[2] = reinterpret_cast<void*>(&fake_wait_get_poses);
    g_compositor013_vtable[3] = reinterpret_cast<void*>(&fake_get_last_poses);
    g_compositor013_vtable[4] = reinterpret_cast<void*>(&fake_last_pose);
    g_compositor013_vtable[5] = reinterpret_cast<void*>(&fake_submit);
    g_compositor013_vtable[6] = reinterpret_cast<void*>(&fake_clear_last_submitted_frame);
    g_compositor013_vtable[7] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor013_vtable[8] = reinterpret_cast<void*>(&fake_get_frame_timing);
    g_compositor013_vtable[9] = reinterpret_cast<void*>(&fake_get_frame_time_remaining);
    g_compositor013_vtable[10] = reinterpret_cast<void*>(&fake_fade_to_color);
    g_compositor013_vtable[11] = reinterpret_cast<void*>(&fake_fade_grid);
    g_compositor013_vtable[12] = reinterpret_cast<void*>(&fake_set_skybox_override);
    g_compositor013_vtable[13] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor013_vtable[14] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor013_vtable[15] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor013_vtable[16] = reinterpret_cast<void*>(&fake_compositor_is_fullscreen);
    g_compositor013_vtable[17] = reinterpret_cast<void*>(&fake_get_current_scene_focus_process);
    g_compositor013_vtable[18] = reinterpret_cast<void*>(&fake_get_last_frame_renderer);
    g_compositor013_vtable[19] = reinterpret_cast<void*>(&fake_can_render_scene);
    g_compositor013_vtable[20] = reinterpret_cast<void*>(&fake_show_mirror_window);
    g_compositor013_vtable[21] = reinterpret_cast<void*>(&fake_hide_mirror_window);
    g_compositor013_vtable[22] = reinterpret_cast<void*>(&fake_is_mirror_window_visible);
    g_compositor013_vtable[23] = reinterpret_cast<void*>(&fake_compositor_dump_images);
    g_compositor013_vtable[24] = reinterpret_cast<void*>(&fake_should_app_render_with_low_resources);
    g_compositor013_vtable[25] = reinterpret_cast<void*>(&fake_noop_bool);
    g_compositor013_vtable[26] = reinterpret_cast<void*>(&fake_noop_void);

    g_compositor013_fntable[0] = reinterpret_cast<void*>(&fake_c_set_tracking_space);
    g_compositor013_fntable[1] = reinterpret_cast<void*>(&fake_c_tracking_space);
    g_compositor013_fntable[2] = reinterpret_cast<void*>(&fake_c_wait_get_poses);
    g_compositor013_fntable[3] = reinterpret_cast<void*>(&fake_c_get_last_poses);
    g_compositor013_fntable[4] = reinterpret_cast<void*>(&fake_c_last_pose);
    g_compositor013_fntable[5] = reinterpret_cast<void*>(&fake_c_submit);
    g_compositor013_fntable[6] = reinterpret_cast<void*>(&fake_c_clear_last_submitted_frame);
    g_compositor013_fntable[7] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor013_fntable[8] = reinterpret_cast<void*>(&fake_c_get_frame_timing);
    g_compositor013_fntable[9] = reinterpret_cast<void*>(&fake_c_get_frame_time_remaining);
    g_compositor013_fntable[10] = reinterpret_cast<void*>(&fake_c_fade_to_color);
    g_compositor013_fntable[11] = reinterpret_cast<void*>(&fake_c_fade_grid);
    g_compositor013_fntable[12] = reinterpret_cast<void*>(&fake_c_set_skybox_override);
    g_compositor013_fntable[13] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor013_fntable[14] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor013_fntable[15] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor013_fntable[16] = reinterpret_cast<void*>(&fake_c_compositor_is_fullscreen);
    g_compositor013_fntable[17] = reinterpret_cast<void*>(&fake_c_get_current_scene_focus_process);
    g_compositor013_fntable[18] = reinterpret_cast<void*>(&fake_c_get_last_frame_renderer);
    g_compositor013_fntable[19] = reinterpret_cast<void*>(&fake_c_can_render_scene);
    g_compositor013_fntable[20] = reinterpret_cast<void*>(&fake_c_show_mirror_window);
    g_compositor013_fntable[21] = reinterpret_cast<void*>(&fake_c_hide_mirror_window);
    g_compositor013_fntable[22] = reinterpret_cast<void*>(&fake_c_is_mirror_window_visible);
    g_compositor013_fntable[23] = reinterpret_cast<void*>(&fake_c_compositor_dump_images);
    g_compositor013_fntable[24] = reinterpret_cast<void*>(&fake_c_should_app_render_with_low_resources);
    g_compositor013_fntable[25] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_compositor013_fntable[26] = reinterpret_cast<void*>(&fake_c_noop_void);

    for (size_t index = 0; index < kLegacyCompositor014Slots; ++index) {
        g_compositor014_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_compositor014_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_compositor014_vtable[0] = reinterpret_cast<void*>(&fake_set_tracking_space);
    g_compositor014_vtable[1] = reinterpret_cast<void*>(&fake_tracking_space);
    g_compositor014_vtable[2] = reinterpret_cast<void*>(&fake_wait_get_poses);
    g_compositor014_vtable[3] = reinterpret_cast<void*>(&fake_get_last_poses);
    g_compositor014_vtable[4] = reinterpret_cast<void*>(&fake_last_pose);
    g_compositor014_vtable[5] = reinterpret_cast<void*>(&fake_submit);
    g_compositor014_vtable[6] = reinterpret_cast<void*>(&fake_clear_last_submitted_frame);
    g_compositor014_vtable[7] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor014_vtable[8] = reinterpret_cast<void*>(&fake_get_frame_timing);
    g_compositor014_vtable[9] = reinterpret_cast<void*>(&fake_get_frame_time_remaining);
    g_compositor014_vtable[10] = reinterpret_cast<void*>(&fake_fade_to_color);
    g_compositor014_vtable[11] = reinterpret_cast<void*>(&fake_fade_grid);
    g_compositor014_vtable[12] = reinterpret_cast<void*>(&fake_set_skybox_override);
    g_compositor014_vtable[13] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor014_vtable[14] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor014_vtable[15] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor014_vtable[16] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor014_vtable[17] = reinterpret_cast<void*>(&fake_compositor_is_fullscreen);
    g_compositor014_vtable[18] = reinterpret_cast<void*>(&fake_get_current_scene_focus_process);
    g_compositor014_vtable[19] = reinterpret_cast<void*>(&fake_get_last_frame_renderer);
    g_compositor014_vtable[20] = reinterpret_cast<void*>(&fake_can_render_scene);
    g_compositor014_vtable[21] = reinterpret_cast<void*>(&fake_show_mirror_window);
    g_compositor014_vtable[22] = reinterpret_cast<void*>(&fake_hide_mirror_window);
    g_compositor014_vtable[23] = reinterpret_cast<void*>(&fake_is_mirror_window_visible);
    g_compositor014_vtable[24] = reinterpret_cast<void*>(&fake_compositor_dump_images);
    g_compositor014_vtable[25] = reinterpret_cast<void*>(&fake_should_app_render_with_low_resources);
    g_compositor014_vtable[26] = reinterpret_cast<void*>(&fake_noop_bool);
    g_compositor014_vtable[27] = reinterpret_cast<void*>(&fake_noop_void);
    g_compositor014_vtable[28] = reinterpret_cast<void*>(&fake_noop_bool);

    g_compositor014_fntable[0] = reinterpret_cast<void*>(&fake_c_set_tracking_space);
    g_compositor014_fntable[1] = reinterpret_cast<void*>(&fake_c_tracking_space);
    g_compositor014_fntable[2] = reinterpret_cast<void*>(&fake_c_wait_get_poses);
    g_compositor014_fntable[3] = reinterpret_cast<void*>(&fake_c_get_last_poses);
    g_compositor014_fntable[4] = reinterpret_cast<void*>(&fake_c_last_pose);
    g_compositor014_fntable[5] = reinterpret_cast<void*>(&fake_c_submit);
    g_compositor014_fntable[6] = reinterpret_cast<void*>(&fake_c_clear_last_submitted_frame);
    g_compositor014_fntable[7] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor014_fntable[8] = reinterpret_cast<void*>(&fake_c_get_frame_timing);
    g_compositor014_fntable[9] = reinterpret_cast<void*>(&fake_c_get_frame_time_remaining);
    g_compositor014_fntable[10] = reinterpret_cast<void*>(&fake_c_fade_to_color);
    g_compositor014_fntable[11] = reinterpret_cast<void*>(&fake_c_fade_grid);
    g_compositor014_fntable[12] = reinterpret_cast<void*>(&fake_c_set_skybox_override);
    g_compositor014_fntable[13] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor014_fntable[14] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor014_fntable[15] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor014_fntable[16] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor014_fntable[17] = reinterpret_cast<void*>(&fake_c_compositor_is_fullscreen);
    g_compositor014_fntable[18] = reinterpret_cast<void*>(&fake_c_get_current_scene_focus_process);
    g_compositor014_fntable[19] = reinterpret_cast<void*>(&fake_c_get_last_frame_renderer);
    g_compositor014_fntable[20] = reinterpret_cast<void*>(&fake_c_can_render_scene);
    g_compositor014_fntable[21] = reinterpret_cast<void*>(&fake_c_show_mirror_window);
    g_compositor014_fntable[22] = reinterpret_cast<void*>(&fake_c_hide_mirror_window);
    g_compositor014_fntable[23] = reinterpret_cast<void*>(&fake_c_is_mirror_window_visible);
    g_compositor014_fntable[24] = reinterpret_cast<void*>(&fake_c_compositor_dump_images);
    g_compositor014_fntable[25] = reinterpret_cast<void*>(&fake_c_should_app_render_with_low_resources);
    g_compositor014_fntable[26] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_compositor014_fntable[27] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_compositor014_fntable[28] = reinterpret_cast<void*>(&fake_c_noop_bool);

    for (size_t index = 0; index < kLegacyCompositor016Slots; ++index) {
        g_compositor016_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_compositor016_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_compositor016_vtable[0] = reinterpret_cast<void*>(&fake_set_tracking_space);
    g_compositor016_vtable[1] = reinterpret_cast<void*>(&fake_tracking_space);
    g_compositor016_vtable[2] = reinterpret_cast<void*>(&fake_wait_get_poses);
    g_compositor016_vtable[3] = reinterpret_cast<void*>(&fake_get_last_poses);
    g_compositor016_vtable[4] = reinterpret_cast<void*>(&fake_last_pose);
    g_compositor016_vtable[5] = reinterpret_cast<void*>(&fake_submit);
    g_compositor016_vtable[6] = reinterpret_cast<void*>(&fake_clear_last_submitted_frame);
    g_compositor016_vtable[7] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor016_vtable[8] = reinterpret_cast<void*>(&fake_get_frame_timing);
    g_compositor016_vtable[9] = reinterpret_cast<void*>(&fake_get_frame_time_remaining);
    g_compositor016_vtable[10] = reinterpret_cast<void*>(&fake_get_cumulative_stats);
    g_compositor016_vtable[11] = reinterpret_cast<void*>(&fake_fade_to_color);
    g_compositor016_vtable[12] = reinterpret_cast<void*>(&fake_fade_grid);
    g_compositor016_vtable[13] = reinterpret_cast<void*>(&fake_set_skybox_override);
    g_compositor016_vtable[14] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor016_vtable[15] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor016_vtable[16] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor016_vtable[17] = reinterpret_cast<void*>(&fake_post_present_handoff);
    g_compositor016_vtable[18] = reinterpret_cast<void*>(&fake_compositor_is_fullscreen);
    g_compositor016_vtable[19] = reinterpret_cast<void*>(&fake_get_current_scene_focus_process);
    g_compositor016_vtable[20] = reinterpret_cast<void*>(&fake_get_last_frame_renderer);
    g_compositor016_vtable[21] = reinterpret_cast<void*>(&fake_can_render_scene);
    g_compositor016_vtable[22] = reinterpret_cast<void*>(&fake_show_mirror_window);
    g_compositor016_vtable[23] = reinterpret_cast<void*>(&fake_hide_mirror_window);
    g_compositor016_vtable[24] = reinterpret_cast<void*>(&fake_is_mirror_window_visible);
    g_compositor016_vtable[25] = reinterpret_cast<void*>(&fake_compositor_dump_images);
    g_compositor016_vtable[26] = reinterpret_cast<void*>(&fake_should_app_render_with_low_resources);
    g_compositor016_vtable[27] = reinterpret_cast<void*>(&fake_noop_bool);
    g_compositor016_vtable[28] = reinterpret_cast<void*>(&fake_noop_void);
    g_compositor016_vtable[29] = reinterpret_cast<void*>(&fake_noop_bool);
    g_compositor016_vtable[30] = reinterpret_cast<void*>(&fake_compositor_request_failed);
    g_compositor016_vtable[31] = reinterpret_cast<void*>(&fake_compositor_request_failed);
    g_compositor016_vtable[32] = reinterpret_cast<void*>(&fake_release_shared_gl_texture);
    g_compositor016_vtable[33] = reinterpret_cast<void*>(&fake_gl_shared_texture_access);
    g_compositor016_vtable[34] = reinterpret_cast<void*>(&fake_gl_shared_texture_access);

    g_compositor016_fntable[0] = reinterpret_cast<void*>(&fake_c_set_tracking_space);
    g_compositor016_fntable[1] = reinterpret_cast<void*>(&fake_c_tracking_space);
    g_compositor016_fntable[2] = reinterpret_cast<void*>(&fake_c_wait_get_poses);
    g_compositor016_fntable[3] = reinterpret_cast<void*>(&fake_c_get_last_poses);
    g_compositor016_fntable[4] = reinterpret_cast<void*>(&fake_c_last_pose);
    g_compositor016_fntable[5] = reinterpret_cast<void*>(&fake_c_submit);
    g_compositor016_fntable[6] = reinterpret_cast<void*>(&fake_c_clear_last_submitted_frame);
    g_compositor016_fntable[7] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor016_fntable[8] = reinterpret_cast<void*>(&fake_c_get_frame_timing);
    g_compositor016_fntable[9] = reinterpret_cast<void*>(&fake_c_get_frame_time_remaining);
    g_compositor016_fntable[10] = reinterpret_cast<void*>(&fake_c_get_cumulative_stats);
    g_compositor016_fntable[11] = reinterpret_cast<void*>(&fake_c_fade_to_color);
    g_compositor016_fntable[12] = reinterpret_cast<void*>(&fake_c_fade_grid);
    g_compositor016_fntable[13] = reinterpret_cast<void*>(&fake_c_set_skybox_override);
    g_compositor016_fntable[14] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor016_fntable[15] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor016_fntable[16] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor016_fntable[17] = reinterpret_cast<void*>(&fake_c_post_present_handoff);
    g_compositor016_fntable[18] = reinterpret_cast<void*>(&fake_c_compositor_is_fullscreen);
    g_compositor016_fntable[19] = reinterpret_cast<void*>(&fake_c_get_current_scene_focus_process);
    g_compositor016_fntable[20] = reinterpret_cast<void*>(&fake_c_get_last_frame_renderer);
    g_compositor016_fntable[21] = reinterpret_cast<void*>(&fake_c_can_render_scene);
    g_compositor016_fntable[22] = reinterpret_cast<void*>(&fake_c_show_mirror_window);
    g_compositor016_fntable[23] = reinterpret_cast<void*>(&fake_c_hide_mirror_window);
    g_compositor016_fntable[24] = reinterpret_cast<void*>(&fake_c_is_mirror_window_visible);
    g_compositor016_fntable[25] = reinterpret_cast<void*>(&fake_c_compositor_dump_images);
    g_compositor016_fntable[26] = reinterpret_cast<void*>(&fake_c_should_app_render_with_low_resources);
    g_compositor016_fntable[27] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_compositor016_fntable[28] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_compositor016_fntable[29] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_compositor016_fntable[30] = reinterpret_cast<void*>(&fake_c_compositor_request_failed);
    g_compositor016_fntable[31] = reinterpret_cast<void*>(&fake_c_compositor_request_failed);
    g_compositor016_fntable[32] = reinterpret_cast<void*>(&fake_c_release_shared_gl_texture);
    g_compositor016_fntable[33] = reinterpret_cast<void*>(&fake_c_gl_shared_texture_access);
    g_compositor016_fntable[34] = reinterpret_cast<void*>(&fake_c_gl_shared_texture_access);

    for (size_t index = 0; index < kChaperoneSlots; ++index) {
        g_chaperone_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_chaperone_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_chaperone_vtable[0] = reinterpret_cast<void*>(&fake_get_chaperone_calibration_state);
    g_chaperone_vtable[1] = reinterpret_cast<void*>(&fake_get_play_area_size);
    g_chaperone_vtable[2] = reinterpret_cast<void*>(&fake_get_play_area_rect);
    g_chaperone_vtable[3] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_vtable[4] = reinterpret_cast<void*>(&fake_noop_color);
    g_chaperone_vtable[5] = reinterpret_cast<void*>(&fake_get_bounds_color);
    g_chaperone_vtable[6] = reinterpret_cast<void*>(&fake_false);
    g_chaperone_vtable[7] = reinterpret_cast<void*>(&fake_noop_bool);
    g_chaperone_vtable[8] = reinterpret_cast<void*>(&fake_noop_void);

    g_chaperone_fntable[0] = reinterpret_cast<void*>(&fake_c_get_chaperone_calibration_state);
    g_chaperone_fntable[1] = reinterpret_cast<void*>(&fake_c_get_play_area_size);
    g_chaperone_fntable[2] = reinterpret_cast<void*>(&fake_c_get_play_area_rect);
    g_chaperone_fntable[3] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_fntable[4] = reinterpret_cast<void*>(&fake_c_noop_color);
    g_chaperone_fntable[5] = reinterpret_cast<void*>(&fake_c_get_bounds_color);
    g_chaperone_fntable[6] = reinterpret_cast<void*>(&fake_c_false);
    g_chaperone_fntable[7] = reinterpret_cast<void*>(&fake_c_noop_bool);
    g_chaperone_fntable[8] = reinterpret_cast<void*>(&fake_c_noop_void);

    for (size_t index = 0; index < kChaperoneSetupSlots; ++index) {
        g_chaperone_setup_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_chaperone_setup_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_chaperone_setup_vtable[0] = reinterpret_cast<void*>(&fake_commit_working_copy);
    g_chaperone_setup_vtable[1] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_setup_vtable[2] = reinterpret_cast<void*>(&fake_get_play_area_size);
    g_chaperone_setup_vtable[3] = reinterpret_cast<void*>(&fake_get_play_area_rect);
    g_chaperone_setup_vtable[4] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup_vtable[5] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup_vtable[6] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup_vtable[7] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup_vtable[8] = reinterpret_cast<void*>(&fake_set_play_area_size);
    g_chaperone_setup_vtable[9] = reinterpret_cast<void*>(&fake_set_collision_bounds);
    g_chaperone_setup_vtable[10] = reinterpret_cast<void*>(&fake_set_perimeter);
    g_chaperone_setup_vtable[11] = reinterpret_cast<void*>(&fake_set_pose34);
    g_chaperone_setup_vtable[12] = reinterpret_cast<void*>(&fake_set_pose34);
    g_chaperone_setup_vtable[13] = reinterpret_cast<void*>(&fake_reload_from_disk);
    g_chaperone_setup_vtable[14] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup_vtable[15] = reinterpret_cast<void*>(&fake_export_buffer);
    g_chaperone_setup_vtable[16] = reinterpret_cast<void*>(&fake_import_buffer);
    g_chaperone_setup_vtable[17] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_setup_vtable[18] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_setup_vtable[19] = reinterpret_cast<void*>(&fake_noop_void);

    g_chaperone_setup_fntable[0] = reinterpret_cast<void*>(&fake_c_commit_working_copy);
    g_chaperone_setup_fntable[1] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_setup_fntable[2] = reinterpret_cast<void*>(&fake_c_get_play_area_size);
    g_chaperone_setup_fntable[3] = reinterpret_cast<void*>(&fake_c_get_play_area_rect);
    g_chaperone_setup_fntable[4] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup_fntable[5] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup_fntable[6] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup_fntable[7] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup_fntable[8] = reinterpret_cast<void*>(&fake_c_set_play_area_size);
    g_chaperone_setup_fntable[9] = reinterpret_cast<void*>(&fake_c_set_collision_bounds);
    g_chaperone_setup_fntable[10] = reinterpret_cast<void*>(&fake_c_set_perimeter);
    g_chaperone_setup_fntable[11] = reinterpret_cast<void*>(&fake_c_set_pose34);
    g_chaperone_setup_fntable[12] = reinterpret_cast<void*>(&fake_c_set_pose34);
    g_chaperone_setup_fntable[13] = reinterpret_cast<void*>(&fake_c_reload_from_disk);
    g_chaperone_setup_fntable[14] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup_fntable[15] = reinterpret_cast<void*>(&fake_c_export_buffer);
    g_chaperone_setup_fntable[16] = reinterpret_cast<void*>(&fake_c_import_buffer);
    g_chaperone_setup_fntable[17] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_setup_fntable[18] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_setup_fntable[19] = reinterpret_cast<void*>(&fake_c_noop_void);

    for (size_t index = 0; index < kChaperoneSetupSlots; ++index) {
        g_chaperone_setup005_vtable[index] = reinterpret_cast<void*>(&fake_ret0);
        g_chaperone_setup005_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_chaperone_setup005_vtable[0] = reinterpret_cast<void*>(&fake_commit_working_copy);
    g_chaperone_setup005_vtable[1] = reinterpret_cast<void*>(&fake_noop_void);
    g_chaperone_setup005_vtable[2] = reinterpret_cast<void*>(&fake_get_play_area_size);
    g_chaperone_setup005_vtable[3] = reinterpret_cast<void*>(&fake_get_play_area_rect);
    g_chaperone_setup005_vtable[4] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup005_vtable[5] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup005_vtable[6] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup005_vtable[7] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup005_vtable[8] = reinterpret_cast<void*>(&fake_set_play_area_size);
    g_chaperone_setup005_vtable[9] = reinterpret_cast<void*>(&fake_set_collision_bounds);
    g_chaperone_setup005_vtable[10] = reinterpret_cast<void*>(&fake_set_pose34);
    g_chaperone_setup005_vtable[11] = reinterpret_cast<void*>(&fake_set_pose34);
    g_chaperone_setup005_vtable[12] = reinterpret_cast<void*>(&fake_reload_from_disk);
    g_chaperone_setup005_vtable[13] = reinterpret_cast<void*>(&fake_get_pose34);
    g_chaperone_setup005_vtable[14] = reinterpret_cast<void*>(&fake_set_tags);
    g_chaperone_setup005_vtable[15] = reinterpret_cast<void*>(&fake_get_tags);
    g_chaperone_setup005_vtable[16] = reinterpret_cast<void*>(&fake_set_physical_bounds);
    g_chaperone_setup005_vtable[17] = reinterpret_cast<void*>(&fake_collision_bounds);
    g_chaperone_setup005_vtable[18] = reinterpret_cast<void*>(&fake_export_buffer);
    g_chaperone_setup005_vtable[19] = reinterpret_cast<void*>(&fake_import_buffer);

    g_chaperone_setup005_fntable[0] = reinterpret_cast<void*>(&fake_c_commit_working_copy);
    g_chaperone_setup005_fntable[1] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_chaperone_setup005_fntable[2] = reinterpret_cast<void*>(&fake_c_get_play_area_size);
    g_chaperone_setup005_fntable[3] = reinterpret_cast<void*>(&fake_c_get_play_area_rect);
    g_chaperone_setup005_fntable[4] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup005_fntable[5] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup005_fntable[6] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup005_fntable[7] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup005_fntable[8] = reinterpret_cast<void*>(&fake_c_set_play_area_size);
    g_chaperone_setup005_fntable[9] = reinterpret_cast<void*>(&fake_c_set_collision_bounds);
    g_chaperone_setup005_fntable[10] = reinterpret_cast<void*>(&fake_c_set_pose34);
    g_chaperone_setup005_fntable[11] = reinterpret_cast<void*>(&fake_c_set_pose34);
    g_chaperone_setup005_fntable[12] = reinterpret_cast<void*>(&fake_c_reload_from_disk);
    g_chaperone_setup005_fntable[13] = reinterpret_cast<void*>(&fake_c_get_pose34);
    g_chaperone_setup005_fntable[14] = reinterpret_cast<void*>(&fake_c_set_tags);
    g_chaperone_setup005_fntable[15] = reinterpret_cast<void*>(&fake_c_get_tags);
    g_chaperone_setup005_fntable[16] = reinterpret_cast<void*>(&fake_c_set_physical_bounds);
    g_chaperone_setup005_fntable[17] = reinterpret_cast<void*>(&fake_c_collision_bounds);
    g_chaperone_setup005_fntable[18] = reinterpret_cast<void*>(&fake_c_export_buffer);
    g_chaperone_setup005_fntable[19] = reinterpret_cast<void*>(&fake_c_import_buffer);

    for (size_t index = 0; index < kOverlaySlots; ++index) {
        g_overlay_vtable[index] = reinterpret_cast<void*>(&fake_overlay_error);
        g_overlay_fntable[index] = reinterpret_cast<void*>(&fake_c_overlay_error);
    }
    g_overlay_vtable[0] = reinterpret_cast<void*>(&fake_overlay_find);
    g_overlay_vtable[1] = reinterpret_cast<void*>(&fake_overlay_create);
    g_overlay_vtable[2] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay_vtable[3] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay_vtable[4] = reinterpret_cast<void*>(&fake_overlay_handle);
    g_overlay_vtable[5] = reinterpret_cast<void*>(&fake_overlay_string);
    g_overlay_vtable[6] = reinterpret_cast<void*>(&fake_overlay_string);
    g_overlay_vtable[9] = reinterpret_cast<void*>(&fake_overlay_error_name);
    g_overlay_vtable[11] = reinterpret_cast<void*>(&fake_overlay_pid);
    g_overlay_vtable[41] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay_vtable[42] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay_vtable[43] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay_vtable[45] = reinterpret_cast<void*>(&fake_overlay_poll);
    g_overlay_vtable[66] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay_vtable[67] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay_vtable[70] = reinterpret_cast<void*>(&fake_ret0);

    g_overlay_fntable[0] = reinterpret_cast<void*>(&fake_c_overlay_find);
    g_overlay_fntable[1] = reinterpret_cast<void*>(&fake_c_overlay_create);
    g_overlay_fntable[2] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay_fntable[3] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay_fntable[4] = reinterpret_cast<void*>(&fake_c_overlay_handle);
    g_overlay_fntable[5] = reinterpret_cast<void*>(&fake_c_overlay_string);
    g_overlay_fntable[6] = reinterpret_cast<void*>(&fake_c_overlay_string);
    g_overlay_fntable[9] = reinterpret_cast<void*>(&fake_c_overlay_error_name);
    g_overlay_fntable[11] = reinterpret_cast<void*>(&fake_c_overlay_pid);
    g_overlay_fntable[41] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay_fntable[42] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay_fntable[43] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay_fntable[45] = reinterpret_cast<void*>(&fake_c_overlay_poll);
    g_overlay_fntable[66] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay_fntable[67] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay_fntable[70] = reinterpret_cast<void*>(&fake_c_ret0);

    for (size_t index = 0; index < kLegacyOverlay013Slots; ++index) {
        g_overlay013_vtable[index] = reinterpret_cast<void*>(&fake_overlay_error);
        g_overlay013_fntable[index] = reinterpret_cast<void*>(&fake_c_overlay_error);
    }
    g_overlay013_vtable[0] = reinterpret_cast<void*>(&fake_overlay_find);
    g_overlay013_vtable[1] = reinterpret_cast<void*>(&fake_overlay_create);
    g_overlay013_vtable[2] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay013_vtable[3] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay013_vtable[4] = reinterpret_cast<void*>(&fake_overlay_handle);
    g_overlay013_vtable[5] = reinterpret_cast<void*>(&fake_overlay_string);
    g_overlay013_vtable[6] = reinterpret_cast<void*>(&fake_overlay_string);
    g_overlay013_vtable[8] = reinterpret_cast<void*>(&fake_overlay_error_name);
    g_overlay013_vtable[10] = reinterpret_cast<void*>(&fake_overlay_pid);
    g_overlay013_vtable[36] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay013_vtable[37] = reinterpret_cast<void*>(&fake_overlay_error);
    g_overlay013_vtable[38] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay013_vtable[40] = reinterpret_cast<void*>(&fake_overlay_poll);
    g_overlay013_vtable[48] = reinterpret_cast<void*>(&fake_overlay_handle);
    g_overlay013_vtable[60] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay013_vtable[61] = reinterpret_cast<void*>(&fake_overlay_visible);
    g_overlay013_vtable[64] = reinterpret_cast<void*>(&fake_noop_void);
    g_overlay013_vtable[65] = reinterpret_cast<void*>(&fake_ret0);
    g_overlay013_vtable[68] = reinterpret_cast<void*>(&fake_overlay_string);
    g_overlay013_vtable[69] = reinterpret_cast<void*>(&fake_noop_void);
    g_overlay013_vtable[70] = reinterpret_cast<void*>(&fake_noop_void);
    g_overlay013_vtable[71] = reinterpret_cast<void*>(&fake_noop_void);

    g_overlay013_fntable[0] = reinterpret_cast<void*>(&fake_c_overlay_find);
    g_overlay013_fntable[1] = reinterpret_cast<void*>(&fake_c_overlay_create);
    g_overlay013_fntable[2] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay013_fntable[3] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay013_fntable[4] = reinterpret_cast<void*>(&fake_c_overlay_handle);
    g_overlay013_fntable[5] = reinterpret_cast<void*>(&fake_c_overlay_string);
    g_overlay013_fntable[6] = reinterpret_cast<void*>(&fake_c_overlay_string);
    g_overlay013_fntable[8] = reinterpret_cast<void*>(&fake_c_overlay_error_name);
    g_overlay013_fntable[10] = reinterpret_cast<void*>(&fake_c_overlay_pid);
    g_overlay013_fntable[36] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay013_fntable[37] = reinterpret_cast<void*>(&fake_c_overlay_error);
    g_overlay013_fntable[38] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay013_fntable[40] = reinterpret_cast<void*>(&fake_c_overlay_poll);
    g_overlay013_fntable[48] = reinterpret_cast<void*>(&fake_c_overlay_handle);
    g_overlay013_fntable[60] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay013_fntable[61] = reinterpret_cast<void*>(&fake_c_overlay_visible);
    g_overlay013_fntable[64] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_overlay013_fntable[65] = reinterpret_cast<void*>(&fake_c_ret0);
    g_overlay013_fntable[68] = reinterpret_cast<void*>(&fake_c_overlay_string);
    g_overlay013_fntable[69] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_overlay013_fntable[70] = reinterpret_cast<void*>(&fake_c_noop_void);
    g_overlay013_fntable[71] = reinterpret_cast<void*>(&fake_c_noop_void);

    for (size_t index = 0; index < kRenderModelsSlots; ++index) {
        g_render_models_fntable[index] = reinterpret_cast<void*>(&fake_c_ret0);
    }
    g_render_models_fntable[0] = reinterpret_cast<void*>(&fake_c_render_models_load_model);
    g_render_models_fntable[1] = reinterpret_cast<void*>(&fake_c_render_models_free_model);
    g_render_models_fntable[2] = reinterpret_cast<void*>(&fake_c_render_models_load_texture);
    g_render_models_fntable[3] = reinterpret_cast<void*>(&fake_c_render_models_free_texture);
    g_render_models_fntable[4] = reinterpret_cast<void*>(&fake_c_render_models_load_texture_d3d11);
    g_render_models_fntable[5] = reinterpret_cast<void*>(&fake_c_render_models_load_into_texture_d3d11);
    g_render_models_fntable[6] = reinterpret_cast<void*>(&fake_c_render_models_free_texture_d3d11);
    g_render_models_fntable[7] = reinterpret_cast<void*>(&fake_c_render_models_get_model_name);
    g_render_models_fntable[8] = reinterpret_cast<void*>(&fake_c_render_models_get_model_count);
    g_render_models_fntable[9] = reinterpret_cast<void*>(&fake_c_render_models_get_component_count);
    g_render_models_fntable[10] = reinterpret_cast<void*>(&fake_c_render_models_get_component_name);
    g_render_models_fntable[11] = reinterpret_cast<void*>(&fake_c_render_models_get_component_button_mask);
    g_render_models_fntable[12] = reinterpret_cast<void*>(&fake_c_render_models_get_component_render_model_name);
    g_render_models_fntable[13] = reinterpret_cast<void*>(&fake_c_render_models_get_component_state_for_device_path);
    g_render_models_fntable[14] = reinterpret_cast<void*>(&fake_c_render_models_get_component_state);
    g_render_models_fntable[15] = reinterpret_cast<void*>(&fake_c_render_models_has_component);
    g_render_models_fntable[16] = reinterpret_cast<void*>(&fake_c_render_models_get_thumbnail_url);
    g_render_models_fntable[17] = reinterpret_cast<void*>(&fake_c_render_models_get_original_path);
    g_render_models_fntable[18] = reinterpret_cast<void*>(&fake_c_render_models_get_error_name);

    for (size_t index = 0; index < kScreenshotsSlots; ++index) {
        g_screenshots_fntable[index] = reinterpret_cast<void*>(&fake_c_screenshots_update_progress);
    }
    g_screenshots_fntable[0] = reinterpret_cast<void*>(&fake_c_screenshots_request);
    g_screenshots_fntable[1] = reinterpret_cast<void*>(&fake_c_screenshots_hook);
    g_screenshots_fntable[2] = reinterpret_cast<void*>(&fake_c_screenshots_get_type);
    g_screenshots_fntable[3] = reinterpret_cast<void*>(&fake_c_screenshots_get_filename);
    g_screenshots_fntable[4] = reinterpret_cast<void*>(&fake_c_screenshots_update_progress);
    g_screenshots_fntable[5] = reinterpret_cast<void*>(&fake_c_screenshots_take_stereo);
    g_screenshots_fntable[6] = reinterpret_cast<void*>(&fake_c_screenshots_submit);

    for (size_t index = 0; index < kApplicationsSlots; ++index) {
        g_applications_vtable[index] = reinterpret_cast<void*>(&fake_app_ok);
        g_applications_fntable[index] = reinterpret_cast<void*>(&fake_c_app_ok);
    }
    g_applications_vtable[0] = reinterpret_cast<void*>(&fake_app_ok_manifest);
    g_applications_vtable[1] = reinterpret_cast<void*>(&fake_app_ok_string);
    g_applications_vtable[2] = reinterpret_cast<void*>(&fake_app_true);
    g_applications_vtable[3] = reinterpret_cast<void*>(&fake_app_count);
    g_applications_vtable[4] = reinterpret_cast<void*>(&fake_get_app_key);
    g_applications_vtable[5] = reinterpret_cast<void*>(&fake_get_app_key);
    g_applications_vtable[6] = reinterpret_cast<void*>(&fake_app_ok_string);
    g_applications_vtable[7] = reinterpret_cast<void*>(&fake_app_ok_template);
    g_applications_vtable[8] = reinterpret_cast<void*>(&fake_app_ok_two_strings);
    g_applications_vtable[9] = reinterpret_cast<void*>(&fake_app_ok_string);
    g_applications_vtable[10] = reinterpret_cast<void*>(&fake_app_false);
    g_applications_vtable[11] = reinterpret_cast<void*>(&fake_app_ok_identify);
    g_applications_vtable[12] = reinterpret_cast<void*>(&fake_app_pid);
    g_applications_vtable[13] = reinterpret_cast<void*>(&fake_app_error_name);
    g_applications_vtable[14] = reinterpret_cast<void*>(&fake_get_app_property_string);
    g_applications_vtable[15] = reinterpret_cast<void*>(&fake_get_app_property_bool);
    g_applications_vtable[16] = reinterpret_cast<void*>(&fake_get_app_property_uint64);
    g_applications_vtable[17] = reinterpret_cast<void*>(&fake_app_ok_string_bool);
    g_applications_vtable[18] = reinterpret_cast<void*>(&fake_app_false);
    g_applications_vtable[19] = reinterpret_cast<void*>(&fake_app_ok_two_strings);
    g_applications_vtable[20] = reinterpret_cast<void*>(&fake_get_app_string_bool);
    g_applications_vtable[21] = reinterpret_cast<void*>(&fake_get_app_string_bool);
    g_applications_vtable[22] = reinterpret_cast<void*>(&fake_get_app_string_count);
    g_applications_vtable[23] = reinterpret_cast<void*>(&fake_get_app_launch_arguments);
    g_applications_vtable[24] = reinterpret_cast<void*>(&fake_get_starting_application);
    g_applications_vtable[25] = reinterpret_cast<void*>(&fake_scene_application_state);
    g_applications_vtable[26] = reinterpret_cast<void*>(&fake_perform_application_prelaunch_check);
    g_applications_vtable[27] = reinterpret_cast<void*>(&fake_scene_application_state_name);
    g_applications_vtable[28] = reinterpret_cast<void*>(&fake_app_ok_internal_process);
    g_applications_vtable[29] = reinterpret_cast<void*>(&fake_app_current_scene_pid);

    g_applications_fntable[0] = reinterpret_cast<void*>(&fake_c_app_ok_manifest);
    g_applications_fntable[1] = reinterpret_cast<void*>(&fake_c_app_ok_string);
    g_applications_fntable[2] = reinterpret_cast<void*>(&fake_c_app_true);
    g_applications_fntable[3] = reinterpret_cast<void*>(&fake_c_app_count);
    g_applications_fntable[4] = reinterpret_cast<void*>(&fake_c_get_app_key);
    g_applications_fntable[5] = reinterpret_cast<void*>(&fake_c_get_app_key);
    g_applications_fntable[6] = reinterpret_cast<void*>(&fake_c_app_ok_string);
    g_applications_fntable[7] = reinterpret_cast<void*>(&fake_c_app_ok_template);
    g_applications_fntable[8] = reinterpret_cast<void*>(&fake_c_app_ok_two_strings);
    g_applications_fntable[9] = reinterpret_cast<void*>(&fake_c_app_ok_string);
    g_applications_fntable[10] = reinterpret_cast<void*>(&fake_c_app_false);
    g_applications_fntable[11] = reinterpret_cast<void*>(&fake_c_app_ok_identify);
    g_applications_fntable[12] = reinterpret_cast<void*>(&fake_c_app_pid);
    g_applications_fntable[13] = reinterpret_cast<void*>(&fake_c_app_error_name);
    g_applications_fntable[14] = reinterpret_cast<void*>(&fake_c_get_app_property_string);
    g_applications_fntable[15] = reinterpret_cast<void*>(&fake_c_get_app_property_bool);
    g_applications_fntable[16] = reinterpret_cast<void*>(&fake_c_get_app_property_uint64);
    g_applications_fntable[17] = reinterpret_cast<void*>(&fake_c_app_ok_string_bool);
    g_applications_fntable[18] = reinterpret_cast<void*>(&fake_c_app_false);
    g_applications_fntable[19] = reinterpret_cast<void*>(&fake_c_app_ok_two_strings);
    g_applications_fntable[20] = reinterpret_cast<void*>(&fake_c_get_app_string_bool);
    g_applications_fntable[21] = reinterpret_cast<void*>(&fake_c_get_app_string_bool);
    g_applications_fntable[22] = reinterpret_cast<void*>(&fake_c_get_app_string_count);
    g_applications_fntable[23] = reinterpret_cast<void*>(&fake_c_get_app_launch_arguments);
    g_applications_fntable[24] = reinterpret_cast<void*>(&fake_c_get_starting_application);
    g_applications_fntable[25] = reinterpret_cast<void*>(&fake_c_scene_application_state);
    g_applications_fntable[26] = reinterpret_cast<void*>(&fake_c_perform_application_prelaunch_check);
    g_applications_fntable[27] = reinterpret_cast<void*>(&fake_c_scene_application_state_name);
    g_applications_fntable[28] = reinterpret_cast<void*>(&fake_c_app_ok_internal_process);
    g_applications_fntable[29] = reinterpret_cast<void*>(&fake_c_app_current_scene_pid);

    for (size_t index = 0; index < kLegacyApplications004Slots; ++index) {
        g_applications004_vtable[index] = reinterpret_cast<void*>(&fake_app_ok);
        g_applications004_fntable[index] = reinterpret_cast<void*>(&fake_c_app_ok);
    }
    g_applications004_vtable[0] = reinterpret_cast<void*>(&fake_app_ok_manifest);
    g_applications004_vtable[1] = reinterpret_cast<void*>(&fake_app_ok_string);
    g_applications004_vtable[2] = reinterpret_cast<void*>(&fake_app_true);
    g_applications004_vtable[3] = reinterpret_cast<void*>(&fake_app_count);
    g_applications004_vtable[4] = reinterpret_cast<void*>(&fake_get_app_key);
    g_applications004_vtable[5] = reinterpret_cast<void*>(&fake_get_app_key);
    g_applications004_vtable[6] = reinterpret_cast<void*>(&fake_app_ok_string);
    g_applications004_vtable[7] = reinterpret_cast<void*>(&fake_app_ok_string);
    g_applications004_vtable[8] = reinterpret_cast<void*>(&fake_app_false);
    g_applications004_vtable[9] = reinterpret_cast<void*>(&fake_app_ok_identify);
    g_applications004_vtable[10] = reinterpret_cast<void*>(&fake_app_pid);
    g_applications004_vtable[11] = reinterpret_cast<void*>(&fake_app_error_name);
    g_applications004_vtable[12] = reinterpret_cast<void*>(&fake_get_app_property_string);
    g_applications004_vtable[13] = reinterpret_cast<void*>(&fake_get_app_property_bool);
    g_applications004_vtable[14] = reinterpret_cast<void*>(&fake_get_app_property_uint64);
    g_applications004_vtable[15] = reinterpret_cast<void*>(&fake_app_ok_string_bool);
    g_applications004_vtable[16] = reinterpret_cast<void*>(&fake_app_false);
    g_applications004_vtable[17] = reinterpret_cast<void*>(&fake_get_starting_application);
    g_applications004_vtable[18] = reinterpret_cast<void*>(&fake_legacy_transition_state);
    g_applications004_vtable[19] = reinterpret_cast<void*>(&fake_app_ok_string);
    g_applications004_vtable[20] = reinterpret_cast<void*>(&fake_legacy_transition_state_name);
    g_applications004_vtable[21] = reinterpret_cast<void*>(&fake_false);
    g_applications004_vtable[22] = reinterpret_cast<void*>(&fake_app_ok_internal_process);

    g_applications004_fntable[0] = reinterpret_cast<void*>(&fake_c_app_ok_manifest);
    g_applications004_fntable[1] = reinterpret_cast<void*>(&fake_c_app_ok_string);
    g_applications004_fntable[2] = reinterpret_cast<void*>(&fake_c_app_true);
    g_applications004_fntable[3] = reinterpret_cast<void*>(&fake_c_app_count);
    g_applications004_fntable[4] = reinterpret_cast<void*>(&fake_c_get_app_key);
    g_applications004_fntable[5] = reinterpret_cast<void*>(&fake_c_get_app_key);
    g_applications004_fntable[6] = reinterpret_cast<void*>(&fake_c_app_ok_string);
    g_applications004_fntable[7] = reinterpret_cast<void*>(&fake_c_app_ok_string);
    g_applications004_fntable[8] = reinterpret_cast<void*>(&fake_c_app_false);
    g_applications004_fntable[9] = reinterpret_cast<void*>(&fake_c_app_ok_identify);
    g_applications004_fntable[10] = reinterpret_cast<void*>(&fake_c_app_pid);
    g_applications004_fntable[11] = reinterpret_cast<void*>(&fake_c_app_error_name);
    g_applications004_fntable[12] = reinterpret_cast<void*>(&fake_c_get_app_property_string);
    g_applications004_fntable[13] = reinterpret_cast<void*>(&fake_c_get_app_property_bool);
    g_applications004_fntable[14] = reinterpret_cast<void*>(&fake_c_get_app_property_uint64);
    g_applications004_fntable[15] = reinterpret_cast<void*>(&fake_c_app_ok_string_bool);
    g_applications004_fntable[16] = reinterpret_cast<void*>(&fake_c_app_false);
    g_applications004_fntable[17] = reinterpret_cast<void*>(&fake_c_get_starting_application);
    g_applications004_fntable[18] = reinterpret_cast<void*>(&fake_c_legacy_transition_state);
    g_applications004_fntable[19] = reinterpret_cast<void*>(&fake_c_app_ok_string);
    g_applications004_fntable[20] = reinterpret_cast<void*>(&fake_c_legacy_transition_state_name);
    g_applications004_fntable[21] = reinterpret_cast<void*>(&fake_c_false);
    g_applications004_fntable[22] = reinterpret_cast<void*>(&fake_c_app_ok_internal_process);

    for (size_t index = 0; index < kSettingsSlots; ++index) {
        g_settings_vtable[index] = reinterpret_cast<void*>(&fake_noop_void);
        g_settings_fntable[index] = reinterpret_cast<void*>(&fake_c_noop_void);
    }
    g_settings_vtable[0] = reinterpret_cast<void*>(&fake_settings_error_name);
    g_settings_vtable[1] = reinterpret_cast<void*>(&fake_settings_set_bool);
    g_settings_vtable[2] = reinterpret_cast<void*>(&fake_settings_set_int);
    g_settings_vtable[3] = reinterpret_cast<void*>(&fake_settings_set_float);
    g_settings_vtable[4] = reinterpret_cast<void*>(&fake_settings_set_string);
    g_settings_vtable[5] = reinterpret_cast<void*>(&fake_settings_get_bool);
    g_settings_vtable[6] = reinterpret_cast<void*>(&fake_settings_get_int);
    g_settings_vtable[7] = reinterpret_cast<void*>(&fake_settings_get_float);
    g_settings_vtable[8] = reinterpret_cast<void*>(&fake_settings_get_string);
    g_settings_vtable[9] = reinterpret_cast<void*>(&fake_settings_remove_section);
    g_settings_vtable[10] = reinterpret_cast<void*>(&fake_settings_remove_key);

    g_settings_fntable[0] = reinterpret_cast<void*>(&fake_c_settings_error_name);
    g_settings_fntable[1] = reinterpret_cast<void*>(&fake_c_settings_set_bool);
    g_settings_fntable[2] = reinterpret_cast<void*>(&fake_c_settings_set_int);
    g_settings_fntable[3] = reinterpret_cast<void*>(&fake_c_settings_set_float);
    g_settings_fntable[4] = reinterpret_cast<void*>(&fake_c_settings_set_string);
    g_settings_fntable[5] = reinterpret_cast<void*>(&fake_c_settings_get_bool);
    g_settings_fntable[6] = reinterpret_cast<void*>(&fake_c_settings_get_int);
    g_settings_fntable[7] = reinterpret_cast<void*>(&fake_c_settings_get_float);
    g_settings_fntable[8] = reinterpret_cast<void*>(&fake_c_settings_get_string);
    g_settings_fntable[9] = reinterpret_cast<void*>(&fake_c_settings_remove_section);
    g_settings_fntable[10] = reinterpret_cast<void*>(&fake_c_settings_remove_key);

    for (size_t index = 0; index < kLegacySettings001Slots; ++index) {
        g_settings001_vtable[index] = reinterpret_cast<void*>(&fake_noop_void);
        g_settings001_fntable[index] = reinterpret_cast<void*>(&fake_c_noop_void);
    }
    g_settings001_vtable[0] = reinterpret_cast<void*>(&fake_settings_error_name);
    g_settings001_vtable[1] = reinterpret_cast<void*>(&fake_legacy_settings_sync);
    g_settings001_vtable[2] = reinterpret_cast<void*>(&fake_legacy_settings_get_bool);
    g_settings001_vtable[3] = reinterpret_cast<void*>(&fake_settings_set_bool);
    g_settings001_vtable[4] = reinterpret_cast<void*>(&fake_legacy_settings_get_int);
    g_settings001_vtable[5] = reinterpret_cast<void*>(&fake_settings_set_int);
    g_settings001_vtable[6] = reinterpret_cast<void*>(&fake_legacy_settings_get_float);
    g_settings001_vtable[7] = reinterpret_cast<void*>(&fake_settings_set_float);
    g_settings001_vtable[8] = reinterpret_cast<void*>(&fake_legacy_settings_get_string);
    g_settings001_vtable[9] = reinterpret_cast<void*>(&fake_settings_set_string);
    g_settings001_vtable[10] = reinterpret_cast<void*>(&fake_settings_remove_section);
    g_settings001_vtable[11] = reinterpret_cast<void*>(&fake_settings_remove_key);

    g_settings001_fntable[0] = reinterpret_cast<void*>(&fake_c_settings_error_name);
    g_settings001_fntable[1] = reinterpret_cast<void*>(&fake_c_legacy_settings_sync);
    g_settings001_fntable[2] = reinterpret_cast<void*>(&fake_c_legacy_settings_get_bool);
    g_settings001_fntable[3] = reinterpret_cast<void*>(&fake_c_settings_set_bool);
    g_settings001_fntable[4] = reinterpret_cast<void*>(&fake_c_legacy_settings_get_int);
    g_settings001_fntable[5] = reinterpret_cast<void*>(&fake_c_settings_set_int);
    g_settings001_fntable[6] = reinterpret_cast<void*>(&fake_c_legacy_settings_get_float);
    g_settings001_fntable[7] = reinterpret_cast<void*>(&fake_c_settings_set_float);
    g_settings001_fntable[8] = reinterpret_cast<void*>(&fake_c_legacy_settings_get_string);
    g_settings001_fntable[9] = reinterpret_cast<void*>(&fake_c_settings_set_string);
    g_settings001_fntable[10] = reinterpret_cast<void*>(&fake_c_settings_remove_section);
    g_settings001_fntable[11] = reinterpret_cast<void*>(&fake_c_settings_remove_key);

    for (size_t index = 0; index < kLegacyInput005Slots; ++index) {
        g_input005_fntable[index] = reinterpret_cast<void*>(&fake_c_input_update_action_state);
    }
    g_input005_fntable[0] = reinterpret_cast<void*>(&fake_c_input_set_action_manifest_path);
    g_input005_fntable[1] = reinterpret_cast<void*>(&fake_c_input_get_action_set_handle);
    g_input005_fntable[2] = reinterpret_cast<void*>(&fake_c_input_get_action_handle);
    g_input005_fntable[3] = reinterpret_cast<void*>(&fake_c_input_get_source_handle);
    g_input005_fntable[4] = reinterpret_cast<void*>(&fake_c_input_update_action_state);
    g_input005_fntable[5] = reinterpret_cast<void*>(&fake_c_input_get_digital_action_data);
    g_input005_fntable[6] = reinterpret_cast<void*>(&fake_c_input_get_analog_action_data);
    g_input005_fntable[7] = reinterpret_cast<void*>(&fake_c_input_get_pose_action_data);
    g_input005_fntable[8] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_action_data);
    g_input005_fntable[9] = reinterpret_cast<void*>(&fake_c_input_get_bone_count);
    g_input005_fntable[10] = reinterpret_cast<void*>(&fake_c_input_get_bone_hierarchy);
    g_input005_fntable[11] = reinterpret_cast<void*>(&fake_c_input_get_bone_name);
    g_input005_fntable[12] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_reference_transforms);
    g_input005_fntable[13] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_tracking_level);
    g_input005_fntable[14] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_bone_data);
    g_input005_fntable[15] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_summary_data);
    g_input005_fntable[16] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_bone_data_compressed);
    g_input005_fntable[17] = reinterpret_cast<void*>(&fake_c_input_decompress_skeletal_bone_data);
    g_input005_fntable[18] = reinterpret_cast<void*>(&fake_c_input_trigger_haptic);
    g_input005_fntable[19] = reinterpret_cast<void*>(&fake_c_input_get_action_origins);
    g_input005_fntable[20] = reinterpret_cast<void*>(&fake_c_input_get_origin_localized_name);
    g_input005_fntable[21] = reinterpret_cast<void*>(&fake_c_input_get_origin_tracked_device_info);
    g_input005_fntable[22] = reinterpret_cast<void*>(&fake_c_input_show_action_origins);
    g_input005_fntable[23] = reinterpret_cast<void*>(&fake_c_input_show_bindings_for_action_set);
    g_input005_fntable[24] = reinterpret_cast<void*>(&fake_c_input_is_using_legacy_input);

    g_input006_vtable[0] = reinterpret_cast<void*>(&fake_input_set_action_manifest_path);
    g_input006_vtable[1] = reinterpret_cast<void*>(&fake_input_get_action_set_handle);
    g_input006_vtable[2] = reinterpret_cast<void*>(&fake_input_get_action_handle);
    g_input006_vtable[3] = reinterpret_cast<void*>(&fake_input_get_source_handle);
    g_input006_vtable[4] = reinterpret_cast<void*>(&fake_input_update_action_state);
    g_input006_vtable[5] = reinterpret_cast<void*>(&fake_input_get_digital_action_data);
    g_input006_vtable[6] = reinterpret_cast<void*>(&fake_input_get_analog_action_data);
    g_input006_vtable[7] = reinterpret_cast<void*>(&fake_input_get_pose_action_data);
    g_input006_vtable[8] = reinterpret_cast<void*>(&fake_input_get_pose_action_data_for_next_frame);
    g_input006_vtable[9] = reinterpret_cast<void*>(&fake_input_get_skeletal_action_data);
    g_input006_vtable[10] = reinterpret_cast<void*>(&fake_input_get_bone_count);
    g_input006_vtable[11] = reinterpret_cast<void*>(&fake_input_get_bone_hierarchy);
    g_input006_vtable[12] = reinterpret_cast<void*>(&fake_input_get_bone_name);
    g_input006_vtable[13] = reinterpret_cast<void*>(&fake_input_get_skeletal_reference_transforms);
    g_input006_vtable[14] = reinterpret_cast<void*>(&fake_input_get_skeletal_tracking_level);
    g_input006_vtable[15] = reinterpret_cast<void*>(&fake_input_get_skeletal_bone_data);
    g_input006_vtable[16] = reinterpret_cast<void*>(&fake_input_get_skeletal_summary_data006);
    g_input006_vtable[17] = reinterpret_cast<void*>(&fake_input_get_skeletal_bone_data_compressed);
    g_input006_vtable[18] = reinterpret_cast<void*>(&fake_input_decompress_skeletal_bone_data);
    g_input006_vtable[19] = reinterpret_cast<void*>(&fake_input_trigger_haptic);
    g_input006_vtable[20] = reinterpret_cast<void*>(&fake_input_get_action_origins);
    g_input006_vtable[21] = reinterpret_cast<void*>(&fake_input_get_origin_localized_name);
    g_input006_vtable[22] = reinterpret_cast<void*>(&fake_input_get_origin_tracked_device_info);
    g_input006_vtable[23] = reinterpret_cast<void*>(&fake_input_show_action_origins);
    g_input006_vtable[24] = reinterpret_cast<void*>(&fake_input_show_bindings_for_action_set);
    g_input006_vtable[25] = reinterpret_cast<void*>(&fake_input_is_using_legacy_input);

    g_input006_fntable[0] = reinterpret_cast<void*>(&fake_c_input_set_action_manifest_path);
    g_input006_fntable[1] = reinterpret_cast<void*>(&fake_c_input_get_action_set_handle);
    g_input006_fntable[2] = reinterpret_cast<void*>(&fake_c_input_get_action_handle);
    g_input006_fntable[3] = reinterpret_cast<void*>(&fake_c_input_get_source_handle);
    g_input006_fntable[4] = reinterpret_cast<void*>(&fake_c_input_update_action_state);
    g_input006_fntable[5] = reinterpret_cast<void*>(&fake_c_input_get_digital_action_data);
    g_input006_fntable[6] = reinterpret_cast<void*>(&fake_c_input_get_analog_action_data);
    g_input006_fntable[7] = reinterpret_cast<void*>(&fake_c_input_get_pose_action_data);
    g_input006_fntable[8] = reinterpret_cast<void*>(&fake_c_input_get_pose_action_data_for_next_frame);
    g_input006_fntable[9] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_action_data);
    g_input006_fntable[10] = reinterpret_cast<void*>(&fake_c_input_get_bone_count);
    g_input006_fntable[11] = reinterpret_cast<void*>(&fake_c_input_get_bone_hierarchy);
    g_input006_fntable[12] = reinterpret_cast<void*>(&fake_c_input_get_bone_name);
    g_input006_fntable[13] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_reference_transforms);
    g_input006_fntable[14] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_tracking_level);
    g_input006_fntable[15] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_bone_data);
    g_input006_fntable[16] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_summary_data006);
    g_input006_fntable[17] = reinterpret_cast<void*>(&fake_c_input_get_skeletal_bone_data_compressed);
    g_input006_fntable[18] = reinterpret_cast<void*>(&fake_c_input_decompress_skeletal_bone_data);
    g_input006_fntable[19] = reinterpret_cast<void*>(&fake_c_input_trigger_haptic);
    g_input006_fntable[20] = reinterpret_cast<void*>(&fake_c_input_get_action_origins);
    g_input006_fntable[21] = reinterpret_cast<void*>(&fake_c_input_get_origin_localized_name);
    g_input006_fntable[22] = reinterpret_cast<void*>(&fake_c_input_get_origin_tracked_device_info);
    g_input006_fntable[23] = reinterpret_cast<void*>(&fake_c_input_show_action_origins);
    g_input006_fntable[24] = reinterpret_cast<void*>(&fake_c_input_show_bindings_for_action_set);
    g_input006_fntable[25] = reinterpret_cast<void*>(&fake_c_input_is_using_legacy_input);

    g_tables_initialized = true;
}

bool is_known_interface(const char* interface_version) {
    if (!interface_version) {
        return false;
    }
    if (is_system_interface(interface_version) || is_compositor_interface(interface_version)
        || is_chaperone_interface(interface_version) || is_chaperone_setup_interface(interface_version)
        || is_overlay_interface(interface_version) || is_applications_interface(interface_version)
        || is_settings_interface(interface_version)
        || is_legacy_input006_interface(interface_version)) {
        return true;
    }
    if (std::strncmp(interface_version, kFnTablePrefix, std::strlen(kFnTablePrefix)) != 0) {
        return false;
    }
    const char* version = interface_version + std::strlen(kFnTablePrefix);
    return is_system_interface(version) || is_compositor_interface(version) || is_chaperone_interface(version)
        || is_chaperone_setup_interface(version) || is_overlay_interface(version)
        || is_render_models_interface(version)
        || is_screenshots_interface(version)
        || is_applications_interface(version)
        || is_settings_interface(version)
        || is_legacy_input005_interface(version)
        || is_legacy_input006_interface(version);
}

} // namespace

extern "C" __declspec(dllexport) uint32_t VR_InitInternal(
    vr::EVRInitError* error,
    vr::EVRApplicationType application_type
) {
    ensure_tables_initialized();
    activate_pacer();
    if (error) {
        *error = vr::VRInitError_None;
    }
    log_line("fake VR_InitInternal");
    log_call_u32("VR_InitInternal application_type", static_cast<uint32_t>(application_type));
    return 1;
}

extern "C" __declspec(dllexport) uint32_t VR_InitInternal2(
    vr::EVRInitError* error,
    vr::EVRApplicationType application_type,
    const char*
) {
    return VR_InitInternal(error, application_type);
}

extern "C" __declspec(dllexport) void VR_ShutdownInternal() {
    deactivate_pacer();
    log_line("fake VR_ShutdownInternal");
    flush_log();
}
extern "C" __declspec(dllexport) bool VR_IsHmdPresent() { return true; }
extern "C" __declspec(dllexport) bool VR_IsRuntimeInstalled() { return true; }

extern "C" __declspec(dllexport) bool VR_GetRuntimePath(
    char* path_buffer,
    uint32_t buffer_size,
    uint32_t* required_size
) {
    const char* path = "C:\\FakeOpenVR";
    if (required_size) {
        *required_size = static_cast<uint32_t>(std::strlen(path) + 1);
    }
    if (!path_buffer || buffer_size <= std::strlen(path)) {
        return false;
    }
    std::strcpy(path_buffer, path);
    return true;
}

extern "C" __declspec(dllexport) const char* VR_RuntimePath() { return "C:\\FakeOpenVR"; }

extern "C" __declspec(dllexport) void* VR_GetGenericInterface(
    const char* interface_version,
    vr::EVRInitError* error
) {
    ensure_tables_initialized();
    if (error) {
        *error = vr::VRInitError_None;
    }
    if (is_legacy_system011_or_012_interface(interface_version)) {
        log_interface("IVRSystem", interface_version);
        return &g_system011;
    }
    if (is_legacy_system019_interface(interface_version)) {
        log_interface("IVRSystem", interface_version);
        return &g_system019;
    }
    if (is_system_interface(interface_version)) {
        log_interface("IVRSystem", interface_version);
        return &g_system;
    }
    if (is_legacy_compositor013_interface(interface_version)) {
        log_interface("IVRCompositor", interface_version);
        return &g_compositor013;
    }
    if (is_legacy_compositor014_interface(interface_version)) {
        log_interface("IVRCompositor", interface_version);
        return &g_compositor014;
    }
    if (is_legacy_compositor016_interface(interface_version)) {
        log_interface("IVRCompositor", interface_version);
        return &g_compositor016;
    }
    if (is_compositor_interface(interface_version)) {
        log_interface("IVRCompositor", interface_version);
        return &g_compositor;
    }
    if (is_chaperone_interface(interface_version)) {
        log_interface("IVRChaperone", interface_version);
        return &g_chaperone;
    }
    if (is_legacy_chaperone_setup005_interface(interface_version)) {
        log_interface("IVRChaperoneSetup", interface_version);
        return &g_chaperone_setup005;
    }
    if (is_chaperone_setup_interface(interface_version)) {
        log_interface("IVRChaperoneSetup", interface_version);
        return &g_chaperone_setup;
    }
    if (is_legacy_overlay013_interface(interface_version)) {
        log_interface("IVROverlay", interface_version);
        return &g_overlay013;
    }
    if (is_overlay_interface(interface_version)) {
        log_interface("IVROverlay", interface_version);
        return &g_overlay;
    }
    if (is_legacy_applications004_or_005_interface(interface_version)) {
        log_interface("IVRApplications", interface_version);
        return &g_applications004;
    }
    if (is_applications_interface(interface_version)) {
        log_interface("IVRApplications", interface_version);
        return &g_applications;
    }
    if (is_legacy_settings001_interface(interface_version)) {
        log_interface("IVRSettings", interface_version);
        return &g_settings001;
    }
    if (is_settings_interface(interface_version)) {
        log_interface("IVRSettings", interface_version);
        return &g_settings;
    }
    if (is_legacy_input006_interface(interface_version)) {
        log_interface("IVRInput", interface_version);
        return &g_input006;
    }
    if (interface_version
        && std::strncmp(interface_version, kFnTablePrefix, std::strlen(kFnTablePrefix)) == 0) {
        const char* version = interface_version + std::strlen(kFnTablePrefix);
        if (is_legacy_system011_or_012_interface(version)) {
            log_interface("FnTable IVRSystem", interface_version);
            return g_system011_fntable;
        }
        if (is_legacy_system019_interface(version)) {
            log_interface("FnTable IVRSystem", interface_version);
            return g_system019_fntable;
        }
        if (is_system_interface(version)) {
            log_interface("FnTable IVRSystem", interface_version);
            return g_system_fntable;
        }
        if (is_legacy_compositor013_interface(version)) {
            log_interface("FnTable IVRCompositor", interface_version);
            return g_compositor013_fntable;
        }
        if (is_legacy_compositor014_interface(version)) {
            log_interface("FnTable IVRCompositor", interface_version);
            return g_compositor014_fntable;
        }
        if (is_legacy_compositor016_interface(version)) {
            log_interface("FnTable IVRCompositor", interface_version);
            return g_compositor016_fntable;
        }
        if (is_compositor_interface(version)) {
            log_interface("FnTable IVRCompositor", interface_version);
            return g_compositor_fntable;
        }
        if (is_chaperone_interface(version)) {
            log_interface("FnTable IVRChaperone", interface_version);
            return g_chaperone_fntable;
        }
        if (is_legacy_chaperone_setup005_interface(version)) {
            log_interface("FnTable IVRChaperoneSetup", interface_version);
            return g_chaperone_setup005_fntable;
        }
        if (is_chaperone_setup_interface(version)) {
            log_interface("FnTable IVRChaperoneSetup", interface_version);
            return g_chaperone_setup_fntable;
        }
        if (is_legacy_overlay013_interface(version)) {
            log_interface("FnTable IVROverlay", interface_version);
            return g_overlay013_fntable;
        }
        if (is_overlay_interface(version)) {
            log_interface("FnTable IVROverlay", interface_version);
            return g_overlay_fntable;
        }
        if (is_render_models_interface(version)) {
            log_interface("FnTable IVRRenderModels", interface_version);
            return g_render_models_fntable;
        }
        if (is_screenshots_interface(version)) {
            log_interface("FnTable IVRScreenshots", interface_version);
            return g_screenshots_fntable;
        }
        if (is_legacy_applications004_or_005_interface(version)) {
            log_interface("FnTable IVRApplications", interface_version);
            return g_applications004_fntable;
        }
        if (is_applications_interface(version)) {
            log_interface("FnTable IVRApplications", interface_version);
            return g_applications_fntable;
        }
        if (is_legacy_settings001_interface(version)) {
            log_interface("FnTable IVRSettings", interface_version);
            return g_settings001_fntable;
        }
        if (is_settings_interface(version)) {
            log_interface("FnTable IVRSettings", interface_version);
            return g_settings_fntable;
        }
        if (is_legacy_input005_interface(version)) {
            log_interface("FnTable IVRInput", interface_version);
            return g_input005_fntable;
        }
        if (is_legacy_input006_interface(version)) {
            log_interface("FnTable IVRInput", interface_version);
            return g_input006_fntable;
        }
    }
    if (error) {
        *error = vr::VRInitError_Init_InterfaceNotFound;
    }
    char message[256] = {};
    std::snprintf(
        message,
        sizeof(message),
        "fake VR_GetGenericInterface unknown %s",
        interface_version ? interface_version : "<null>"
    );
    log_line(message);
    return nullptr;
}

extern "C" __declspec(dllexport) bool VR_IsInterfaceVersionValid(const char* interface_version) {
    bool valid = is_known_interface(interface_version);
    char message[320] = {};
    std::snprintf(
        message,
        sizeof(message),
        "fake VR_IsInterfaceVersionValid %s -> %s",
        interface_version ? interface_version : "<null>",
        valid ? "true" : "false"
    );
    log_line(message);
    return valid;
}

extern "C" __declspec(dllexport) uint32_t VR_GetInitToken() { return 1; }
extern "C" __declspec(dllexport) const char* VR_GetVRInitErrorAsSymbol(vr::EVRInitError error) {
    return error == vr::VRInitError_None ? "None" : "InterfaceNotFound";
}
extern "C" __declspec(dllexport) const char* VR_GetVRInitErrorAsEnglishDescription(vr::EVRInitError error) {
    return error == vr::VRInitError_None ? "None" : "Interface not found";
}
extern "C" __declspec(dllexport) const char* VR_GetStringForHmdError(vr::EVRInitError error) {
    return VR_GetVRInitErrorAsEnglishDescription(error);
}
