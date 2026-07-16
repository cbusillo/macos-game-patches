#include <bootstrap.h>
#include <CoreFoundation/CoreFoundation.h>
#include <IOSurface/IOSurface.h>
#include <mach/mach.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

#include "alvr_iosurface_bridge/iosurface_handoff_protocol.h"

enum
{
    probe_width = 16,
    probe_height = 8,
    probe_bytes_per_element = 4,
    probe_sample_x = 5,
    probe_sample_y = 3,
    probe_timeout_ms = 5000
};

static const uint8_t probe_expected_bgra[4] = {0x25, 0x7a, 0xc3, 0xff};

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

union receive_message
{
    struct request_message request;
    struct offer_message offer;
    struct ack_message ack;
    uint8_t bytes[sizeof(struct offer_message) + MAX_TRAILER_SIZE];
};

static const char *architecture_name(void)
{
#if defined(__arm64__)
    return "arm64";
#elif defined(__x86_64__)
    return "x86_64";
#else
    return "unknown";
#endif
}

static bool process_is_translated(void)
{
    int translated = 0;
    size_t size = sizeof(translated);

    if (sysctlbyname("sysctl.proc_translated", &translated, &size, NULL, 0) != 0)
        return false;
    return translated == 1;
}

static bool dictionary_set_u32(CFMutableDictionaryRef dictionary,
                               CFStringRef key,
                               uint32_t value)
{
    int64_t signed_value = value;
    CFNumberRef number = CFNumberCreate(
        kCFAllocatorDefault, kCFNumberSInt64Type, &signed_value);

    if (!number) return false;
    CFDictionarySetValue(dictionary, key, number);
    CFRelease(number);
    return true;
}

static IOSurfaceRef create_probe_surface(void)
{
    CFMutableDictionaryRef properties = CFDictionaryCreateMutable(
        kCFAllocatorDefault,
        0,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);
    IOSurfaceRef surface = NULL;

    if (!properties) return NULL;
    if (!dictionary_set_u32(properties, kIOSurfaceWidth, probe_width) ||
        !dictionary_set_u32(properties, kIOSurfaceHeight, probe_height) ||
        !dictionary_set_u32(properties,
                            kIOSurfaceBytesPerElement,
                            probe_bytes_per_element) ||
        !dictionary_set_u32(properties,
                            kIOSurfaceBytesPerRow,
                            probe_width * probe_bytes_per_element) ||
        !dictionary_set_u32(properties,
                            kIOSurfaceAllocSize,
                            probe_width * probe_height *
                                probe_bytes_per_element) ||
        !dictionary_set_u32(properties,
                            kIOSurfacePixelFormat,
                            ALVR_IOSURFACE_PIXEL_FORMAT_BGRA))
    {
        CFRelease(properties);
        return NULL;
    }

    surface = IOSurfaceCreate(properties);
    CFRelease(properties);
    return surface;
}

static bool initialize_probe_surface(IOSurfaceRef surface, bool write_marker)
{
    uint8_t *base;
    size_t bytes_per_row;

    if (IOSurfaceLock(surface, 0, NULL) != kIOReturnSuccess) return false;
    base = IOSurfaceGetBaseAddress(surface);
    bytes_per_row = IOSurfaceGetBytesPerRow(surface);
    if (!base || bytes_per_row < probe_width * probe_bytes_per_element)
    {
        IOSurfaceUnlock(surface, 0, NULL);
        return false;
    }

    memset(base, 0, IOSurfaceGetAllocSize(surface));
    if (write_marker)
    {
        memcpy(base + probe_sample_y * bytes_per_row +
                   probe_sample_x * probe_bytes_per_element,
               probe_expected_bgra,
               sizeof(probe_expected_bgra));
    }
    IOSurfaceUnlock(surface, 0, NULL);
    return true;
}

static bool read_probe_marker(IOSurfaceRef surface, uint8_t actual_bgra[4])
{
    const uint8_t *base;
    size_t bytes_per_row;

    if (IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL) != kIOReturnSuccess)
        return false;
    base = IOSurfaceGetBaseAddress(surface);
    bytes_per_row = IOSurfaceGetBytesPerRow(surface);
    if (!base || bytes_per_row < probe_width * probe_bytes_per_element)
    {
        IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
        return false;
    }

    memcpy(actual_bgra,
           base + probe_sample_y * bytes_per_row +
               probe_sample_x * probe_bytes_per_element,
           4);
    IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
    return true;
}

