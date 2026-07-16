#include <stdarg.h>
#include <string.h>

#include "ntstatus.h"
#define WIN32_NO_STATUS
#include "windef.h"
#include "winbase.h"

#include "unixlib.h"

struct alvr_iosurface_import_result
{
    UINT32 verification_status;
    UINT32 surface_id;
    UINT32 width;
    UINT32 height;
    UINT32 bytes_per_row;
    UINT32 pixel_format;
    UINT32 producer_pid;
    UINT32 client_pid;
    BYTE expected_bgra[4];
    BYTE actual_bgra[4];
};

struct alvr_iosurface_bind_result
{
    UINT64 frame_id;
    INT32 vk_result;
    UINT32 verification_status;
    UINT32 slot_index;
    UINT32 generation;
    UINT32 surface_id;
    UINT32 width;
    UINT32 height;
    UINT32 bytes_per_row;
    UINT32 pixel_format;
    UINT32 producer_pid;
    BYTE expected_bgra[4];
};

struct alvr_iosurface_frame
{
    UINT64 frame_id;
    UINT64 video_timestamp_ns;
    UINT32 slot_index;
    UINT32 generation;
    UINT32 flags;
    UINT32 surface_id;
    UINT32 width;
    UINT32 height;
    UINT32 sample_x;
    UINT32 sample_y;
    BYTE expected_bgra[4];
    UINT64 pose_timestamp_ns;
    UINT64 pose_generation;
    float pose[3][4];
};

struct alvr_iosurface_release_result
{
    UINT32 status;
    UINT32 consumer_pid;
    BYTE actual_bgra[4];
};

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, void *reserved)
{
    (void)reserved;

    if (reason == DLL_PROCESS_ATTACH)
    {
        DisableThreadLibraryCalls(instance);
        return __wine_init_unix_call() == STATUS_SUCCESS;
    }

    return TRUE;
}

NTSTATUS WINAPI alvr_iosurface_scalar(UINT64 input, UINT64 *output)
{
    struct scalar_params params = {input, 0};
    NTSTATUS status;

    if (!output) return STATUS_INVALID_PARAMETER;

    status = WINE_UNIX_CALL(unix_call_scalar, &params);
    if (status == STATUS_SUCCESS) *output = params.output;
    return status;
}

NTSTATUS WINAPI alvr_iosurface_attach(UINT64 image,
                                      INT32 *vk_result,
                                      UINT32 *surface_id,
                                      UINT32 *mach_port)
{
    struct attach_params params = {image, 0, 0, 0, 0};
    NTSTATUS status;

    if (!image || !vk_result || !surface_id || !mach_port)
        return STATUS_INVALID_PARAMETER;

    status = WINE_UNIX_CALL(unix_call_attach, &params);
    *vk_result = params.vk_result;
    if (status == STATUS_SUCCESS)
    {
        *surface_id = params.surface_id;
        *mach_port = params.mach_port;
    }
    return status;
}

NTSTATUS WINAPI alvr_iosurface_release_port(UINT32 mach_port)
{
    struct release_port_params params = {mach_port};

    if (!params.mach_port) return STATUS_INVALID_PARAMETER;
    return WINE_UNIX_CALL(unix_call_release_port, &params);
}

NTSTATUS WINAPI alvr_iosurface_import_probe(
    const char *service_name,
    UINT64 session_nonce,
    struct alvr_iosurface_import_result *result)
{
    struct import_probe_params params = {0};
    SIZE_T service_name_length;
    NTSTATUS status;

    if (!service_name || !session_nonce || !result)
        return STATUS_INVALID_PARAMETER;
    service_name_length = lstrlenA(service_name);
    if (!service_name_length ||
        service_name_length >= ALVR_IOSURFACE_SERVICE_NAME_CAPACITY)
        return STATUS_INVALID_PARAMETER;

    params.session_nonce = session_nonce;
    memcpy(params.service_name, service_name, service_name_length + 1);
    status = WINE_UNIX_CALL(unix_call_import_probe, &params);

    result->verification_status = params.verification_status;
    result->surface_id = params.surface_id;
    result->width = params.width;
    result->height = params.height;
    result->bytes_per_row = params.bytes_per_row;
    result->pixel_format = params.pixel_format;
    result->producer_pid = params.producer_pid;
    result->client_pid = params.client_pid;
    memcpy(result->expected_bgra,
           params.expected_bgra,
           sizeof(result->expected_bgra));
    memcpy(result->actual_bgra,
           params.actual_bgra,
           sizeof(result->actual_bgra));
    return status;
}

NTSTATUS WINAPI alvr_iosurface_import_bind(
    UINT64 image,
    const char *service_name,
    UINT64 session_nonce,
    struct alvr_iosurface_bind_result *result)
{
    struct import_bind_params params = {0};
    SIZE_T service_name_length;
    NTSTATUS status;

