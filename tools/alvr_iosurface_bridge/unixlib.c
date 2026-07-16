#if 0
#pragma makedep unix
#endif

#include <bootstrap.h>
#include <dlfcn.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <CoreFoundation/CoreFoundation.h>
#include <mach/mach.h>
#include <IOSurface/IOSurface.h>

#include "ntstatus.h"
#define WIN32_NO_STATUS
#include "unixlib.h"

#include "iosurface_handoff_protocol.h"

enum
{
    import_probe_timeout_ms = 5000,
    frame_release_timeout_ms = 30000
};

struct request_message
{
    mach_msg_header_t header;
    struct alvr_iosurface_request payload;
};

struct offer_message
{
    mach_msg_header_t header;
    mach_msg_body_t body;
    mach_msg_port_descriptor_t surface_port;
    struct alvr_iosurface_offer payload;
};

struct ack_message
{
    mach_msg_header_t header;
    struct alvr_iosurface_ack payload;
};

struct frame_ready_message
{
    mach_msg_header_t header;
    struct alvr_iosurface_frame_ready payload;
};

struct slot_release_message
{
    mach_msg_header_t header;
    struct alvr_iosurface_slot_release payload;
};

union receive_message
{
    struct offer_message offer;
    BYTE bytes[sizeof(struct offer_message) + MAX_TRAILER_SIZE];
};

union release_receive_message
{
    struct slot_release_message release;
    BYTE bytes[sizeof(struct slot_release_message) + MAX_TRAILER_SIZE];
};

typedef INT32 (*vk_use_iosurface_mvk_fn)(void *image, IOSurfaceRef surface);
typedef void (*vk_get_iosurface_mvk_fn)(void *image, IOSurfaceRef *surface);

static vk_use_iosurface_mvk_fn use_iosurface;
static vk_get_iosurface_mvk_fn get_iosurface;

static void resolve_moltenvk_functions(void)
{
    void *library = RTLD_DEFAULT;
    const char *library_path;

    use_iosurface = (vk_use_iosurface_mvk_fn)dlsym(
        library, "vkUseIOSurfaceMVK");
    get_iosurface = (vk_get_iosurface_mvk_fn)dlsym(
        library, "vkGetIOSurfaceMVK");
    if (use_iosurface && get_iosurface) return;

    library_path = getenv("ALVR_MOLTENVK_PATH");
    if (!library_path || !*library_path) return;

    library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (!library) return;

    use_iosurface = (vk_use_iosurface_mvk_fn)dlsym(
        library, "vkUseIOSurfaceMVK");
    get_iosurface = (vk_get_iosurface_mvk_fn)dlsym(
        library, "vkGetIOSurfaceMVK");
}

static NTSTATUS scalar_call(void *opaque)
{
    struct scalar_params *params = opaque;

    params->output = params->input ^ UINT64_C(0x9e3779b97f4a7c15);
    return STATUS_SUCCESS;
}

static NTSTATUS attach_call(void *opaque)
{
    struct attach_params *params = opaque;
    IOSurfaceRef surface = NULL;
    mach_port_t port;

    if (!params->image) return STATUS_INVALID_PARAMETER;

    resolve_moltenvk_functions();
    if (!use_iosurface || !get_iosurface)
        return STATUS_ENTRYPOINT_NOT_FOUND;

    params->vk_result = use_iosurface(
        (void *)(uintptr_t)params->image, NULL);
    if (params->vk_result != 0) return STATUS_UNSUCCESSFUL;

    get_iosurface((void *)(uintptr_t)params->image, &surface);
    if (!surface) return STATUS_UNSUCCESSFUL;

    params->surface_id = IOSurfaceGetID(surface);
    port = IOSurfaceCreateMachPort(surface);
    if (!params->surface_id || port == MACH_PORT_NULL)
    {
        if (port != MACH_PORT_NULL)
            mach_port_deallocate(mach_task_self(), port);
        return STATUS_UNSUCCESSFUL;
    }

    params->mach_port = (UINT32)port;
    return STATUS_SUCCESS;
}

