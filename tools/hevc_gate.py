#!/usr/bin/env python3

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class GateConfig:
    duration_seconds: int
    width: int
    height: int
    fps: int
    bitrate_mbps: int


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def require_executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"Missing required executable: {name}")
    return path


def run_dir(base_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    result = base_dir / f"{stamp}-h0-hevc-videotoolbox"
    (result / "logs").mkdir(parents=True, exist_ok=True)
    return result


def ffmpeg_command(ffmpeg: str, output_file: Path, cfg: GateConfig) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={cfg.width}x{cfg.height}:rate={cfg.fps}",
        "-t",
        str(cfg.duration_seconds),
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
        f"{cfg.bitrate_mbps}M",
        str(output_file),
    ]


def ffprobe_command(ffprobe: str, output_file: Path) -> list[str]:
    return [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_name,codec_tag_string,avg_frame_rate,width,height",
        "-of",
        "json",
        str(output_file),
    ]


def parse_probe_fps(value: str) -> float:
    return float(Fraction(value))


def evaluate(ffmpeg_log_text: str, ffprobe_data: dict, cfg: GateConfig) -> tuple[bool, list[str]]:
    errors: list[str] = []
    lowered = ffmpeg_log_text.lower()
    if "hevc_videotoolbox" not in lowered:
        errors.append("ffmpeg log missing hevc_videotoolbox marker")

    fallback_markers = [
        "software fallback",
        "using software",
        "fallback to software",
    ]
    for marker in fallback_markers:
        if marker in lowered:
            errors.append(f"ffmpeg log contains fallback marker: {marker}")

    streams = ffprobe_data.get("streams", [])
    if not streams:
        errors.append("ffprobe returned no video streams")
        return False, errors

    stream = streams[0]
    codec_name = stream.get("codec_name")
    width = stream.get("width")
    height = stream.get("height")
    fps = stream.get("avg_frame_rate", "0/1")

    if codec_name != "hevc":
        errors.append(f"unexpected codec_name: {codec_name}")
    if int(width) != cfg.width:
        errors.append(f"unexpected width: {width}")
    if int(height) != cfg.height:
        errors.append(f"unexpected height: {height}")
    if abs(parse_probe_fps(fps) - cfg.fps) > 0.01:
        errors.append(f"unexpected fps: {fps}")

    return len(errors) == 0, errors


def run_gate(base_dir: Path, cfg: GateConfig) -> int:
    ffmpeg = require_executable("ffmpeg")
    ffprobe = require_executable("ffprobe")

    gate_dir = run_dir(base_dir)
    output_file = gate_dir / "h0-hevc-videotoolbox.mp4"
    ffmpeg_log_path = gate_dir / "logs" / "h0-ffmpeg.log"
    ffprobe_json_path = gate_dir / "logs" / "h0-ffprobe.json"
    summary_json_path = gate_dir / "logs" / "h0-summary.json"

    ffmpeg_result = subprocess.run(
        ffmpeg_command(ffmpeg, output_file, cfg),
        capture_output=True,
        text=True,
        check=False,
    )
    ffmpeg_log_text = (ffmpeg_result.stdout or "") + (ffmpeg_result.stderr or "")
    ffmpeg_log_path.write_text(ffmpeg_log_text, encoding="utf-8")

    if ffmpeg_result.returncode != 0:
        summary_json_path.write_text(
            json.dumps(
                {
                    "pass": False,
                    "reason": "ffmpeg failed",
                    "return_code": ffmpeg_result.returncode,
                    "run_dir": str(gate_dir),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"FAIL: ffmpeg exited with code {ffmpeg_result.returncode}")
        print(f"Run bundle: {gate_dir}")
        return 1

    ffprobe_result = subprocess.run(
        ffprobe_command(ffprobe, output_file),
        capture_output=True,
        text=True,
        check=False,
    )
    if ffprobe_result.returncode != 0:
        summary_json_path.write_text(
            json.dumps(
                {
                    "pass": False,
                    "reason": "ffprobe failed",
                    "return_code": ffprobe_result.returncode,
                    "run_dir": str(gate_dir),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"FAIL: ffprobe exited with code {ffprobe_result.returncode}")
        print(f"Run bundle: {gate_dir}")
        return 1

    ffprobe_json_path.write_text(ffprobe_result.stdout, encoding="utf-8")
    ffprobe_data = json.loads(ffprobe_result.stdout)
    passed, errors = evaluate(ffmpeg_log_text, ffprobe_data, cfg)

    summary_json_path.write_text(
        json.dumps(
            {
                "pass": passed,
                "errors": errors,
                "config": {
                    "duration_seconds": cfg.duration_seconds,
                    "width": cfg.width,
                    "height": cfg.height,
                    "fps": cfg.fps,
                    "bitrate_mbps": cfg.bitrate_mbps,
                },
                "run_dir": str(gate_dir),
                "output_file": str(output_file),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if not passed:
        print("FAIL: hardware HEVC gate did not pass")
        for error in errors:
            print(f"- {error}")
        print(f"Run bundle: {gate_dir}")
        return 1

    print("PASS: HEVC VideoToolbox hardware gate")
    print(f"Run bundle: {gate_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run macOS HEVC hardware gate (VideoToolbox only)")
    parser.add_argument("--duration", type=int, default=12, help="test duration in seconds")
    parser.add_argument("--width", type=int, default=3840, help="encode width")
    parser.add_argument("--height", type=int, default=2160, help="encode height")
    parser.add_argument("--fps", type=int, default=90, help="target frame rate")
    parser.add_argument("--bitrate-mbps", type=int, default=40, help="target bitrate in Mbps")
    parser.add_argument(
        "--run-root",
        default=str(repo_root() / "temp" / "vr_runs"),
        help="directory where run bundles are created",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = GateConfig(
        duration_seconds=args.duration,
        width=args.width,
        height=args.height,
        fps=args.fps,
        bitrate_mbps=args.bitrate_mbps,
    )
    return run_gate(Path(args.run_root), cfg)


if __name__ == "__main__":
    sys.exit(main())
