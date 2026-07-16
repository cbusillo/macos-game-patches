#include <bootstrap.h>
#include <CoreFoundation/CoreFoundation.h>
#include <IOSurface/IOSurface.h>
#include <mach/mach.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "alvr_iosurface_bridge/iosurface_handoff_protocol.h"

enum
{
    pool_slot_count = 3,
    receive_timeout_ms = 60000
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
    struct request_message request;
    struct frame_ready_message frame;
    uint8_t bytes[sizeof(struct frame_ready_message) + MAX_TRAILER_SIZE];
};

struct pool_slot
{
    IOSurfaceRef surface;
    uint32_t surface_id;
    uint32_t last_generation;
};

static volatile sig_atomic_t stop_requested;

static void request_stop(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

static bool dictionary_set_size(CFMutableDictionaryRef dictionary,
                                CFStringRef key,
                                size_t value)
{
    int64_t signed_value = (int64_t)value;
    CFNumberRef number = CFNumberCreate(
        kCFAllocatorDefault, kCFNumberSInt64Type, &signed_value);

    if (!number) return false;
    CFDictionarySetValue(dictionary, key, number);
    CFRelease(number);
    return true;
}

static IOSurfaceRef create_surface(uint32_t width, uint32_t height)
{
    const size_t bytes_per_row = IOSurfaceAlignProperty(
        kIOSurfaceBytesPerRow, (size_t)width * 4);
    const size_t alloc_size = bytes_per_row * height;
    CFMutableDictionaryRef properties = CFDictionaryCreateMutable(
        kCFAllocatorDefault,
        0,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);
    IOSurfaceRef surface = NULL;

    if (!properties) return NULL;
    if (!dictionary_set_size(properties, kIOSurfaceWidth, width) ||
        !dictionary_set_size(properties, kIOSurfaceHeight, height) ||
        !dictionary_set_size(properties, kIOSurfaceBytesPerElement, 4) ||
        !dictionary_set_size(properties, kIOSurfaceBytesPerRow, bytes_per_row) ||
        !dictionary_set_size(properties, kIOSurfaceAllocSize, alloc_size) ||
        !dictionary_set_size(properties,
                             kIOSurfacePixelFormat,
                             ALVR_IOSURFACE_PIXEL_FORMAT_BGRA))
    {
        CFRelease(properties);
        return NULL;
    }

    surface = IOSurfaceCreate(properties);
    CFRelease(properties);
    if (!surface) return NULL;
    if (IOSurfaceLock(surface, 0, NULL) != kIOReturnSuccess)
    {
        CFRelease(surface);
        return NULL;
    }
    memset(IOSurfaceGetBaseAddress(surface), 0, IOSurfaceGetAllocSize(surface));
    IOSurfaceUnlock(surface, 0, NULL);
    return surface;
}

static void destroy_receive_port(mach_port_t *port)
{
    if (*port == MACH_PORT_NULL) return;
    mach_port_deallocate(mach_task_self(), *port);
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

static int register_service(const char *service_name, mach_port_t *receive_port)
{
    kern_return_t result = mach_port_allocate(
        mach_task_self(), MACH_PORT_RIGHT_RECEIVE, receive_port);

    if (result != KERN_SUCCESS) return 1;
    result = mach_port_insert_right(mach_task_self(),
                                    *receive_port,
                                    *receive_port,
                                    MACH_MSG_TYPE_MAKE_SEND);
    if (result != KERN_SUCCESS)
    {
        destroy_receive_port(receive_port);
        return 1;
    }

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    result = bootstrap_register(
        bootstrap_port, (char *)service_name, *receive_port);
#pragma clang diagnostic pop
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr,
                "POOL error=bootstrap_register code=%d detail=%s\n",
                result,
                bootstrap_strerror(result));
        destroy_receive_port(receive_port);
        return 1;
    }
    return 0;
}