static kern_return_t receive_message(mach_port_t port,
                                     union receive_message *message)
{
    memset(message, 0, sizeof(*message));
    return mach_msg(&message->request.header,
                    MACH_RCV_MSG | MACH_RCV_TIMEOUT,
                    0,
                    sizeof(*message),
                    port,
                    probe_timeout_ms,
                    MACH_PORT_NULL);
}

static void destroy_port(mach_port_t *port)
{
    if (*port == MACH_PORT_NULL) return;
    mach_port_deallocate(mach_task_self(), *port);
    mach_port_mod_refs(
        mach_task_self(), *port, MACH_PORT_RIGHT_RECEIVE, -1);
    *port = MACH_PORT_NULL;
}

static void deallocate_port(mach_port_t *port)
{
    if (*port == MACH_PORT_NULL) return;
    mach_port_deallocate(mach_task_self(), *port);
    *port = MACH_PORT_NULL;
}

static int register_service(const char *service_name, mach_port_t *receive_port)
{
    kern_return_t result = mach_port_allocate(
        mach_task_self(), MACH_PORT_RIGHT_RECEIVE, receive_port);

    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_port_allocate code=%d\n", result);
        return 1;
    }

    result = mach_port_insert_right(mach_task_self(),
                                    *receive_port,
                                    *receive_port,
                                    MACH_MSG_TYPE_MAKE_SEND);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_port_insert_right code=%d\n", result);
        destroy_port(receive_port);
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
                "PROBE error=bootstrap_register code=%d detail=%s\n",
                result,
                bootstrap_strerror(result));
        destroy_port(receive_port);
        return 1;
    }
    return 0;
}

static int send_offer(mach_port_t reply_port,
                      mach_port_t surface_port,
                      const struct alvr_iosurface_offer *offer)
{
    struct offer_message message;
    kern_return_t result;

    memset(&message, 0, sizeof(message));
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
                      probe_timeout_ms,
                      MACH_PORT_NULL);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_msg_offer code=%d\n", result);
        deallocate_port(&reply_port);
        return 1;
    }
    return 0;
}