static NTSTATUS release_port_call(void *opaque)
{
    const struct release_port_params *params = opaque;

    if (!params->mach_port) return STATUS_INVALID_PARAMETER;
    if (mach_port_deallocate(mach_task_self(), params->mach_port) != KERN_SUCCESS)
        return STATUS_INVALID_HANDLE;
    return STATUS_SUCCESS;
}

static void destroy_receive_port(mach_port_t *port)
{
    if (*port == MACH_PORT_NULL) return;
    mach_port_mod_refs(
        mach_task_self(), *port, MACH_PORT_RIGHT_RECEIVE, -1);
    *port = MACH_PORT_NULL;
}

static void deallocate_send_port(mach_port_t *port)
{
    if (*port == MACH_PORT_NULL) return;
    mach_port_deallocate(mach_task_self(), *port);
    *port = MACH_PORT_NULL;
}

static kern_return_t send_import_request(mach_port_t server_port,
                                         mach_port_t reply_port,
                                         UINT64 session_nonce)
{
    struct request_message message = {0};

    message.header.msgh_bits = MACH_MSGH_BITS(
        MACH_MSG_TYPE_COPY_SEND, MACH_MSG_TYPE_MAKE_SEND_ONCE);
    message.header.msgh_size = sizeof(message);
    message.header.msgh_remote_port = server_port;
    message.header.msgh_local_port = reply_port;
    message.header.msgh_id = ALVR_IOSURFACE_MESSAGE_REQUEST;
    message.payload.session_nonce = session_nonce;
    message.payload.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
    message.payload.client_pid = getpid();

    return mach_msg(&message.header,
                    MACH_SEND_MSG | MACH_SEND_TIMEOUT,
                    message.header.msgh_size,
                    0,
                    MACH_PORT_NULL,
                    import_probe_timeout_ms,
                    MACH_PORT_NULL);
}

static kern_return_t receive_import_offer(mach_port_t reply_port,
                                          union receive_message *message)
{
    memset(message, 0, sizeof(*message));
    return mach_msg(&message->offer.header,
                    MACH_RCV_MSG | MACH_RCV_TIMEOUT,
                    0,
                    sizeof(*message),
                    reply_port,
                    import_probe_timeout_ms,
                    MACH_PORT_NULL);
}

static kern_return_t send_import_ack(
    mach_port_t server_port,
    const struct alvr_iosurface_ack *ack)
{
    struct ack_message message = {0};

    message.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
    message.header.msgh_size = sizeof(message);
    message.header.msgh_remote_port = server_port;
    message.header.msgh_id = ALVR_IOSURFACE_MESSAGE_ACK;
    message.payload = *ack;

    return mach_msg(&message.header,
                    MACH_SEND_MSG | MACH_SEND_TIMEOUT,
                    message.header.msgh_size,
                    0,
                    MACH_PORT_NULL,
                    import_probe_timeout_ms,
                    MACH_PORT_NULL);
}

static UINT32 verify_imported_surface(
    IOSurfaceRef surface,
    const struct alvr_iosurface_offer *offer,
    BYTE actual_bgra[4])
{
    const BYTE *base;
    size_t bytes_per_row;

    if (IOSurfaceGetID(surface) != offer->surface_id ||
        IOSurfaceGetWidth(surface) != offer->width ||
        IOSurfaceGetHeight(surface) != offer->height ||
        IOSurfaceGetBytesPerRow(surface) != offer->bytes_per_row ||
        IOSurfaceGetPixelFormat(surface) != offer->pixel_format ||
        offer->sample_x >= offer->width || offer->sample_y >= offer->height)
        return ALVR_IOSURFACE_PROBE_METADATA_MISMATCH;

    if (IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL) != kIOReturnSuccess)
        return ALVR_IOSURFACE_PROBE_LOCK_FAILED;
    base = IOSurfaceGetBaseAddress(surface);
    bytes_per_row = IOSurfaceGetBytesPerRow(surface);
    if (!base)
    {
        IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
        return ALVR_IOSURFACE_PROBE_LOCK_FAILED;
    }

    memcpy(actual_bgra,
           base + offer->sample_y * bytes_per_row + offer->sample_x * 4,
           4);
    IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
    if (memcmp(actual_bgra, offer->expected_bgra, 4) != 0)
        return ALVR_IOSURFACE_PROBE_PIXEL_MISMATCH;
    return ALVR_IOSURFACE_PROBE_PASS;
}

