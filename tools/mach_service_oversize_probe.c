#include <bootstrap.h>
#include <mach/mach.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "alvr_iosurface_bridge/iosurface_handoff_protocol.h"

enum
{
    oversized_payload_size = 4096,
    send_timeout_ms = 1000
};

struct oversized_message
{
    mach_msg_header_t header;
    uint8_t payload[oversized_payload_size];
};

int main(int argc, char **argv)
{
    struct oversized_message message = {0};
    mach_port_t server_port = MACH_PORT_NULL;
    kern_return_t result;

    if (argc != 3 ||
        (strcmp(argv[2], "import") != 0 && strcmp(argv[2], "frame") != 0))
    {
        fprintf(stderr, "usage: %s <service-name> <import|frame>\n", argv[0]);
        return 2;
    }

    result = bootstrap_look_up(bootstrap_port, argv[1], &server_port);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr,
                "oversize_probe lookup failed code=%d detail=%s\n",
                result,
                mach_error_string(result));
        return 1;
    }

    message.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
    message.header.msgh_remote_port = server_port;
    message.header.msgh_local_port = MACH_PORT_NULL;
    message.header.msgh_size = sizeof(message);
    message.header.msgh_id = strcmp(argv[2], "import") == 0
        ? ALVR_IOSURFACE_MESSAGE_REQUEST
        : ALVR_IOSURFACE_MESSAGE_FRAME_READY;
    result = mach_msg(&message.header,
                      MACH_SEND_MSG | MACH_SEND_TIMEOUT,
                      message.header.msgh_size,
                      0,
                      MACH_PORT_NULL,
                      send_timeout_ms,
                      MACH_PORT_NULL);
    mach_port_deallocate(mach_task_self(), server_port);
    if (result != KERN_SUCCESS)
    {
        fprintf(stderr,
                "oversize_probe send failed kind=%s code=%d detail=%s\n",
                argv[2],
                result,
                mach_error_string(result));
        return 1;
    }

    printf("oversize_probe sent kind=%s bytes=%zu\n", argv[2], sizeof(message));
    return 0;
}