    if (!image || !service_name || !session_nonce || !result)
        return STATUS_INVALID_PARAMETER;
    service_name_length = lstrlenA(service_name);
    if (!service_name_length ||
        service_name_length >= ALVR_IOSURFACE_SERVICE_NAME_CAPACITY)
        return STATUS_INVALID_PARAMETER;

    params.image = image;
    params.session_nonce = session_nonce;
    memcpy(params.service_name, service_name, service_name_length + 1);
    status = WINE_UNIX_CALL(unix_call_import_bind, &params);

    result->frame_id = params.frame_id;
    result->vk_result = params.vk_result;
    result->verification_status = params.verification_status;
    result->slot_index = params.slot_index;
    result->generation = params.generation;
    result->surface_id = params.surface_id;
    result->width = params.width;
    result->height = params.height;
    result->bytes_per_row = params.bytes_per_row;
    result->pixel_format = params.pixel_format;
    result->producer_pid = params.producer_pid;
    memcpy(result->expected_bgra,
           params.expected_bgra,
           sizeof(result->expected_bgra));
    return status;
}

NTSTATUS WINAPI alvr_iosurface_signal_ready(
    const char *service_name,
    UINT64 session_nonce,
    const struct alvr_iosurface_bind_result *result,
    UINT32 verification_status)
{
    struct signal_ready_params params = {0};
    SIZE_T service_name_length;

    if (!service_name || !session_nonce || !result || !result->frame_id)
        return STATUS_INVALID_PARAMETER;
    service_name_length = lstrlenA(service_name);
    if (!service_name_length ||
        service_name_length >= ALVR_IOSURFACE_SERVICE_NAME_CAPACITY)
        return STATUS_INVALID_PARAMETER;

    params.session_nonce = session_nonce;
    params.frame_id = result->frame_id;
    params.slot_index = result->slot_index;
    params.generation = result->generation;
    params.verification_status = verification_status;
    params.surface_id = result->surface_id;
    memcpy(params.actual_bgra,
           result->expected_bgra,
           sizeof(params.actual_bgra));
    memcpy(params.service_name, service_name, service_name_length + 1);
    return WINE_UNIX_CALL(unix_call_signal_ready, &params);
}

NTSTATUS WINAPI alvr_iosurface_frame_ready(
    const char *service_name,
    UINT64 session_nonce,
    const struct alvr_iosurface_frame *frame,
    struct alvr_iosurface_release_result *release)
{
    struct frame_ready_params params = {0};
    SIZE_T service_name_length;
    NTSTATUS status;

    if (!service_name || !session_nonce || !frame || !release ||
        !frame->frame_id)
        return STATUS_INVALID_PARAMETER;
    service_name_length = lstrlenA(service_name);
    if (!service_name_length ||
        service_name_length >= ALVR_IOSURFACE_SERVICE_NAME_CAPACITY)
        return STATUS_INVALID_PARAMETER;

    params.session_nonce = session_nonce;
    params.frame_id = frame->frame_id;
    params.video_timestamp_ns = frame->video_timestamp_ns;
    params.slot_index = frame->slot_index;
    params.generation = frame->generation;
    params.flags = frame->flags;
    params.surface_id = frame->surface_id;
    params.width = frame->width;
    params.height = frame->height;
    params.sample_x = frame->sample_x;
    params.sample_y = frame->sample_y;
    memcpy(params.expected_bgra,
           frame->expected_bgra,
           sizeof(params.expected_bgra));
    params.pose_timestamp_ns = frame->pose_timestamp_ns;
    params.pose_generation = frame->pose_generation;
    memcpy(params.pose, frame->pose, sizeof(params.pose));
    memcpy(params.service_name, service_name, service_name_length + 1);
    status = WINE_UNIX_CALL(unix_call_frame_ready, &params);

    release->status = params.release_status;
    release->consumer_pid = params.consumer_pid;
    memcpy(release->actual_bgra,
           params.actual_bgra,
           sizeof(release->actual_bgra));
    return status;
}

C_ASSERT(sizeof(struct scalar_params) == 16);
C_ASSERT(sizeof(struct attach_params) == 24);
C_ASSERT(sizeof(struct release_port_params) == 4);
C_ASSERT(sizeof(struct import_probe_params) == 176);
C_ASSERT(sizeof(struct alvr_iosurface_import_result) == 40);
C_ASSERT(sizeof(struct import_bind_params) == 200);
C_ASSERT(sizeof(struct signal_ready_params) == 168);
C_ASSERT(sizeof(struct alvr_iosurface_bind_result) == 56);
C_ASSERT(sizeof(struct frame_ready_params) == 264);
C_ASSERT(sizeof(struct alvr_iosurface_frame) == 120);
C_ASSERT(sizeof(struct alvr_iosurface_release_result) == 12);