static NTSTATUS import_probe_call(void *opaque)
{
    struct import_probe_params *params = opaque;
    mach_port_t server_port = MACH_PORT_NULL;
    mach_port_t reply_port = MACH_PORT_NULL;
    mach_port_t surface_port = MACH_PORT_NULL;
    IOSurfaceRef surface = NULL;
    union receive_message received;
    struct alvr_iosurface_ack ack = {0};
    kern_return_t mach_result;
    NTSTATUS status = STATUS_UNSUCCESSFUL;

    params->verification_status = ALVR_IOSURFACE_PROBE_PROTOCOL_MISMATCH;
    if (!params->session_nonce || !params->service_name[0] ||
        !memchr(params->service_name,
                '\0',
                ALVR_IOSURFACE_SERVICE_NAME_CAPACITY))
        return STATUS_INVALID_PARAMETER;

    mach_result = bootstrap_look_up(
        bootstrap_port, params->service_name, &server_port);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    mach_result = mach_port_allocate(
        mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &reply_port);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    mach_result = send_import_request(
        server_port, reply_port, params->session_nonce);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    mach_result = receive_import_offer(reply_port, &received);
    if (mach_result != KERN_SUCCESS) goto cleanup;

    if (received.offer.header.msgh_id != ALVR_IOSURFACE_MESSAGE_OFFER ||
        !(received.offer.header.msgh_bits & MACH_MSGH_BITS_COMPLEX) ||
        received.offer.body.msgh_descriptor_count != 1 ||
        received.offer.surface_port.type != MACH_MSG_PORT_DESCRIPTOR ||
        received.offer.surface_port.name == MACH_PORT_NULL ||
        received.offer.payload.session_nonce != params->session_nonce ||
        received.offer.payload.protocol_version !=
            ALVR_IOSURFACE_PROTOCOL_VERSION)
    {
        mach_msg_destroy(&received.offer.header);
        goto cleanup;
    }

    surface_port = received.offer.surface_port.name;
    received.offer.surface_port.name = MACH_PORT_NULL;
    surface = IOSurfaceLookupFromMachPort(surface_port);
    deallocate_send_port(&surface_port);

    params->surface_id = received.offer.payload.surface_id;
    params->width = received.offer.payload.width;
    params->height = received.offer.payload.height;
    params->bytes_per_row = received.offer.payload.bytes_per_row;
    params->pixel_format = received.offer.payload.pixel_format;
    params->producer_pid = received.offer.payload.producer_pid;
    params->client_pid = getpid();
    memcpy(params->expected_bgra,
           received.offer.payload.expected_bgra,
           sizeof(params->expected_bgra));

    if (!surface)
        params->verification_status = ALVR_IOSURFACE_PROBE_LOOKUP_FAILED;
    else
        params->verification_status = verify_imported_surface(
            surface, &received.offer.payload, params->actual_bgra);

    ack.session_nonce = params->session_nonce;
    ack.frame_id = received.offer.payload.frame_id;
    ack.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
    ack.slot_index = received.offer.payload.slot_index;
    ack.generation = received.offer.payload.generation;
    ack.status = params->verification_status;
    ack.surface_id = params->surface_id;
    memcpy(ack.actual_bgra,
           params->actual_bgra,
           sizeof(ack.actual_bgra));
    ack.client_pid = params->client_pid;
    mach_result = send_import_ack(server_port, &ack);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    if (params->verification_status == ALVR_IOSURFACE_PROBE_PASS)
        status = STATUS_SUCCESS;

cleanup:
    if (surface) CFRelease(surface);
    deallocate_send_port(&surface_port);
    destroy_receive_port(&reply_port);
    deallocate_send_port(&server_port);
    return status;
}