static int run_server(const char *service_name,
                      uint64_t expected_nonce,
                      bool client_writes)
{
    mach_port_t receive_port = MACH_PORT_NULL;
    mach_port_t surface_port = MACH_PORT_NULL;
    IOSurfaceRef surface = NULL;
    union receive_message received;
    struct alvr_iosurface_offer offer;
    kern_return_t result;
    int status = 1;

    if (register_service(service_name, &receive_port) != 0) return 1;
    printf("PROBE server architecture=%s translated=%s mode=%s "
           "registered=%s port=%u\n",
           architecture_name(),
           process_is_translated() ? "true" : "false",
           client_writes ? "client-writes" : "server-writes",
           service_name,
           receive_port);
    fflush(stdout);

    result = receive_message(receive_port, &received);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_msg_request code=%d\n", result);
        goto cleanup;
    }
    if (received.request.header.msgh_id != ALVR_IOSURFACE_MESSAGE_REQUEST ||
        received.request.header.msgh_size != sizeof(received.request) ||
        (received.request.header.msgh_bits & MACH_MSGH_BITS_COMPLEX) ||
        received.request.payload.protocol_version !=
            ALVR_IOSURFACE_PROTOCOL_VERSION ||
        received.request.payload.session_nonce != expected_nonce ||
        received.request.header.msgh_remote_port == MACH_PORT_NULL)
    {
        fprintf(stderr, "PROBE error=request_validation\n");
        mach_msg_destroy(&received.request.header);
        goto cleanup;
    }

    surface = create_probe_surface();
    if (!surface || !initialize_probe_surface(surface, !client_writes))
    {
        fprintf(stderr, "PROBE error=surface_create_or_write\n");
        deallocate_port(&received.request.header.msgh_remote_port);
        goto cleanup;
    }
    surface_port = IOSurfaceCreateMachPort(surface);
    if (surface_port == MACH_PORT_NULL)
    {
        fprintf(stderr, "PROBE error=IOSurfaceCreateMachPort\n");
        deallocate_port(&received.request.header.msgh_remote_port);
        goto cleanup;
    }

    memset(&offer, 0, sizeof(offer));
    offer.session_nonce = expected_nonce;
    offer.frame_id = 1;
    offer.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
    offer.slot_index = 0;
    offer.generation = 1;
    offer.surface_id = IOSurfaceGetID(surface);
    offer.width = IOSurfaceGetWidth(surface);
    offer.height = IOSurfaceGetHeight(surface);
    offer.bytes_per_row = IOSurfaceGetBytesPerRow(surface);
    offer.pixel_format = IOSurfaceGetPixelFormat(surface);
    offer.sample_x = probe_sample_x;
    offer.sample_y = probe_sample_y;
    memcpy(offer.expected_bgra,
           probe_expected_bgra,
           sizeof(offer.expected_bgra));
    offer.producer_pid = getpid();

    if (send_offer(received.request.header.msgh_remote_port,
                   surface_port,
                   &offer) != 0)
    {
        received.request.header.msgh_remote_port = MACH_PORT_NULL;
        goto cleanup;
    }
    received.request.header.msgh_remote_port = MACH_PORT_NULL;
    printf("PROBE server offered nonce=%llu surface_id=%u mach_port=%u "
           "size=%ux%u bytes_per_row=%u request_client_pid=%u\n",
           (unsigned long long)offer.session_nonce,
           offer.surface_id,
           surface_port,
           offer.width,
           offer.height,
           offer.bytes_per_row,
           received.request.payload.client_pid);
    fflush(stdout);

    deallocate_port(&surface_port);
    if (!client_writes)
    {
        CFRelease(surface);
        surface = NULL;
        printf("PROBE server released_local_surface=true\n");
        fflush(stdout);
    }

    result = receive_message(receive_port, &received);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_msg_ack code=%d\n", result);
        goto cleanup;
    }
    if (received.ack.header.msgh_id != ALVR_IOSURFACE_MESSAGE_ACK ||
        received.ack.header.msgh_size != sizeof(received.ack) ||
        (received.ack.header.msgh_bits & MACH_MSGH_BITS_COMPLEX) ||
        received.ack.payload.session_nonce != expected_nonce ||
        received.ack.payload.frame_id != offer.frame_id ||
        received.ack.payload.protocol_version !=
            ALVR_IOSURFACE_PROTOCOL_VERSION ||
        received.ack.payload.slot_index != offer.slot_index ||
        received.ack.payload.generation != offer.generation ||
        received.ack.payload.surface_id != offer.surface_id ||
        received.ack.payload.status != ALVR_IOSURFACE_PROBE_PASS ||
        memcmp(received.ack.payload.actual_bgra,
               offer.expected_bgra,
               sizeof(offer.expected_bgra)) != 0)
    {
        fprintf(stderr,
                "PROBE server ack_status=%u actual_bgra=%u,%u,%u,%u "
                "result=fail\n",
                received.ack.payload.status,
                received.ack.payload.actual_bgra[0],
                received.ack.payload.actual_bgra[1],
                received.ack.payload.actual_bgra[2],
                received.ack.payload.actual_bgra[3]);
        mach_msg_destroy(&received.ack.header);
        goto cleanup;
    }

    if (client_writes)
    {
        uint8_t actual_bgra[4] = {0};

        if (!surface || !read_probe_marker(surface, actual_bgra) ||
            memcmp(actual_bgra,
                   offer.expected_bgra,
                   sizeof(actual_bgra)) != 0)
        {
            fprintf(stderr,
                    "PROBE server native_read actual_bgra=%u,%u,%u,%u "
                    "expected_bgra=%u,%u,%u,%u result=fail\n",
                    actual_bgra[0],
                    actual_bgra[1],
                    actual_bgra[2],
                    actual_bgra[3],
                    offer.expected_bgra[0],
                    offer.expected_bgra[1],
                    offer.expected_bgra[2],
                    offer.expected_bgra[3]);
            goto cleanup;
        }
        printf("PROBE server native_read surface_id=%u "
               "actual_bgra=%u,%u,%u,%u result=pass\n",
               offer.surface_id,
               actual_bgra[0],
               actual_bgra[1],
               actual_bgra[2],
               actual_bgra[3]);
    }

    printf("PROBE server ack_client_pid=%u surface_id=%u "
           "actual_bgra=%u,%u,%u,%u result=pass\n",
           received.ack.payload.client_pid,
           received.ack.payload.surface_id,
           received.ack.payload.actual_bgra[0],
           received.ack.payload.actual_bgra[1],
           received.ack.payload.actual_bgra[2],
           received.ack.payload.actual_bgra[3]);
    status = 0;