static kern_return_t receive_message(mach_port_t port,
                                     union receive_message *message,
                                     mach_msg_timeout_t timeout_ms)
{
    memset(message, 0, sizeof(*message));
    return mach_msg(&message->request.header,
                    MACH_RCV_MSG | MACH_RCV_TIMEOUT | MACH_RCV_INTERRUPT,
                    0,
                    sizeof(*message),
                    port,
                    timeout_ms,
                    MACH_PORT_NULL);
}

static int send_offer(mach_port_t reply_port,
                      mach_port_t surface_port,
                      const struct alvr_iosurface_offer *offer)
{
    struct offer_message message = {0};
    kern_return_t result;

    message.header.msgh_bits =
        MACH_MSGH_BITS(MACH_MSG_TYPE_MOVE_SEND_ONCE, 0) |
        MACH_MSGH_BITS_COMPLEX;
    message.header.msgh_size = sizeof(message);
    message.header.msgh_remote_port = reply_port;
    message.header.msgh_id = ALVR_IOSURFACE_MESSAGE_OFFER;
    message.body.msgh_descriptor_count = 1;
    message.surface_port.name = surface_port;
    message.surface_port.disposition = MACH_MSG_TYPE_COPY_SEND;
    message.surface_port.type = MACH_MSG_PORT_DESCRIPTOR;
    message.payload = *offer;
    result = mach_msg(&message.header,
                      MACH_SEND_MSG | MACH_SEND_TIMEOUT,
                      message.header.msgh_size,
                      0,
                      MACH_PORT_NULL,
                      5000,
                      MACH_PORT_NULL);
    if (result != KERN_SUCCESS)
    {
        deallocate_send_port(&reply_port);
        return 1;
    }
    return 0;
}

static int send_release(
    mach_port_t reply_port,
    const struct alvr_iosurface_slot_release *release)
{
    struct slot_release_message message = {0};
    kern_return_t result;

    message.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_MOVE_SEND_ONCE, 0);
    message.header.msgh_size = sizeof(message);
    message.header.msgh_remote_port = reply_port;
    message.header.msgh_id = ALVR_IOSURFACE_MESSAGE_SLOT_RELEASE;
    message.payload = *release;
    result = mach_msg(&message.header,
                      MACH_SEND_MSG | MACH_SEND_TIMEOUT,
                      message.header.msgh_size,
                      0,
                      MACH_PORT_NULL,
                      5000,
                      MACH_PORT_NULL);
    if (result != KERN_SUCCESS)
    {
        deallocate_send_port(&reply_port);
        return 1;
    }
    return 0;
}

static uint32_t read_sample(IOSurfaceRef surface,
                            uint32_t x,
                            uint32_t y,
                            uint8_t actual_bgra[4])
{
    const uint8_t *base;
    size_t bytes_per_row;

    if (x >= IOSurfaceGetWidth(surface) || y >= IOSurfaceGetHeight(surface))
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
    memcpy(actual_bgra, base + (size_t)y * bytes_per_row + (size_t)x * 4, 4);
    IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
    return ALVR_IOSURFACE_PROBE_PASS;
}

static uint32_t find_nonblack_sample(IOSurfaceRef surface,
                                     uint8_t actual_bgra[4])
{
    const uint8_t *base;
    size_t width = IOSurfaceGetWidth(surface);
    size_t height = IOSurfaceGetHeight(surface);
    size_t bytes_per_row;

    if (IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL) != kIOReturnSuccess)
        return ALVR_IOSURFACE_PROBE_LOCK_FAILED;
    base = IOSurfaceGetBaseAddress(surface);
    bytes_per_row = IOSurfaceGetBytesPerRow(surface);
    if (!base)
    {
        IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
        return ALVR_IOSURFACE_PROBE_LOCK_FAILED;
    }
    for (size_t y = 8; y < height; y += 16)
    {
        for (size_t x = 8; x < width; x += 16)
        {
            const uint8_t *pixel = base + y * bytes_per_row + x * 4;
            if ((uint32_t)pixel[0] + pixel[1] + pixel[2] >= 96)
            {
                memcpy(actual_bgra, pixel, 4);
                IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
                return ALVR_IOSURFACE_PROBE_PASS;
            }
        }
    }
    IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
    return ALVR_IOSURFACE_PROBE_PIXEL_MISMATCH;
}

