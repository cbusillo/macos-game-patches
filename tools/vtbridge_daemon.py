#!/usr/bin/env python3

import argparse
import mmap
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO

from vtbridge_protocol import (
    CODEC_HEVC,
    CONFIGURE_VIDEO_REQUEST_STRUCT,
    CONFIGURE_VIDEO_RESPONSE_STRUCT,
    DEFAULT_PORT,
    ENCODED_NAL_STRUCT,
    ENVELOPE_STRUCT,
    ErrorCode,
    FATAL_STRUCT,
    FRAME_READY_STRUCT,
    HELLO_REQUEST_STRUCT,
    HELLO_RESPONSE_STRUCT,
    MAX_CONTROL_PAYLOAD_BYTES,
    MessageKind,
    PROTOCOL_MAGIC,
    PROTOCOL_VERSION,
    PixelFormat,
    RING_HEADER_STRUCT,
    RING_MAGIC,
    RING_SLOT_HEADER_STRUCT,
    SLOT_STATE_EMPTY,
    SLOT_STATE_READY,
    SLOT_STATE_READING,
    STATS_STRUCT,
    VIDEO_CONFIG_STRUCT,
    make_frame,
    parse_envelope,
    slot_offset,
)


@dataclass
class ServerConfig:
    bind_host: str
    port: int
    accept_configure: bool
    require_hardware: bool
    enforce_hw_hevc: bool
    ring_path: str
    force_codec: str
    force_test_pattern_hevc: bool
    debug_dump_dir: str
    debug_dump_limit: int
    native_window_capture_title_filters: list[str]
    native_window_capture_owner_filters: list[str]
    native_window_capture_fps: int


@dataclass
class NativeWindowCaptureFrame:
    width: int
    height: int
    row_bytes: int
    sequence: int
    capture_ns: int
    payload: bytes