cleanup:
    if (surface) CFRelease(surface);
    deallocate_port(&surface_port);
    destroy_port(&receive_port);
    return status;
}

static int send_request(mach_port_t server_port,
                        mach_port_t reply_port,
                        uint64_t nonce)
{
    struct request_message message;
    kern_return_t result;

    memset(&message, 0, sizeof(message));
    message.header.msgh_bits = MACH_MSGH_BITS(
        MACH_MSG_TYPE_COPY_SEND, MACH_MSG_TYPE_MAKE_SEND_ONCE);
    message.header.msgh_size = sizeof(message);
    message.header.msgh_remote_port = server_port;
    message.header.msgh_local_port = reply_port;
    message.header.msgh_id = ALVR_IOSURFACE_MESSAGE_REQUEST;
    message.payload.session_nonce = nonce;
    message.payload.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
    message.payload.client_pid = getpid();

    result = mach_msg(&message.header,
                      MACH_SEND_MSG | MACH_SEND_TIMEOUT,
                      message.header.msgh_size,
                      0,
                      MACH_PORT_NULL,
                      probe_timeout_ms,
                      MACH_PORT_NULL);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_msg_request code=%d\n", result);
        return 1;
    }
    return 0;
}

static int send_ack(mach_port_t server_port,
                    const struct alvr_iosurface_ack *ack)
{
    struct ack_message message;
    kern_return_t result;

    memset(&message, 0, sizeof(message));
    message.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
    message.header.msgh_size = sizeof(message);
    message.header.msgh_remote_port = server_port;
    message.header.msgh_id = ALVR_IOSURFACE_MESSAGE_ACK;
    message.payload = *ack;

    result = mach_msg(&message.header,
                      MACH_SEND_MSG | MACH_SEND_TIMEOUT,
                      message.header.msgh_size,
                      0,
                      MACH_PORT_NULL,
                      probe_timeout_ms,
                      MACH_PORT_NULL);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_msg_ack code=%d\n", result);
        return 1;
    }
    return 0;
}