static bool parse_u32(const char *text, uint32_t *value)
{
    char *end = NULL;
    unsigned long parsed;

    if (!text || !*text) return false;
    parsed = strtoul(text, &end, 10);
    if (!end || *end != '\0' || parsed > UINT32_MAX) return false;
    *value = (uint32_t)parsed;
    return true;
}

static bool parse_u64(const char *text, uint64_t *value)
{
    char *end = NULL;
    unsigned long long parsed;

    if (!text || !*text) return false;
    parsed = strtoull(text, &end, 10);
    if (!end || *end != '\0' || parsed == 0) return false;
    *value = (uint64_t)parsed;
    return true;
}

int main(int argc, char **argv)
{
    const char *service_name;
    uint64_t session_nonce;
    uint32_t width;
    uint32_t height;
    uint32_t frame_message_count;
    uint32_t release_delay_ms;
    mach_port_t receive_port = MACH_PORT_NULL;
    struct pool_slot slots[pool_slot_count] = {0};
    union receive_message received;
    uint64_t last_frame_id = 0;
    uint64_t last_video_timestamp_ns = 0;
    uint32_t self_test_count = 0;
    uint32_t real_frame_count = 0;
    uint32_t dropped_order_count = 0;
    uint32_t dropped_timestamp_count = 0;
    bool closing = false;
    uint32_t idle_timeouts = 0;
    int exit_status = 1;

    if (argc != 7 || !parse_u64(argv[2], &session_nonce) ||
        !parse_u32(argv[3], &width) || !width ||
        !parse_u32(argv[4], &height) || !height ||
        !parse_u32(argv[5], &frame_message_count) ||
        !parse_u32(argv[6], &release_delay_ms))
    {
        fprintf(stderr,
                "usage: %s SERVICE NONCE WIDTH HEIGHT FRAME_MESSAGES "
                "RELEASE_DELAY_MS\n",
                argv[0]);
        return 64;
    }
    service_name = argv[1];
    signal(SIGINT, request_stop);
    signal(SIGTERM, request_stop);

    for (uint32_t index = 0; index < pool_slot_count; ++index)
    {
        slots[index].surface = create_surface(width, height);
        if (!slots[index].surface)
        {
            fprintf(stderr, "POOL error=surface_create slot=%u\n", index);
            goto cleanup;
        }
        slots[index].surface_id = IOSurfaceGetID(slots[index].surface);
    }
    if (register_service(service_name, &receive_port) != 0) goto cleanup;
    printf("POOL registered=%s nonce=%llu slots=%u size=%ux%u "
           "release_delay_ms=%u\n",
           service_name,
           (unsigned long long)session_nonce,
           pool_slot_count,
           width,
           height,
           release_delay_ms);
    fflush(stdout);

    for (uint32_t slot_index = 0; slot_index < pool_slot_count; ++slot_index)
    {
        struct alvr_iosurface_offer offer = {0};
        mach_port_t surface_port;
        kern_return_t result = receive_message(
            receive_port, &received, receive_timeout_ms);

        if (result != KERN_SUCCESS ||
            received.request.header.msgh_id !=
                ALVR_IOSURFACE_MESSAGE_REQUEST ||
            received.request.payload.protocol_version !=
                ALVR_IOSURFACE_PROTOCOL_VERSION ||
            received.request.payload.session_nonce != session_nonce ||
            received.request.header.msgh_remote_port == MACH_PORT_NULL)
        {
            fprintf(stderr, "POOL error=import_request slot=%u code=%d\n",
                    slot_index,
                    result);
            goto cleanup;
        }

        surface_port = IOSurfaceCreateMachPort(slots[slot_index].surface);
        if (surface_port == MACH_PORT_NULL) goto cleanup;
        offer.session_nonce = session_nonce;
        offer.frame_id = slot_index + 1;
        offer.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
        offer.slot_index = slot_index;
        offer.generation = 0;
        offer.surface_id = slots[slot_index].surface_id;
        offer.width = width;
        offer.height = height;
        offer.bytes_per_row = IOSurfaceGetBytesPerRow(
            slots[slot_index].surface);
        offer.pixel_format = IOSurfaceGetPixelFormat(slots[slot_index].surface);
        offer.producer_pid = getpid();
        if (send_offer(received.request.header.msgh_remote_port,
                       surface_port,
                       &offer) != 0)
        {
            deallocate_send_port(&surface_port);
            goto cleanup;
        }
        received.request.header.msgh_remote_port = MACH_PORT_NULL;
        deallocate_send_port(&surface_port);
        printf("POOL offered slot=%u surface_id=%u bytes_per_row=%u "
               "client_pid=%u\n",
               slot_index,
               offer.surface_id,
               offer.bytes_per_row,
               received.request.payload.client_pid);
        fflush(stdout);
    }

    uint32_t message_index = 0;
    while (!stop_requested)
    {
        struct alvr_iosurface_slot_release release = {0};
        uint8_t actual_bgra[4] = {0};
        uint32_t validation_status = ALVR_IOSURFACE_PROBE_PASS;
        kern_return_t result = receive_message(receive_port, &received, 250);
        const struct alvr_iosurface_frame_ready *frame = &received.frame.payload;

        if (result == MACH_RCV_TIMED_OUT || result == MACH_RCV_INTERRUPTED)
        {
            if (closing && ++idle_timeouts >= 4) break;
            continue;
        }
        idle_timeouts = 0;

        if (result != KERN_SUCCESS ||
            received.frame.header.msgh_id !=
                ALVR_IOSURFACE_MESSAGE_FRAME_READY ||
            received.frame.header.msgh_remote_port == MACH_PORT_NULL ||
            frame->protocol_version != ALVR_IOSURFACE_PROTOCOL_VERSION ||
            frame->session_nonce != session_nonce ||
            frame->slot_index >= pool_slot_count)
        {
            fprintf(stderr,
                    "POOL error=frame_message index=%u code=%d id=%d\n",
                    message_index,
                    result,
                    received.frame.header.msgh_id);
            goto cleanup;
        }

        struct pool_slot *slot = &slots[frame->slot_index];
        const bool consumer_sample =
            (frame->flags & ALVR_IOSURFACE_FRAME_CONSUMER_SAMPLE) != 0;
        const bool self_test =
            (frame->flags & ALVR_IOSURFACE_FRAME_SELF_TEST) != 0;
        if ((frame->flags & ~(ALVR_IOSURFACE_FRAME_SELF_TEST |
                              ALVR_IOSURFACE_FRAME_CONSUMER_SAMPLE)) ||
            (self_test && consumer_sample))
            validation_status = ALVR_IOSURFACE_PROBE_PROTOCOL_MISMATCH;
        if (frame->surface_id != slot->surface_id || frame->width != width ||
            frame->height != height ||
            frame->generation <= slot->last_generation)
            validation_status = ALVR_IOSURFACE_PROBE_METADATA_MISMATCH;
        if (frame->frame_id <= last_frame_id)
        {
            ++dropped_order_count;
            validation_status = ALVR_IOSURFACE_PROBE_METADATA_MISMATCH;
        }
        if (!self_test &&
            (frame->video_timestamp_ns == 0 ||
             frame->video_timestamp_ns <= last_video_timestamp_ns))
        {
            ++dropped_timestamp_count;
            validation_status = ALVR_IOSURFACE_PROBE_METADATA_MISMATCH;
        }
        if (validation_status == ALVR_IOSURFACE_PROBE_PASS && consumer_sample)
            validation_status = find_nonblack_sample(
                slot->surface, actual_bgra);
        else if (validation_status == ALVR_IOSURFACE_PROBE_PASS && self_test)
            validation_status = read_sample(
                slot->surface, frame->sample_x, frame->sample_y, actual_bgra);
        if (validation_status == ALVR_IOSURFACE_PROBE_PASS &&
            self_test &&
            memcmp(actual_bgra, frame->expected_bgra, 4) != 0)
            validation_status = ALVR_IOSURFACE_PROBE_PIXEL_MISMATCH;

        if (validation_status == ALVR_IOSURFACE_PROBE_PASS)
        {
            last_frame_id = frame->frame_id;
            slot->last_generation = frame->generation;
            if (self_test)
                ++self_test_count;
            else
            {
                last_video_timestamp_ns = frame->video_timestamp_ns;
                ++real_frame_count;
            }
        }

        if (release_delay_ms) usleep((useconds_t)release_delay_ms * 1000);

        uint32_t release_status = validation_status;
        if (validation_status == ALVR_IOSURFACE_PROBE_PASS &&
            (closing ||
             (frame_message_count != 0 &&
              message_index + 1 >= frame_message_count)))
        {
            release_status = ALVR_IOSURFACE_PROBE_SESSION_CLOSED;
            closing = true;
        }

        release.session_nonce = session_nonce;
        release.frame_id = frame->frame_id;
        release.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
        release.slot_index = frame->slot_index;
        release.generation = frame->generation;
        release.status = release_status;
        release.surface_id = slot->surface_id;
        release.consumer_pid = getpid();
        memcpy(release.actual_bgra, actual_bgra, sizeof(actual_bgra));
        if (send_release(received.frame.header.msgh_remote_port, &release) != 0)
            goto cleanup;
        received.frame.header.msgh_remote_port = MACH_PORT_NULL;

        printf("POOL frame index=%u frame_id=%llu slot=%u generation=%u "
               "flags=0x%x timestamp_ns=%llu sample=%u,%u "
               "actual_bgra=%u,%u,%u,%u expected_bgra=%u,%u,%u,%u "
               "validation_status=%u release_status=%u result=%s\n",
               message_index,
               (unsigned long long)frame->frame_id,
               frame->slot_index,
               frame->generation,
               frame->flags,
               (unsigned long long)frame->video_timestamp_ns,
               frame->sample_x,
               frame->sample_y,
               actual_bgra[0],
               actual_bgra[1],
               actual_bgra[2],
               actual_bgra[3],
               frame->expected_bgra[0],
               frame->expected_bgra[1],
               frame->expected_bgra[2],
               frame->expected_bgra[3],
               validation_status,
               release_status,
               validation_status != ALVR_IOSURFACE_PROBE_PASS
                   ? "fail"
                   : (release_status == ALVR_IOSURFACE_PROBE_SESSION_CLOSED
                          ? "closed"
                          : "pass"));
        fflush(stdout);
        if (validation_status != ALVR_IOSURFACE_PROBE_PASS) goto cleanup;
        ++message_index;
    }

    printf("POOL summary self_tests=%u real_frames=%u reordered=%u "
           "timestamp_reordered=%u last_frame_id=%llu "
           "last_timestamp_ns=%llu stop_requested=%u closing=%u "
           "result=pass\n",
           self_test_count,
           real_frame_count,
           dropped_order_count,
           dropped_timestamp_count,
           (unsigned long long)last_frame_id,
           (unsigned long long)last_video_timestamp_ns,
           stop_requested ? 1 : 0,
           closing ? 1 : 0);
    exit_status = 0;

cleanup:
    destroy_receive_port(&receive_port);
    for (uint32_t index = 0; index < pool_slot_count; ++index)
    {
        if (slots[index].surface) CFRelease(slots[index].surface);
    }
    return exit_status;
}