static NTSTATUS import_bind_call(void *opaque)
{
    struct import_bind_params *params = opaque;
    mach_port_t server_port = MACH_PORT_NULL;
    mach_port_t reply_port = MACH_PORT_NULL;
    mach_port_t surface_port = MACH_PORT_NULL;
    IOSurfaceRef surface = NULL;
    IOSurfaceRef bound_surface = NULL;
    union receive_message received;
    struct alvr_iosurface_ack failure_ack = {0};
    kern_return_t mach_result;
    NTSTATUS status = STATUS_UNSUCCESSFUL;
    bool offer_received = false;

    params->vk_result = -7;
    params->verification_status = ALVR_IOSURFACE_PROBE_PROTOCOL_MISMATCH;
    if (!params->image || !params->session_nonce || !params->service_name[0] ||
        !memchr(params->service_name,
                '\0',
                ALVR_IOSURFACE_SERVICE_NAME_CAPACITY))
        return STATUS_INVALID_PARAMETER;

    mach_result = bootstrap_look_up(
        bootstrap_port, params->service_name, &server_port);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    mach_result = mach_port_allocate(
        mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &reply_port);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    mach_result = send_import_request(
        server_port, reply_port, params->session_nonce);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    mach_result = receive_import_offer(reply_port, &received);
    if (mach_result != KERN_SUCCESS) goto cleanup;

    if (received.offer.header.msgh_id != ALVR_IOSURFACE_MESSAGE_OFFER ||
        !(received.offer.header.msgh_bits & MACH_MSGH_BITS_COMPLEX) ||
        received.offer.body.msgh_descriptor_count != 1 ||
        received.offer.surface_port.type != MACH_MSG_PORT_DESCRIPTOR ||
        received.offer.surface_port.name == MACH_PORT_NULL ||
        received.offer.payload.session_nonce != params->session_nonce ||
        received.offer.payload.protocol_version !=
            ALVR_IOSURFACE_PROTOCOL_VERSION)
    {
        mach_msg_destroy(&received.offer.header);
        goto cleanup;
    }
    offer_received = true;

    surface_port = received.offer.surface_port.name;
    received.offer.surface_port.name = MACH_PORT_NULL;
    surface = IOSurfaceLookupFromMachPort(surface_port);
    deallocate_send_port(&surface_port);

    params->frame_id = received.offer.payload.frame_id;
    params->slot_index = received.offer.payload.slot_index;
    params->generation = received.offer.payload.generation;
    params->surface_id = received.offer.payload.surface_id;
    params->width = received.offer.payload.width;
    params->height = received.offer.payload.height;
    params->bytes_per_row = received.offer.payload.bytes_per_row;
    params->pixel_format = received.offer.payload.pixel_format;
    params->producer_pid = received.offer.payload.producer_pid;
    memcpy(params->expected_bgra,
           received.offer.payload.expected_bgra,
           sizeof(params->expected_bgra));

    if (!surface)
    {
        params->verification_status = ALVR_IOSURFACE_PROBE_LOOKUP_FAILED;
        goto acknowledge_failure;
    }
    if (IOSurfaceGetID(surface) != params->surface_id ||
        IOSurfaceGetWidth(surface) != params->width ||
        IOSurfaceGetHeight(surface) != params->height ||
        IOSurfaceGetBytesPerRow(surface) != params->bytes_per_row ||
        IOSurfaceGetPixelFormat(surface) != params->pixel_format)
    {
        params->verification_status = ALVR_IOSURFACE_PROBE_METADATA_MISMATCH;
        goto acknowledge_failure;
    }

    resolve_moltenvk_functions();
    if (!use_iosurface || !get_iosurface)
    {
        params->verification_status = ALVR_IOSURFACE_PROBE_BIND_FAILED;
        goto acknowledge_failure;
    }
    params->vk_result = use_iosurface(
        (void *)(uintptr_t)params->image, surface);
    if (params->vk_result != 0)
    {
        params->verification_status = ALVR_IOSURFACE_PROBE_BIND_FAILED;
        goto acknowledge_failure;
    }
    get_iosurface((void *)(uintptr_t)params->image, &bound_surface);
    if (!bound_surface || IOSurfaceGetID(bound_surface) != params->surface_id)
    {
        params->verification_status = ALVR_IOSURFACE_PROBE_IDENTITY_MISMATCH;
        goto acknowledge_failure;
    }

    params->verification_status = ALVR_IOSURFACE_PROBE_PASS;
    status = STATUS_SUCCESS;
    goto cleanup;

acknowledge_failure:
    if (offer_received)
    {
        failure_ack.session_nonce = params->session_nonce;
        failure_ack.frame_id = params->frame_id;
        failure_ack.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
        failure_ack.slot_index = params->slot_index;
        failure_ack.generation = params->generation;
        failure_ack.status = params->verification_status;
        failure_ack.surface_id = params->surface_id;
        failure_ack.client_pid = getpid();
        send_import_ack(server_port, &failure_ack);
    }

cleanup:
    if (surface) CFRelease(surface);
    deallocate_send_port(&surface_port);
    destroy_receive_port(&reply_port);
    deallocate_send_port(&server_port);
    return status;
}

