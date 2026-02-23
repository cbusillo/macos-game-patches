import os
import struct
from dataclasses import dataclass
from enum import IntEnum


PROTOCOL_VERSION = 1
PROTOCOL_MAGIC = 0x42545641
RING_MAGIC = 0x52425641
CODEC_HEVC = 2
DEFAULT_PORT = 37317
MAX_CONTROL_PAYLOAD_BYTES = 1024 * 1024

SLOT_STATE_EMPTY = 0
SLOT_STATE_WRITING = 1
SLOT_STATE_READY = 2
SLOT_STATE_READING = 3


class MessageKind(IntEnum):
    HELLO_REQUEST = 1
    HELLO_RESPONSE = 2
    CONFIGURE_VIDEO_REQUEST = 3
    CONFIGURE_VIDEO_RESPONSE = 4
    FRAME_READY = 5
    VIDEO_CONFIG = 6
    ENCODED_NAL = 7
    STATS = 8
    FATAL = 9
    PING = 10
    PONG = 11


class ErrorCode(IntEnum):
    NONE = 0
    BAD_MAGIC = 1
    BAD_VERSION = 2
    AUTH_FAILED = 3
    INVALID_CONFIGURATION = 4
    ENCODER_INIT_FAILED = 5
    HARDWARE_ENCODER_REQUIRED = 6
    INTERNAL_ERROR = 7


class PixelFormat(IntEnum):
    BGRA8 = 1


class ConfigureVideoFlag(IntEnum):
    LOW_LATENCY = 1 << 0
    REQUIRE_HARDWARE = 1 << 1


ENVELOPE_STRUCT = struct.Struct("<IHHI")
HELLO_REQUEST_STRUCT = struct.Struct("<32sIII")
HELLO_RESPONSE_STRUCT = struct.Struct("<IIII")
CONFIGURE_VIDEO_REQUEST_STRUCT = struct.Struct("<IIIIIIIIIIII")
CONFIGURE_VIDEO_RESPONSE_STRUCT = struct.Struct("<IIII")
FRAME_READY_STRUCT = struct.Struct("<IIIIQQ")
VIDEO_CONFIG_STRUCT = struct.Struct("<IIII")
ENCODED_NAL_STRUCT = struct.Struct("<IIIIQQ")
STATS_STRUCT = struct.Struct("<QQQQIIII")
FATAL_STRUCT = struct.Struct("<II")
RING_HEADER_STRUCT = struct.Struct("<IHH32sIIIIIIII")
RING_SLOT_HEADER_STRUCT = struct.Struct("<IIIIQQQQQ")


@dataclass(frozen=True)
class Envelope:
    protocol_magic: int
    protocol_version: int
    message_kind: int
    payload_bytes: int


def read_port_from_env() -> int:
    raw = os.environ.get("ALVR_VTBRIDGE_PORT", "")
    if raw == "":
        return DEFAULT_PORT
    value = int(raw)
    if value < 1 or value > 65535:
        raise ValueError(f"Invalid ALVR_VTBRIDGE_PORT: {raw}")
    return value


def make_frame(kind: MessageKind, payload: bytes) -> bytes:
    if len(payload) > MAX_CONTROL_PAYLOAD_BYTES:
        raise ValueError("Payload too large")
    envelope = ENVELOPE_STRUCT.pack(
        PROTOCOL_MAGIC,
        PROTOCOL_VERSION,
        int(kind),
        len(payload),
    )
    frame_body = envelope + payload
    return struct.pack("<I", len(frame_body)) + frame_body


def parse_envelope(data: bytes) -> Envelope:
    protocol_magic, protocol_version, message_kind, payload_bytes = ENVELOPE_STRUCT.unpack(data)
    return Envelope(
        protocol_magic=protocol_magic,
        protocol_version=protocol_version,
        message_kind=message_kind,
        payload_bytes=payload_bytes,
    )


def slot_offset(slot_index: int, slot_stride_bytes: int) -> int:
    return RING_HEADER_STRUCT.size + slot_index * slot_stride_bytes