static uint32_t verify_surface(IOSurfaceRef surface,
                               const struct alvr_iosurface_offer *offer,
                               uint8_t actual_bgra[4])
{
    const uint8_t *base;
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

static int run_client(const char *service_name, uint64_t expected_nonce)
{
    mach_port_t server_port = MACH_PORT_NULL;
    mach_port_t reply_port = MACH_PORT_NULL;
    mach_port_t received_surface_port = MACH_PORT_NULL;
    IOSurfaceRef surface = NULL;
    union receive_message received;
    struct alvr_iosurface_ack ack;
    kern_return_t result;
    uint32_t verification_status = ALVR_IOSURFACE_PROBE_PROTOCOL_MISMATCH;
    int status = 1;

    result = bootstrap_look_up(bootstrap_port, service_name, &server_port);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr,
                "PROBE error=bootstrap_look_up code=%d detail=%s\n",
                result,
                bootstrap_strerror(result));
        goto cleanup;
    }
    result = mach_port_allocate(
        mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &reply_port);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_port_allocate_reply code=%d\n", result);
        goto cleanup;
    }

    printf("PROBE client architecture=%s translated=%s service=%s "
           "server_port=%u\n",
           architecture_name(),
           process_is_translated() ? "true" : "false",
           service_name,
           server_port);
    fflush(stdout);

    if (send_request(server_port, reply_port, expected_nonce) != 0) goto cleanup;
    result = receive_message(reply_port, &received);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr, "PROBE error=mach_msg_offer code=%d\n", result);
        goto cleanup;
    }
    if (received.offer.header.msgh_id != ALVR_IOSURFACE_MESSAGE_OFFER ||
        received.offer.header.msgh_size != sizeof(received.offer) ||
        !(received.offer.header.msgh_bits & MACH_MSGH_BITS_COMPLEX) ||
        received.offer.body.msgh_descriptor_count != 1 ||
        received.offer.surface_port.type != MACH_MSG_PORT_DESCRIPTOR ||
        received.offer.surface_port.name == MACH_PORT_NULL ||
        received.offer.payload.session_nonce != expected_nonce ||
        received.offer.payload.protocol_version !=
            ALVR_IOSURFACE_PROTOCOL_VERSION)
    {
        fprintf(stderr, "PROBE error=offer_validation\n");
        mach_msg_destroy(&received.offer.header);
        goto cleanup;
    }

    received_surface_port = received.offer.surface_port.name;
    received.offer.surface_port.name = MACH_PORT_NULL;
    surface = IOSurfaceLookupFromMachPort(received_surface_port);
    deallocate_port(&received_surface_port);

    memset(&ack, 0, sizeof(ack));
    ack.session_nonce = expected_nonce;
    ack.frame_id = received.offer.payload.frame_id;
    ack.protocol_version = ALVR_IOSURFACE_PROTOCOL_VERSION;
    ack.slot_index = received.offer.payload.slot_index;
    ack.generation = received.offer.payload.generation;
    ack.surface_id = received.offer.payload.surface_id;
    ack.client_pid = getpid();
    if (!surface)
        verification_status = ALVR_IOSURFACE_PROBE_LOOKUP_FAILED;
    else
        verification_status = verify_surface(
            surface, &received.offer.payload, ack.actual_bgra);
    ack.status = verification_status;

    printf("PROBE client imported producer_pid=%u surface_id=%u size=%ux%u "
           "bytes_per_row=%u actual_bgra=%u,%u,%u,%u "
           "expected_bgra=%u,%u,%u,%u result=%s\n",
           received.offer.payload.producer_pid,
           surface ? IOSurfaceGetID(surface) : 0,
           surface ? (uint32_t)IOSurfaceGetWidth(surface) : 0,
           surface ? (uint32_t)IOSurfaceGetHeight(surface) : 0,
           surface ? (uint32_t)IOSurfaceGetBytesPerRow(surface) : 0,
           ack.actual_bgra[0],
           ack.actual_bgra[1],
           ack.actual_bgra[2],
           ack.actual_bgra[3],
           received.offer.payload.expected_bgra[0],
           received.offer.payload.expected_bgra[1],
           received.offer.payload.expected_bgra[2],
           received.offer.payload.expected_bgra[3],
           verification_status == ALVR_IOSURFACE_PROBE_PASS ? "pass" : "fail");
    fflush(stdout);

    if (send_ack(server_port, &ack) != 0) goto cleanup;
    status = verification_status == ALVR_IOSURFACE_PROBE_PASS ? 0 : 1;

cleanup:
    if (surface) CFRelease(surface);
    deallocate_port(&received_surface_port);
    destroy_port(&reply_port);
    deallocate_port(&server_port);
    return status;
}

static bool parse_nonce(const char *text, uint64_t *nonce)
{
    char *end = NULL;
    unsigned long long value;

    if (!text || !*text) return false;
    value = strtoull(text, &end, 10);
    if (!end || *end != '\0' || value == 0) return false;
    *nonce = value;
    return true;
}

int main(int argc, char **argv)
{
    uint64_t nonce;

    if (argc != 4 || !parse_nonce(argv[3], &nonce))
    {
        fprintf(stderr,
                "usage: %s server|server-bind|client SERVICE NONCE\n",
                argv[0]);
        return 64;
    }
    if (strcmp(argv[1], "server") == 0)
        return run_server(argv[2], nonce, false);
    if (strcmp(argv[1], "server-bind") == 0)
        return run_server(argv[2], nonce, true);
    if (strcmp(argv[1], "client") == 0)
        return run_client(argv[2], nonce);
    fprintf(stderr,
            "usage: %s server|server-bind|client SERVICE NONCE\n",
            argv[0]);
    return 64;
}