static NTSTATUS signal_ready_call(void *opaque)
{
    const struct signal_ready_params *params = opaque;
    mach_port_t server_port = MACH_PORT_NULL;
    struct alvr_iosurface_ack ack = {0};
    kern_return_t mach_result;

    if (!params->session_nonce || !params->frame_id ||
        !params->service_name[0] ||
        !memchr(params->service_name,
                '\0',
                ALVR_IOSURFACE_SERVICE_NAME_CAPACITY))
        return STATUS_INVALID_PARAMETER;

    mach_result = bootstrap_look_up(
        bootstrap_port, params->service_name, &server_port);
    if (mach_result != KERN_SUCCESS) return STATUS_UNSUCCESSFUL;

    ack.session_nonce = params->session_nonce;
    ack.frame_id = params->frame_id;
    ack.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
    ack.slot_index = params->slot_index;
    ack.generation = params->generation;
    ack.status = params->verification_status;
    ack.surface_id = params->surface_id;
    memcpy(ack.actual_bgra, params->actual_bgra, sizeof(ack.actual_bgra));
    ack.client_pid = getpid();
    mach_result = send_import_ack(server_port, &ack);
    deallocate_send_port(&server_port);
    return mach_result == KERN_SUCCESS ? STATUS_SUCCESS : STATUS_UNSUCCESSFUL;
}