class NativeWindowCaptureStream:
    HEADER_STRUCT = struct.Struct("<7Q")
    HEADER_MAGIC = 0x4D574346  # MWCF

    def __init__(
        self,
        *,
        title_filters: list[str],
        owner_filters: list[str],
        width: int,
        height: int,
        fps: int,
    ) -> None:
        script_path = Path(__file__).with_name("macos_window_capture_stream.swift")
        if not script_path.exists():
            raise FileNotFoundError(f"missing capture helper: {script_path}")

        binary_path = Path("/tmp/macos_window_capture_stream")
        if (not binary_path.exists()) or binary_path.stat().st_mtime < script_path.stat().st_mtime:
            build = subprocess.run(
                ["swiftc", "-O", str(script_path), "-o", str(binary_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            if build.returncode != 0:
                stderr_text = (build.stderr or "").strip()
                raise RuntimeError(
                    f"failed to build native capture helper rc={build.returncode} stderr={stderr_text}"
                )
            log(f"native_window_capture_built path={binary_path}")

        command = [
            str(binary_path),
            "--width",
            str(width),
            "--height",
            str(height),
            "--fps",
            str(max(1, fps)),
        ]
        if title_filters:
            command.extend(["--title-contains", ",".join(title_filters)])
        if owner_filters:
            command.extend(["--owner-contains", ",".join(owner_filters)])

        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._lock = threading.Lock()
        self._latest_frame: NativeWindowCaptureFrame | None = None
        self._latest_arrival_ns: int | None = None
        self._closed = False
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread.start()
        log(
            "native_window_capture_start "
            + f"pid={self._process.pid} width={width} height={height} fps={fps}"
        )

    def _read_exact(self, size: int) -> bytes:
        if self._process.stdout is None:
            raise RuntimeError("capture helper stdout unavailable")
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self._process.stdout.read(remaining)
            if not chunk:
                raise EOFError("capture helper stdout closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _reader_loop(self) -> None:
        try:
            while True:
                header = self._read_exact(self.HEADER_STRUCT.size)
                magic, width, height, row_bytes, payload_bytes, sequence, capture_ns = (
                    self.HEADER_STRUCT.unpack(header)
                )
                if magic != self.HEADER_MAGIC:
                    raise ValueError(f"bad native capture header magic: 0x{magic:08x}")
                payload = self._read_exact(payload_bytes)
                frame = NativeWindowCaptureFrame(
                    width=int(width),
                    height=int(height),
                    row_bytes=int(row_bytes),
                    sequence=int(sequence),
                    capture_ns=int(capture_ns),
                    payload=payload,
                )
                with self._lock:
                    self._latest_frame = frame
                    self._latest_arrival_ns = time.monotonic_ns()
                if sequence <= 5 or sequence % 120 == 0:
                    log(
                        "native_window_capture_frame "
                        + f"sequence={sequence} width={width} height={height} payload_bytes={payload_bytes}"
                    )
        except Exception as exc:
            if not self._closed:
                log(f"native_window_capture_reader_stopped reason={type(exc).__name__}: {exc}")

    def _stderr_loop(self) -> None:
        if self._process.stderr is None:
            return
        for raw_line in self._process.stderr:
            if self._closed:
                return
            text = raw_line.decode("utf-8", errors="replace").strip()
            if text:
                log(f"native_window_capture {text}")

    def latest_frame(self, *, max_age_ns: int | None = None) -> NativeWindowCaptureFrame | None:
        with self._lock:
            if self._latest_frame is None:
                return None
            if max_age_ns is not None and self._latest_arrival_ns is not None:
                if time.monotonic_ns() - self._latest_arrival_ns > max_age_ns:
                    return None
            return self._latest_frame

    def stop(self) -> None:
        self._closed = True
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1)
        log(f"native_window_capture_stop rc={self._process.returncode}")


@dataclass
class RingState:
    path: str
    token_bytes: bytes
    file_handle: BinaryIO
    mapping: mmap.mmap
    slot_count: int
    slot_stride_bytes: int


@dataclass
class SessionState:
    token_bytes: bytes
    ring: RingState | None
    frame_count: int
    width: int
    height: int
    row_pitch_bytes: int
    pixel_format: int
    sent_video_config: bool
    bootstrap_encoded_frame: bytes | None
    bootstrap_pattern_index: int
    last_encoded_frame: bytes | None
    reused_frame_count: int
    last_fresh_encode_sequence: int | None
    last_observed_spread_crc: int | None
    last_observed_sample_nonzero: int | None
    debug_dump_dir: str
    debug_dump_limit: int
    debug_dump_count: int
    debug_dump_last_spread_crc: int | None
    codec: str
    native_window_capture: NativeWindowCaptureStream | None
    native_window_capture_override_seen: bool


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = conn.recv(remaining)
        if chunk == b"":
            raise ConnectionError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(conn: socket.socket) -> tuple[int, bytes]:
    raw_size = recv_exact(conn, 4)
    (frame_size,) = struct.unpack("<I", raw_size)
    if frame_size < ENVELOPE_STRUCT.size:
        raise ValueError(f"invalid frame size {frame_size}")
    payload = recv_exact(conn, frame_size)
    envelope = parse_envelope(payload[: ENVELOPE_STRUCT.size])
    if envelope.protocol_magic != PROTOCOL_MAGIC:
        raise ValueError(f"bad protocol magic 0x{envelope.protocol_magic:08x}")
    if envelope.protocol_version != PROTOCOL_VERSION:
        raise ValueError(f"bad protocol version {envelope.protocol_version}")
    if envelope.payload_bytes > MAX_CONTROL_PAYLOAD_BYTES:
        raise ValueError(f"payload too large {envelope.payload_bytes}")
    body = payload[ENVELOPE_STRUCT.size :]
    if len(body) != envelope.payload_bytes:
        raise ValueError(
            f"payload mismatch expected={envelope.payload_bytes} actual={len(body)}"
        )
    return envelope.message_kind, body


def send_response(conn: socket.socket, kind: MessageKind, payload: bytes) -> None:
    conn.sendall(make_frame(kind, payload))


def send_fatal(conn: socket.socket, error_code: ErrorCode, message: str) -> None:
    payload_text = message.encode("utf-8")[:512]
    fatal_header = FATAL_STRUCT.pack(int(error_code), len(payload_text))
    send_response(conn, MessageKind.FATAL, fatal_header + payload_text)


def close_ring(ring: RingState | None) -> None:
    if ring is None:
        return
    ring.mapping.close()
    ring.file_handle.close()


def probe_hardware_hevc() -> tuple[bool, str]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False, "ffmpeg not found"

    command = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=30",
        "-t",
        "1",
        "-an",
        "-c:v",
        "hevc_videotoolbox",
        "-allow_sw",
        "false",
        "-require_sw",
        "false",
        "-realtime",
        "true",
        "-b:v",
        "4M",
        "-f",
        "null",
        "-",
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    if result.returncode != 0:
        return False, f"ffmpeg failed rc={result.returncode}"

    output_text = (result.stdout or "") + (result.stderr or "")
    if "hevc_videotoolbox" not in output_text.lower():
        return False, "hevc_videotoolbox marker missing"

    return True, "ok"


def create_ring(
    cfg: ServerConfig,
    token_bytes: bytes,
    frame_width: int,
    frame_height: int,
    row_pitch_bytes: int,
    pixel_format: int,
    requested_slot_count: int,
    requested_slot_stride_bytes: int,
) -> RingState:
    frame_payload_capacity = row_pitch_bytes * frame_height
    slot_count = requested_slot_count if requested_slot_count > 0 else 3
    slot_stride_bytes = requested_slot_stride_bytes
    if slot_stride_bytes <= 0:
        slot_stride_bytes = RING_SLOT_HEADER_STRUCT.size + frame_payload_capacity

    minimum_stride = RING_SLOT_HEADER_STRUCT.size + frame_payload_capacity
    if slot_stride_bytes < minimum_stride:
        raise ValueError(
            f"slot stride too small stride={slot_stride_bytes} minimum={minimum_stride}"
        )

    total_bytes = RING_HEADER_STRUCT.size + slot_count * slot_stride_bytes
    ring_path = cfg.ring_path
    with open(ring_path, "wb") as ring_writer:
        ring_writer.truncate(total_bytes)

    ring_file = open(ring_path, "r+b")
    ring_mapping = mmap.mmap(ring_file.fileno(), total_bytes, access=mmap.ACCESS_WRITE)

    ring_header = RING_HEADER_STRUCT.pack(
        RING_MAGIC,
        PROTOCOL_VERSION,
        0,
        token_bytes,
        slot_count,
        slot_stride_bytes,
        frame_width,
        frame_height,
        row_pitch_bytes,
        pixel_format,
        frame_payload_capacity,
        0,
    )
    ring_mapping[0 : RING_HEADER_STRUCT.size] = ring_header

    for slot_index in range(slot_count):
        offset = slot_offset(slot_index, slot_stride_bytes)
        ring_mapping[offset : offset + RING_SLOT_HEADER_STRUCT.size] = RING_SLOT_HEADER_STRUCT.pack(
            SLOT_STATE_EMPTY,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )

    ring_mapping.flush()
    log(
        "ring "
        + f"path={ring_path} slots={slot_count} stride={slot_stride_bytes} "
        + f"capacity={frame_payload_capacity}"
    )
    return RingState(
        path=ring_path,
        token_bytes=token_bytes,
        file_handle=ring_file,
        mapping=ring_mapping,
        slot_count=slot_count,
        slot_stride_bytes=slot_stride_bytes,
    )


def find_start_code(data: bytes, start: int) -> tuple[int, int]:
    index = start
    end = len(data) - 3
    while index <= end:
        if data[index] == 0 and data[index + 1] == 0:
            if data[index + 2] == 1:
                return index, 3
            if index + 3 < len(data) and data[index + 2] == 0 and data[index + 3] == 1:
                return index, 4
        index += 1
    return -1, 0


def split_annexb_nals(data: bytes) -> list[bytes]:
    nals: list[bytes] = []
    cursor = 0
    while True:
        pos, _code_len = find_start_code(data, cursor)
        if pos < 0:
            break
        next_pos, _next_len = find_start_code(data, pos + 3)
        if next_pos < 0:
            nals.append(data[pos:])
            break
        nals.append(data[pos:next_pos])
        cursor = next_pos
    return nals


def hevc_nal_type(nal: bytes) -> int:
    if nal.startswith(b"\x00\x00\x00\x01"):
        start = 4
    elif nal.startswith(b"\x00\x00\x01"):
        start = 3
    else:
        return -1

    if len(nal) <= start:
        return -1
    return (nal[start] >> 1) & 0x3F


def h264_nal_type(nal: bytes) -> int:
    if nal.startswith(b"\x00\x00\x00\x01"):
        start = 4
    elif nal.startswith(b"\x00\x00\x01"):
        start = 3
    else:
        return -1

    if len(nal) <= start:
        return -1
    return nal[start] & 0x1F


def repack_bgra(payload: bytes, width: int, height: int, row_pitch_bytes: int) -> bytes:
    expected_row = width * 4
    if row_pitch_bytes == expected_row:
        return payload[: expected_row * height]

    rows: list[bytes] = []
    for row in range(height):
        row_offset = row * row_pitch_bytes
        rows.append(payload[row_offset : row_offset + expected_row])
    return b"".join(rows)


def encode_frame_with_videotoolbox(
    payload: bytes,
    width: int,
    height: int,
    row_pitch_bytes: int,
    codec: str,
    input_pix_fmt: str = "rgba",
) -> tuple[bytes, float]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found")

    input_bytes = repack_bgra(payload, width, height, row_pitch_bytes)
    if codec == "h264":
        # Short-circuit testing path: synthesize a known test pattern so AVP
        # decoder validation does not depend on SteamVR texture contents.
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=90",
            "-frames:v",
            "1",
            "-an",
            "-vf",
            "format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-x264-params",
            "repeat-headers=1:keyint=1:min-keyint=1:scenecut=0",
            "-f",
            "h264",
            "-",
        ]
    else:
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            input_pix_fmt,
            "-s:v",
            f"{width}x{height}",
            "-r",
            "90",
            "-i",
            "-",
            "-frames:v",
            "1",
            "-an",
            "-c:v",
            "hevc_videotoolbox",
            "-allow_sw",
            "true",
            "-require_sw",
            "false",
            "-realtime",
            "true",
            "-f",
            "hevc",
            "-",
        ]

    started = time.perf_counter()
    result = subprocess.run(
        command,
        input=input_bytes if codec != "h264" else None,
        capture_output=True,
        check=False,
        timeout=5,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if result.returncode != 0:
        stderr_text = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"frame encode failed rc={result.returncode} elapsed_ms={elapsed_ms:.1f} stderr={stderr_text}"
        )

    if len(result.stdout) == 0:
        raise RuntimeError(f"frame encode produced no output elapsed_ms={elapsed_ms:.1f}")

    return result.stdout, elapsed_ms


def payload_spread_sample(payload: bytes, sample_points: int = 4096) -> tuple[bytes, int, int, int, int]:
    if not payload:
        return b"", 0, 0, 0, 0

    # Treat alpha-only variation as noise: classify motion from RGB channels.
    rgb_sample = bytearray()
    rgb_nonzero = 0

    step = max(4, (len(payload) // sample_points) // 4 * 4)
    for offset in range(0, len(payload) - 3, step):
        b = payload[offset]
        g = payload[offset + 1]
        r = payload[offset + 2]

        rgb_sample.extend((b, g, r))
        if b != 0 or g != 0 or r != 0:
            rgb_nonzero += 1

        if len(rgb_sample) >= sample_points * 3:
            break

    spread_sample = bytes(rgb_sample)
    spread_crc = zlib.crc32(spread_sample) & 0xFFFFFFFF
    sample_min = min(spread_sample) if spread_sample else 0
    sample_max = max(spread_sample) if spread_sample else 0
    return spread_sample, spread_crc, rgb_nonzero, sample_min, sample_max


def maybe_dump_debug_frame(
    state: SessionState,
    payload: bytes,
    sequence: int,
    spread_crc: int,
    sample_nonzero: int,
    input_pix_fmt: str = "rgba",
) -> None:
    if state.debug_dump_limit <= 0 or state.debug_dump_count >= state.debug_dump_limit:
        return
    if state.debug_dump_last_spread_crc == spread_crc:
        return

    output_dir = Path(state.debug_dump_dir)
    tag = "nonblack" if sample_nonzero > 0 else "black"
    output_path = output_dir / f"frame-{sequence:06d}-{tag}-crc{spread_crc:08x}.png"

    ffmpeg = shutil.which("ffmpeg")
    input_bytes = repack_bgra(payload, state.width, state.height, state.row_pitch_bytes)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if ffmpeg is None:
            raw_path = output_path.with_suffix(".bgra")
            raw_path.write_bytes(input_bytes)
            log(f"debug_frame_dump_raw sequence={sequence} path={raw_path}")
        else:
            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "rawvideo",
                "-pix_fmt",
                input_pix_fmt,
                "-s:v",
                f"{state.width}x{state.height}",
                "-i",
                "-",
                "-frames:v",
                "1",
                str(output_path),
            ]
            result = subprocess.run(
                command,
                input=input_bytes,
                capture_output=True,
                check=False,
                timeout=5,
            )
            if result.returncode != 0:
                stderr_text = (result.stderr or b"").decode("utf-8", errors="replace").strip()
                log(
                    "debug_frame_dump_failed "
                    + f"sequence={sequence} rc={result.returncode} stderr={stderr_text}"
                )
            else:
                log(f"debug_frame_dump_png sequence={sequence} path={output_path}")
    except Exception as exc:
        log(
            "debug_frame_dump_failed "
            + f"sequence={sequence} error={type(exc).__name__}: {exc}"
        )
    finally:
        state.debug_dump_count += 1
        state.debug_dump_last_spread_crc = spread_crc


def encode_bootstrap_frame_with_videotoolbox(width: int, height: int) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found")

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate=90",
        "-frames:v",
        "1",
        "-an",
        "-c:v",
        "hevc_videotoolbox",
        "-allow_sw",
        "false",
        "-require_sw",
        "false",
        "-realtime",
        "true",
        "-f",
        "hevc",
        "-",
    ]

    result = subprocess.run(command, capture_output=True, check=False, timeout=15)
    if result.returncode != 0:
        stderr_text = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"bootstrap encode failed rc={result.returncode} stderr={stderr_text}")

    if len(result.stdout) == 0:
        raise RuntimeError("bootstrap encode produced no output")

    return result.stdout


