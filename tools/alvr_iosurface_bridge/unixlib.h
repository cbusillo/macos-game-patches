#ifndef ALVR_IOSURFACE_BRIDGE_UNIXLIB_H
#define ALVR_IOSURFACE_BRIDGE_UNIXLIB_H

#include <stdarg.h>

#include "windef.h"
#include "winternl.h"
#include "wine/unixlib.h"

enum alvr_iosurface_unix_call
{
    unix_call_scalar,
    unix_call_attach,
    unix_call_release_port,
    unix_call_import_probe,
    unix_call_import_bind,
    unix_call_signal_ready,
    unix_call_frame_ready,
    unix_call_count
};

#define ALVR_IOSURFACE_SERVICE_NAME_CAPACITY 128

struct scalar_params
{
    UINT64 input;
    UINT64 output;
};

struct attach_params
{
    UINT64 image;
    INT32 vk_result;
    UINT32 surface_id;
    UINT32 mach_port;
    UINT32 reserved;
};

struct release_port_params
{
    UINT32 mach_port;
};

struct import_probe_params
{
    UINT64 session_nonce;
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
    CHAR service_name[ALVR_IOSURFACE_SERVICE_NAME_CAPACITY];
};

struct import_bind_params
{
    UINT64 image;
    UINT64 session_nonce;
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
    CHAR service_name[ALVR_IOSURFACE_SERVICE_NAME_CAPACITY];
};

struct signal_ready_params
{
    UINT64 session_nonce;
    UINT64 frame_id;
    UINT32 slot_index;
    UINT32 generation;
    UINT32 verification_status;
    UINT32 surface_id;
    BYTE actual_bgra[4];
    UINT32 reserved;
    CHAR service_name[ALVR_IOSURFACE_SERVICE_NAME_CAPACITY];
};

struct frame_ready_params
{
    UINT64 session_nonce;
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
    UINT32 release_status;
    BYTE actual_bgra[4];
    UINT32 consumer_pid;
    UINT64 pose_timestamp_ns;
    UINT64 pose_generation;
    float pose[3][4];
    CHAR service_name[ALVR_IOSURFACE_SERVICE_NAME_CAPACITY];
};

#endif