static NTSTATUS frame_ready_call(void *opaque)
{
    struct frame_ready_params *params = opaque;
    mach_port_t server_port = MACH_PORT_NULL;
    mach_port_t reply_port = MACH_PORT_NULL;
    struct frame_ready_message message = {0};
    union release_receive_message received;
    kern_return_t mach_result;
    NTSTATUS status = STATUS_UNSUCCESSFUL;

    params->release_status = ALVR_IOSURFACE_PROBE_PROTOCOL_MISMATCH;
    if (!params->session_nonce || !params->frame_id ||
        !params->surface_id || !params->width || !params->height ||
        params->sample_x >= params->width ||
        params->sample_y >= params->height || !params->service_name[0] ||
        !memchr(params->service_name,
                '\0',
                ALVR_IOSURFACE_SERVICE_NAME_CAPACITY))
        return STATUS_INVALID_PARAMETER;

    mach_result = bootstrap_look_up(
        bootstrap_port, params->service_name, &server_port);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    mach_result = mach_port_allocate(
        mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &reply_port);
    if (mach_result != KERN_SUCCESS) goto cleanup;

    message.header.msgh_bits = MACH_MSGH_BITS(
        MACH_MSG_TYPE_COPY_SEND, MACH_MSG_TYPE_MAKE_SEND_ONCE);
    message.header.msgh_size = sizeof(message);
    message.header.msgh_remote_port = server_port;
    message.header.msgh_local_port = reply_port;
    message.header.msgh_id = ALVR_IOSURFACE_MESSAGE_FRAME_READY;
    message.payload.session_nonce = params->session_nonce;
    message.payload.frame_id = params->frame_id;
    message.payload.video_timestamp_ns = params->video_timestamp_ns;
    message.payload.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
    message.payload.slot_index = params->slot_index;
    message.payload.generation = params->generation;
    message.payload.flags = params->flags;
    message.payload.surface_id = params->surface_id;
    message.payload.width = params->width;
    message.payload.height = params->height;
    message.payload.sample_x = params->sample_x;
    message.payload.sample_y = params->sample_y;
    memcpy(message.payload.expected_bgra,
           params->expected_bgra,
           sizeof(message.payload.expected_bgra));
    message.payload.producer_pid = getpid();
    message.payload.pose_timestamp_ns = params->pose_timestamp_ns;
    message.payload.pose_generation = params->pose_generation;
    memcpy(message.payload.pose, params->pose, sizeof(message.payload.pose));

    mach_result = mach_msg(&message.header,
                           MACH_SEND_MSG | MACH_SEND_TIMEOUT,
                           message.header.msgh_size,
                           0,
                           MACH_PORT_NULL,
                           import_probe_timeout_ms,
                           MACH_PORT_NULL);
    if (mach_result != KERN_SUCCESS) goto cleanup;

    memset(&received, 0, sizeof(received));
    mach_result = mach_msg(&received.release.header,
                           MACH_RCV_MSG | MACH_RCV_TIMEOUT | MACH_RCV_LARGE,
                           0,
                           sizeof(received),
                           reply_port,
                           frame_release_timeout_ms,
                           MACH_PORT_NULL);
    if (mach_result != KERN_SUCCESS) goto cleanup;
    if (received.release.header.msgh_size !=
            sizeof(struct slot_release_message) ||
        (received.release.header.msgh_bits & MACH_MSGH_BITS_COMPLEX) ||
        received.release.header.msgh_id !=
            ALVR_IOSURFACE_MESSAGE_SLOT_RELEASE ||
        received.release.payload.session_nonce != params->session_nonce ||
        received.release.payload.frame_id != params->frame_id ||
        received.release.payload.protocol_version !=
            ALVR_IOSURFACE_PROTOCOL_VERSION ||
        received.release.payload.slot_index != params->slot_index ||
        received.release.payload.generation != params->generation ||
        received.release.payload.surface_id != params->surface_id)
    {
        mach_msg_destroy(&received.release.header);
        goto cleanup;
    }

    params->release_status = received.release.payload.status;
    params->consumer_pid = received.release.payload.consumer_pid;
    memcpy(params->actual_bgra,
           received.release.payload.actual_bgra,
           sizeof(params->actual_bgra));
    if (params->release_status == ALVR_IOSURFACE_PROBE_PASS ||
        params->release_status == ALVR_IOSURFACE_PROBE_SESSION_CLOSED ||
        params->release_status == ALVR_IOSURFACE_PROBE_FRAME_DROPPED)
        status = STATUS_SUCCESS;

cleanup:
    destroy_receive_port(&reply_port);
    deallocate_send_port(&server_port);
    return status;
}

const unixlib_entry_t __wine_unix_call_funcs[] =
{
    scalar_call,
    attach_call,
    release_port_call,
    import_probe_call,
    import_bind_call,
    signal_ready_call,
    frame_ready_call
};

C_ASSERT(sizeof(struct scalar_params) == 16);
C_ASSERT(sizeof(struct attach_params) == 24);
C_ASSERT(sizeof(struct import_probe_params) == 176);
C_ASSERT(sizeof(struct import_bind_params) == 200);
C_ASSERT(sizeof(struct signal_ready_params) == 168);
C_ASSERT(sizeof(struct frame_ready_params) == 264);
C_ASSERT(sizeof(__wine_unix_call_funcs) /
         sizeof(__wine_unix_call_funcs[0]) == unix_call_count);