def encode_bootstrap_frame_with_libx265(width: int, height: int, color: str = "yellow") -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found")

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:size={width}x{height}:rate=60",
        "-frames:v",
        "1",
        "-an",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx265",
        "-preset",
        "medium",
        "-x265-params",
        "keyint=1:min-keyint=1:repeat-headers=1:scenecut=0:qp=40:log-level=error",
        "-f",
        "hevc",
        "-",
    ]

    result = subprocess.run(command, capture_output=True, check=False, timeout=20)
    if result.returncode != 0:
        stderr_text = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"bootstrap x265 encode failed rc={result.returncode} stderr={stderr_text}")

    if len(result.stdout) == 0:
        raise RuntimeError("bootstrap x265 encode produced no output")

    return result.stdout


def maybe_send_video_config(conn: socket.socket, state: SessionState, nals: list[bytes]) -> None:
    if state.sent_video_config:
        return

    if state.codec == "h264":
        config_nals = [nal for nal in nals if h264_nal_type(nal) in {7, 8}]
    else:
        config_nals = [nal for nal in nals if hevc_nal_type(nal) in {32, 33, 34}]
    if not config_nals:
        return

    config_payload = b"".join(config_nals)
    header = VIDEO_CONFIG_STRUCT.pack(CODEC_HEVC, 1, len(config_payload), 0)
    send_response(conn, MessageKind.VIDEO_CONFIG, header + config_payload)
    state.sent_video_config = True


