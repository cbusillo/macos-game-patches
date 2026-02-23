#!/usr/bin/env python3

import argparse
import mmap
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

from vtbridge_protocol import (
    CONFIGURE_VIDEO_REQUEST_STRUCT,
    CONFIGURE_VIDEO_RESPONSE_STRUCT,
    ConfigureVideoFlag,
    DEFAULT_PORT,
    ENCODED_NAL_STRUCT,
    ENVELOPE_STRUCT,
    FRAME_READY_STRUCT,
    HELLO_REQUEST_STRUCT,
    HELLO_RESPONSE_STRUCT,
    MessageKind,
    PixelFormat,
    RING_HEADER_STRUCT,
    RING_MAGIC,
    RING_SLOT_HEADER_STRUCT,
    SLOT_STATE_EMPTY,
    SLOT_STATE_READY,
    make_frame,
    parse_envelope,
    slot_offset,
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
    envelope = parse_envelope(frame_body[: ENVELOPE_STRUCT.size])
    payload = frame_body[ENVELOPE_STRUCT.size :]
    if len(payload) != envelope.payload_bytes:
        raise ValueError("payload mismatch")
    return envelope.message_kind, payload


def configure_and_prepare_ring(port: int, ring_path: Path) -> int:
    width = 64
    height = 64
    row_pitch = width * 4
    frame_bytes = row_pitch * height
    slot_stride = RING_SLOT_HEADER_STRUCT.size + frame_bytes
    slot_count = 2

    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
        hello_payload = HELLO_REQUEST_STRUCT.pack(bytes(range(32)), 4242, 0, 0)
        conn.sendall(make_frame(MessageKind.HELLO_REQUEST, hello_payload))
        hello_kind, hello_data = recv_message(conn)
        if hello_kind != int(MessageKind.HELLO_RESPONSE):
            raise RuntimeError("missing hello response")
        hello = HELLO_RESPONSE_STRUCT.unpack(hello_data)
        if hello[0] != 1:
            raise RuntimeError(f"hello rejected error={hello[2]}")

        flags = int(ConfigureVideoFlag.LOW_LATENCY) | int(ConfigureVideoFlag.REQUIRE_HARDWARE)
        configure_payload = CONFIGURE_VIDEO_REQUEST_STRUCT.pack(
            2,
            int(PixelFormat.BGRA8),
            width,
            height,
            row_pitch,
            90,
            1,
            15_000_000,
            90,
            slot_count,
            slot_stride,
            flags,
        )
        conn.sendall(make_frame(MessageKind.CONFIGURE_VIDEO_REQUEST, configure_payload))
        configure_kind, configure_data = recv_message(conn)
        if configure_kind != int(MessageKind.CONFIGURE_VIDEO_RESPONSE):
            raise RuntimeError("missing configure response")
        configure = CONFIGURE_VIDEO_RESPONSE_STRUCT.unpack(configure_data)
        if configure[0] != 1:
            raise RuntimeError(f"configure rejected error={configure[1]}")

        with open(ring_path, "r+b") as ring_file:
            with mmap.mmap(ring_file.fileno(), 0, access=mmap.ACCESS_WRITE) as mapping:
                ring_header = RING_HEADER_STRUCT.unpack(
                    mapping[: RING_HEADER_STRUCT.size]
                )
                if ring_header[0] != RING_MAGIC:
                    raise RuntimeError("ring magic mismatch")

                slot_index = 1
                offset = slot_offset(slot_index, slot_stride)
                writer_started_ns = time.monotonic_ns()
                writer_completed_ns = writer_started_ns + 100
                mapping[offset : offset + RING_SLOT_HEADER_STRUCT.size] = (
                    RING_SLOT_HEADER_STRUCT.pack(
                        SLOT_STATE_READY,
                        77,
                        0,
                        frame_bytes,
                        111,
                        222,
                        writer_started_ns,
                        writer_completed_ns,
                        0,
                    )
                )

                frame_ready_payload = FRAME_READY_STRUCT.pack(
                    slot_index,
                    77,
                    0,
                    frame_bytes,
                    111,
                    222,
                )
                conn.sendall(make_frame(MessageKind.FRAME_READY, frame_ready_payload))

                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    slot_header = RING_SLOT_HEADER_STRUCT.unpack(
                        mapping[offset : offset + RING_SLOT_HEADER_STRUCT.size]
                    )
                    if slot_header[0] == SLOT_STATE_EMPTY:
                        break
                    time.sleep(0.01)

        conn.settimeout(4.0)
        got_encoded = False
        encoded_payload_len = 0
        while True:
            kind, message_payload = recv_message(conn)
            message_kind = MessageKind(kind)
            if message_kind == MessageKind.VIDEO_CONFIG:
                continue
            if message_kind == MessageKind.STATS:
                continue
            if message_kind == MessageKind.ENCODED_NAL:
                if len(message_payload) < ENCODED_NAL_STRUCT.size:
                    raise RuntimeError("encoded message too small")
                encoded_header = ENCODED_NAL_STRUCT.unpack(
                    message_payload[: ENCODED_NAL_STRUCT.size]
                )
                encoded_payload_len = encoded_header[1]
                if len(message_payload) < ENCODED_NAL_STRUCT.size + encoded_payload_len:
                    raise RuntimeError("encoded payload mismatch")
                got_encoded = encoded_payload_len > 0
                break

        if got_encoded:
            return 0

    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run vtbridge ring state conformance test")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ring-path", default="/tmp/alvr-vtbridge-ring-conformance.bin")
    parser.add_argument(
        "--external-daemon",
        action="store_true",
        help="connect to an already-running daemon instead of starting one",
    )
    args = parser.parse_args()

    ring_path = Path(args.ring_path)
    if args.external_daemon:
        status = configure_and_prepare_ring(args.port, ring_path)
    else:
        if ring_path.exists():
            ring_path.unlink()

        daemon_cmd = [
            sys.executable,
            str(Path(__file__).resolve().parent / "vtbridge_daemon.py"),
            "--port",
            str(args.port),
            "--accept-configure",
            "--report-hardware-active",
            "--ring-path",
            str(ring_path),
        ]
        daemon = subprocess.Popen(
            daemon_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )

        try:
            time.sleep(0.4)
            status = configure_and_prepare_ring(args.port, ring_path)
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=2)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=2)

            if daemon.stdout is not None:
                daemon_output = daemon.stdout.read()
                if daemon_output:
                    print(daemon_output, end="")

        if ring_path.exists():
            ring_path.unlink()

    if status != 0:
        print("FAIL: vtbridge ring conformance")
        return 1

    print("PASS: vtbridge ring conformance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
