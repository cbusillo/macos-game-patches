#!/usr/bin/env python3

import argparse
import socket
import struct
import sys

from vtbridge_protocol import (
    CONFIGURE_VIDEO_REQUEST_STRUCT,
    CONFIGURE_VIDEO_RESPONSE_STRUCT,
    ConfigureVideoFlag,
    DEFAULT_PORT,
    HELLO_REQUEST_STRUCT,
    HELLO_RESPONSE_STRUCT,
    MessageKind,
    PixelFormat,
    make_frame,
    parse_envelope,
)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if chunk == b"":
            raise ConnectionError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_message(sock: socket.socket) -> tuple[int, bytes]:
    (frame_size,) = struct.unpack("<I", recv_exact(sock, 4))
    frame_body = recv_exact(sock, frame_size)
    envelope = parse_envelope(frame_body[:12])
    payload = frame_body[12:]
    if len(payload) != envelope.payload_bytes:
        raise ValueError("payload mismatch")
    return envelope.message_kind, payload


def make_hello_payload() -> bytes:
    token = bytes(range(32))
    return HELLO_REQUEST_STRUCT.pack(token, 9999, 0, 0)


def make_configure_payload(width: int, height: int, bitrate_bps: int) -> bytes:
    flags = int(ConfigureVideoFlag.LOW_LATENCY) | int(ConfigureVideoFlag.REQUIRE_HARDWARE)
    return CONFIGURE_VIDEO_REQUEST_STRUCT.pack(
        2,
        int(PixelFormat.BGRA8),
        width,
        height,
        width * 4,
        90,
        1,
        bitrate_bps,
        90,
        0,
        0,
        flags,
    )


def run_probe(host: str, port: int, width: int, height: int, bitrate_bps: int) -> int:
    with socket.create_connection((host, port), timeout=1.0) as conn:
        conn.sendall(make_frame(MessageKind.HELLO_REQUEST, make_hello_payload()))
        hello_kind, hello_payload = recv_message(conn)
        if hello_kind != int(MessageKind.HELLO_RESPONSE):
            print(f"FAIL: expected HELLO_RESPONSE, got {hello_kind}")
            return 1
        hello_response = HELLO_RESPONSE_STRUCT.unpack(hello_payload)
        if hello_response[0] != 1:
            print(f"FAIL: hello rejected error={hello_response[2]}")
            return 1

        conn.sendall(
            make_frame(
                MessageKind.CONFIGURE_VIDEO_REQUEST,
                make_configure_payload(width, height, bitrate_bps),
            )
        )
        configure_kind, configure_payload = recv_message(conn)
        if configure_kind != int(MessageKind.CONFIGURE_VIDEO_RESPONSE):
            print(f"FAIL: expected CONFIGURE_VIDEO_RESPONSE, got {configure_kind}")
            return 1
        configure_response = CONFIGURE_VIDEO_RESPONSE_STRUCT.unpack(configure_payload)
        print(
            "Probe result: "
            + f"accepted={configure_response[0]} error={configure_response[1]} "
            + f"hardware_active={configure_response[2]}"
        )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VT bridge handshake probe")
    parser.add_argument("--host", default="127.0.0.1", help="daemon host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="daemon port")
    parser.add_argument("--width", type=int, default=2016, help="frame width")
    parser.add_argument("--height", type=int, default=2240, help="frame height")
    parser.add_argument("--bitrate", type=int, default=30000000, help="bitrate in bps")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_probe(args.host, args.port, args.width, args.height, args.bitrate)


if __name__ == "__main__":
    sys.exit(main())