def send_encoded_nal(
    conn: socket.socket,
    encoded_bytes: bytes,
    sequence: int,
    presentation_ns: int,
    target_ns: int,
    codec: str,
) -> None:
    nals = split_annexb_nals(encoded_bytes)
    if codec == "h264":
        is_idr = any(h264_nal_type(nal) == 5 for nal in nals)
    else:
        is_idr = any(hevc_nal_type(nal) in {19, 20} for nal in nals)
    # Keep VPS/SPS/PPS in-band for now: the current bridge client ignores
    # VideoConfig messages, so stripping config NALs causes client decoder
    # restarts due to missing parameter sets.
    payload = encoded_bytes

    header = ENCODED_NAL_STRUCT.pack(
        sequence,
        len(payload),
        int(is_idr),
        0,
        presentation_ns,
        target_ns,
    )
    send_response(conn, MessageKind.ENCODED_NAL, header + payload)


def maybe_send_stats(conn: socket.socket, state: SessionState) -> None:
    if state.frame_count == 0 or state.frame_count % 120 != 0:
        return

    stats = STATS_STRUCT.pack(
        state.frame_count,
        state.frame_count,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    send_response(conn, MessageKind.STATS, stats)


def handle_hello(conn: socket.socket, payload: bytes, state: SessionState) -> None:
    if len(payload) != HELLO_REQUEST_STRUCT.size:
        raise ValueError("invalid HelloRequest payload size")
    token_bytes, driver_pid, steamvr_build_id, _reserved = HELLO_REQUEST_STRUCT.unpack(payload)
    state.token_bytes = token_bytes
    run_id = token_bytes[:16].hex()
    log(
        "hello "
        + f"driver_pid={driver_pid} steamvr_build_id={steamvr_build_id} run_id={run_id}"
    )
    response = HELLO_RESPONSE_STRUCT.pack(1, os.getpid(), int(ErrorCode.NONE), 0)
    send_response(conn, MessageKind.HELLO_RESPONSE, response)


def handle_configure(
    conn: socket.socket,
    payload: bytes,
    cfg: ServerConfig,
    state: SessionState,
) -> None:
    if len(payload) != CONFIGURE_VIDEO_REQUEST_STRUCT.size:
        raise ValueError("invalid ConfigureVideoRequest payload size")

    fields = CONFIGURE_VIDEO_REQUEST_STRUCT.unpack(payload)
    codec = fields[0]
    pixel_format = fields[1]
    width = fields[2]
    height = fields[3]
    row_pitch_bytes = fields[4]
    fps_num = fields[5]
    fps_den = fields[6]
    bitrate = fields[7]
    ring_slot_count = fields[9]
    ring_slot_stride_bytes = fields[10]
    flags = fields[11]

    log(
        "configure "
        + f"codec={codec} size={width}x{height} fps={fps_num}/{fps_den} "
        + f"bitrate={bitrate} slots={ring_slot_count} stride={ring_slot_stride_bytes} "
        + f"flags=0x{flags:08x}"
    )

    accepted = bool(cfg.accept_configure)
    hardware_active = bool(cfg.accept_configure and cfg.require_hardware)
    error_code = ErrorCode.NONE

    if codec != CODEC_HEVC:
        accepted = False
        hardware_active = False
        error_code = ErrorCode.INVALID_CONFIGURATION

    if pixel_format != int(PixelFormat.BGRA8):
        accepted = False
        hardware_active = False
        error_code = ErrorCode.INVALID_CONFIGURATION

    if accepted and cfg.enforce_hw_hevc:
        passed, reason = probe_hardware_hevc()
        log(f"hevc_probe passed={int(passed)} reason={reason}")
        if not passed:
            accepted = False
            hardware_active = False
            error_code = ErrorCode.HARDWARE_ENCODER_REQUIRED
        else:
            hardware_active = True

    if accepted:
        close_ring(state.ring)
        state.ring = create_ring(
            cfg,
            state.token_bytes,
            width,
            height,
            row_pitch_bytes,
            pixel_format,
            ring_slot_count,
            ring_slot_stride_bytes,
        )
        state.width = width
        state.height = height
        state.row_pitch_bytes = row_pitch_bytes
        state.pixel_format = pixel_format
        state.sent_video_config = False
        state.bootstrap_encoded_frame = None
        state.last_encoded_frame = None
        if state.native_window_capture is not None:
            state.native_window_capture.stop()
            state.native_window_capture = None
        if cfg.native_window_capture_title_filters or cfg.native_window_capture_owner_filters:
            try:
                state.native_window_capture = NativeWindowCaptureStream(
                    title_filters=cfg.native_window_capture_title_filters,
                    owner_filters=cfg.native_window_capture_owner_filters,
                    width=width,
                    height=height,
                    fps=cfg.native_window_capture_fps,
                )
            except Exception as exc:
                log(
                    "native_window_capture_start_failed "
                    + f"reason={type(exc).__name__}: {exc}"
                )
                state.native_window_capture = None

    response = CONFIGURE_VIDEO_RESPONSE_STRUCT.pack(
        int(accepted),
        int(error_code),
        int(hardware_active),
        0,
    )
    send_response(conn, MessageKind.CONFIGURE_VIDEO_RESPONSE, response)

    if accepted:
        log("configure accepted")
        if cfg.force_test_pattern_hevc and state.codec == "hevc":
            # Keep handshake responsive: avoid doing expensive bootstrap
            # encoding work before ConfigureVideoResponse is sent.
            state.bootstrap_pattern_index = 0
            state.bootstrap_encoded_frame = encode_bootstrap_frame_with_libx265(
                width,
                height,
                color="yellow",
            )
            state.last_encoded_frame = state.bootstrap_encoded_frame
            log(
                "bootstrap_test_pattern "
                + f"codec=hevc encoder=libx265 bytes={len(state.bootstrap_encoded_frame)}"
            )


def handle_frame_ready(conn: socket.socket, payload: bytes, state: SessionState) -> None:
    if len(payload) != FRAME_READY_STRUCT.size:
        raise ValueError("invalid FrameReady payload size")
    if state.ring is None:
        raise ValueError("frame received before configure")

    slot_index, sequence, frame_flags, payload_bytes, presentation_ns, target_ns = (
        FRAME_READY_STRUCT.unpack(payload)
    )

    if slot_index >= state.ring.slot_count:
        raise ValueError(f"slot index out of range: {slot_index}")

    offset = slot_offset(slot_index, state.ring.slot_stride_bytes)
    slot_data = state.ring.mapping[offset : offset + RING_SLOT_HEADER_STRUCT.size]
    current = RING_SLOT_HEADER_STRUCT.unpack(slot_data)
    state_value = current[0]

    if state_value != SLOT_STATE_READY:
        log(
            "frame_ready_state_mismatch "
            + f"slot={slot_index} state={state_value} expected={SLOT_STATE_READY}"
        )

    if payload_bytes > state.row_pitch_bytes * state.height:
        raise ValueError(f"payload exceeds configured frame capacity: {payload_bytes}")

    slot_payload_offset = offset + RING_SLOT_HEADER_STRUCT.size
    payload_end = slot_payload_offset + payload_bytes
    frame_payload = bytes(state.ring.mapping[slot_payload_offset:payload_end])

    state.ring.mapping[offset : offset + RING_SLOT_HEADER_STRUCT.size] = RING_SLOT_HEADER_STRUCT.pack(
        SLOT_STATE_READING,
        sequence,
        frame_flags,
        payload_bytes,
        presentation_ns,
        target_ns,
        current[6],
        current[7],
        0,
    )

    reader_completed_ns = time.monotonic_ns()
    state.ring.mapping[offset : offset + RING_SLOT_HEADER_STRUCT.size] = RING_SLOT_HEADER_STRUCT.pack(
        SLOT_STATE_EMPTY,
        sequence,
        frame_flags,
        payload_bytes,
        presentation_ns,
        target_ns,
        current[6],
        current[7],
        reader_completed_ns,
    )

    input_pix_fmt = "rgba"
    if state.native_window_capture is not None:
        native_frame = state.native_window_capture.latest_frame(max_age_ns=500_000_000)
        if native_frame is not None:
            expected_payload_bytes = state.row_pitch_bytes * state.height
            if (
                native_frame.width == state.width
                and native_frame.height == state.height
                and native_frame.row_bytes == state.row_pitch_bytes
                and len(native_frame.payload) == expected_payload_bytes
            ):
                if not state.native_window_capture_override_seen:
                    state.native_window_capture_override_seen = True
                    state.debug_dump_count = 0
                    state.debug_dump_last_spread_crc = None
                    log("native_window_capture_debug_dump_reset reason=first_override")
                frame_payload = native_frame.payload
                payload_bytes = len(frame_payload)
                input_pix_fmt = "bgra"
                if sequence <= 5 or sequence % 120 == 0:
                    log(
                        "native_window_capture_override "
                        + f"sequence={sequence} native_sequence={native_frame.sequence} "
                        + f"payload_bytes={payload_bytes} capture_ns={native_frame.capture_ns}"
                    )
            elif sequence <= 5 or sequence % 120 == 0:
                log(
                    "native_window_capture_mismatch "
                    + f"sequence={sequence} native_size={native_frame.width}x{native_frame.height} "
                    + f"native_row_bytes={native_frame.row_bytes} native_payload={len(native_frame.payload)} "
                    + f"expected_size={state.width}x{state.height} expected_row_bytes={state.row_pitch_bytes}"
                )
        elif sequence <= 5 or sequence % 120 == 0:
            log(f"native_window_capture_stale sequence={sequence} fallback=ring_payload")

    sample_rgb = bytearray()
    for offset in range(0, min(len(frame_payload), 262144) - 3, 64):
        sample_rgb.extend((frame_payload[offset], frame_payload[offset + 1], frame_payload[offset + 2]))
    sample_crc = zlib.crc32(bytes(sample_rgb)) & 0xFFFFFFFF
    spread_sample, spread_crc, sample_nonzero, sample_min, sample_max = payload_spread_sample(frame_payload)

    previous_spread_crc = state.last_observed_spread_crc
    previous_sample_nonzero = state.last_observed_sample_nonzero
    state.last_observed_spread_crc = spread_crc
    state.last_observed_sample_nonzero = sample_nonzero

    encode_elapsed_ms = -1.0
    encoded_fresh = False
    encoded: bytes | None = None
    encode_reason = "cached"
    # Optional short-circuit path for AVP bring-up: stream a known-good static
    # HEVC frame so transport and decoder behavior can be isolated from SteamVR
    # texture capture and VideoToolbox variability.
    if state.bootstrap_encoded_frame is not None:
        if sequence % 90 == 1:
            palette = ["yellow", "red", "green", "blue", "magenta", "cyan", "white"]
            state.bootstrap_pattern_index += 1
            color = palette[state.bootstrap_pattern_index % len(palette)]
            try:
                state.bootstrap_encoded_frame = encode_bootstrap_frame_with_libx265(
                    state.width,
                    state.height,
                    color=color,
                )
                log(
                    "bootstrap_test_pattern_refresh "
                    + f"sequence={sequence} color={color} bytes={len(state.bootstrap_encoded_frame)}"
                )
            except Exception as exc:
                log(
                    "bootstrap_test_pattern_refresh_failed "
                    + f"sequence={sequence} color={color} reason={exc}"
                )
        encoded = state.bootstrap_encoded_frame
        encode_reason = "bootstrap"
    else:
        # Encoding every incoming frame at 4288x2048 is too slow on macOS 26.4
        # with per-frame ffmpeg startup. Re-encode adaptively on payload changes
        # and periodically as a safeguard for stale cached content.
        should_encode = False
        if state.last_encoded_frame is None:
            should_encode = True
            encode_reason = "no_cached_frame"
        elif previous_spread_crc is None or previous_sample_nonzero is None:
            should_encode = True
            encode_reason = "first_payload_sample"
        elif spread_crc != previous_spread_crc or sample_nonzero != previous_sample_nonzero:
            should_encode = True
            encode_reason = "payload_changed"
        elif state.last_fresh_encode_sequence is None or (sequence - state.last_fresh_encode_sequence) >= 45:
            should_encode = True
            encode_reason = "periodic_refresh"
        try:
            if should_encode:
                encoded, encode_elapsed_ms = encode_frame_with_videotoolbox(
                    frame_payload,
                    state.width,
                    state.height,
                    state.row_pitch_bytes,
                    state.codec,
                    input_pix_fmt=input_pix_fmt,
                )
                state.last_encoded_frame = encoded
                encoded_fresh = True
                state.last_fresh_encode_sequence = sequence
            else:
                encoded = state.last_encoded_frame
                if encoded is None:
                    raise RuntimeError("missing cached encoded frame")
                state.reused_frame_count += 1
                encode_reason = "cached"
        except Exception as exc:
            if state.last_encoded_frame is None:
                raise
            encoded = state.last_encoded_frame
            if encoded is None:
                raise RuntimeError("missing cached encoded frame after encode failure")
            state.reused_frame_count += 1
            encode_reason = "encode_failed_reused_cached"
            log(
                "frame_encode_failed "
                + f"sequence={sequence} reason={exc} reuse_last_encoded_bytes={len(encoded)}"
            )

    if encoded is None:
        raise RuntimeError("encoded payload unavailable")

    if encoded_fresh:
        maybe_dump_debug_frame(
            state,
            frame_payload,
            sequence,
            spread_crc,
            sample_nonzero,
            input_pix_fmt=input_pix_fmt,
        )
        log(
            "fresh_encode "
            + f"sequence={sequence} encoded_bytes={len(encoded)} sample_crc=0x{sample_crc:08x} "
            + f"spread_crc=0x{spread_crc:08x} sample_nonzero={sample_nonzero} "
            + f"sample_len={len(spread_sample)} "
            + f"sample_min={sample_min} sample_max={sample_max} reason={encode_reason}"
        )

    nals = split_annexb_nals(encoded)
    maybe_send_video_config(conn, state, nals)
    effective_target_ns = 8 if state.bootstrap_encoded_frame is not None else target_ns
    send_encoded_nal(conn, encoded, sequence, presentation_ns, effective_target_ns, state.codec)

    state.frame_count += 1
    if state.frame_count <= 5 or state.frame_count % 60 == 0:
        log(
            "frame_ready "
            + f"sequence={sequence} payload_bytes={payload_bytes} "
            + f"encoded_bytes={len(encoded)} encode_ms={encode_elapsed_ms:.1f} "
            + f"fresh={int(encoded_fresh)} reused_total={state.reused_frame_count} "
            + f"presentation_ns={presentation_ns} target_ns={target_ns}"
        )
    maybe_send_stats(conn, state)
    if state.frame_count % 120 == 0:
        log(f"frames={state.frame_count}")


def run_server(cfg: ServerConfig) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((cfg.bind_host, cfg.port))
        server.listen(4)
        log(f"listening {cfg.bind_host}:{cfg.port}")
        while True:
            conn, addr = server.accept()
            with conn:
                peer = f"{addr[0]}:{addr[1]}"
                log(f"accepted {peer}")
                state = SessionState(
                    token_bytes=b"\x00" * 32,
                    ring=None,
                    frame_count=0,
                    width=0,
                    height=0,
                    row_pitch_bytes=0,
                    pixel_format=0,
                    sent_video_config=False,
                    bootstrap_encoded_frame=None,
                    bootstrap_pattern_index=0,
                    last_encoded_frame=None,
                    reused_frame_count=0,
                    last_fresh_encode_sequence=None,
                    last_observed_spread_crc=None,
                    last_observed_sample_nonzero=None,
                    debug_dump_dir=cfg.debug_dump_dir,
                    debug_dump_limit=cfg.debug_dump_limit,
                    debug_dump_count=0,
                    debug_dump_last_spread_crc=None,
                    codec=cfg.force_codec,
                    native_window_capture=None,
                    native_window_capture_override_seen=False,
                )
                try:
                    while True:
                        kind, body = recv_frame(conn)
                        message_kind = MessageKind(kind)
                        if message_kind == MessageKind.HELLO_REQUEST:
                            handle_hello(conn, body, state)
                            continue
                        if message_kind == MessageKind.CONFIGURE_VIDEO_REQUEST:
                            handle_configure(conn, body, cfg, state)
                            continue
                        if message_kind == MessageKind.FRAME_READY:
                            handle_frame_ready(conn, body, state)
                            continue
                        if message_kind == MessageKind.PING:
                            send_response(conn, MessageKind.PONG, body)
                            continue
                        send_fatal(
                            conn,
                            ErrorCode.INVALID_CONFIGURATION,
                            f"unsupported message kind {kind}",
                        )
                        break
                except KeyboardInterrupt:
                    close_ring(state.ring)
                    if state.native_window_capture is not None:
                        state.native_window_capture.stop()
                    raise
                except Exception as exc:
                    log(f"connection closed {peer} reason={exc}")
                    close_ring(state.ring)
                    if state.native_window_capture is not None:
                        state.native_window_capture.stop()
                    continue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VT bridge daemon skeleton for local handshake testing")
    parser.add_argument("--bind", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="listen port")
    parser.add_argument(
        "--accept-configure",
        action="store_true",
        help="respond success to ConfigureVideoRequest",
    )
    parser.add_argument(
        "--report-hardware-active",
        action="store_true",
        help="set hardware_encoder_active=1 in ConfigureVideoResponse",
    )
    parser.add_argument(
        "--enforce-hw-hevc",
        action="store_true",
        help="run local HEVC VideoToolbox hardware probe during configure",
    )
    parser.add_argument(
        "--ring-path",
        default="/tmp/alvr-vtbridge-ring.bin",
        help="path to ring mmap file",
    )
    parser.add_argument(
        "--force-codec",
        choices=["hevc", "h264"],
        default="hevc",
        help="encoded codec emitted over EncodedNal",
    )
    parser.add_argument(
        "--force-test-pattern-hevc",
        action="store_true",
        help="always send a static libx265 HEVC test pattern frame",
    )
    parser.add_argument(
        "--debug-dump-dir",
        default="",
        help="optional directory for dumping sampled raw source frames as PNG",
    )
    parser.add_argument(
        "--debug-dump-limit",
        type=int,
        default=0,
        help="maximum number of unique-sample source frame dumps (0 disables)",
    )
    parser.add_argument(
        "--native-window-capture-title-contains",
        default="",
        help="comma-separated macOS window title filters for native ScreenCaptureKit fallback",
    )
    parser.add_argument(
        "--native-window-capture-owner-contains",
        default="",
        help="comma-separated macOS window owner filters for native ScreenCaptureKit fallback",
    )
    parser.add_argument(
        "--native-window-capture-fps",
        type=int,
        default=15,
        help="target fps for the native ScreenCaptureKit fallback helper",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = ServerConfig(
        bind_host=args.bind,
        port=args.port,
        accept_configure=args.accept_configure,
        require_hardware=args.report_hardware_active,
        enforce_hw_hevc=args.enforce_hw_hevc,
        ring_path=args.ring_path,
        force_codec=args.force_codec,
        force_test_pattern_hevc=args.force_test_pattern_hevc,
        debug_dump_dir=args.debug_dump_dir,
        debug_dump_limit=max(0, args.debug_dump_limit),
        native_window_capture_title_filters=[
            part.strip()
            for part in args.native_window_capture_title_contains.split(",")
            if part.strip()
        ],
        native_window_capture_owner_filters=[
            part.strip()
            for part in args.native_window_capture_owner_contains.split(",")
            if part.strip()
        ],
        native_window_capture_fps=max(1, args.native_window_capture_fps),
    )
    try:
        return run_server(cfg)
    except KeyboardInterrupt:
        log("stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
