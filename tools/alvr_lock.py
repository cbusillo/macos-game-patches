#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def gather_lock(alvr_dir: Path) -> dict[str, object]:
    head = run(["git", "rev-parse", "HEAD"], cwd=alvr_dir)
    branch = run(["git", "branch", "--show-current"], cwd=alvr_dir)
    summary = run(["git", "show", "-s", "--format=%s", "HEAD"], cwd=alvr_dir)
    date = run(["git", "show", "-s", "--format=%cI", "HEAD"], cwd=alvr_dir)

    remotes_output = run(["git", "remote", "-v"], cwd=alvr_dir).splitlines()
    remotes: dict[str, dict[str, str]] = {}
    for line in remotes_output:
        parts = line.split()
        if len(parts) != 3:
            continue
        remote_name, remote_url, remote_kind = parts
        remote_kind = remote_kind.strip("()")
        remotes.setdefault(remote_name, {})[remote_kind] = remote_url

    upstream_head = ""
    if "upstream" in remotes:
        try:
            upstream_head = run(["git", "rev-parse", "upstream/master"], cwd=alvr_dir)
        except subprocess.CalledProcessError:
            upstream_head = ""

    return {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "alvr_dir": str(alvr_dir),
        "head": head,
        "branch": branch,
        "commit_date": date,
        "summary": summary,
        "upstream_master_head": upstream_head,
        "remotes": remotes,
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(description="Capture local ALVR fork revision lock")
    parser.add_argument("--alvr-dir", default=str(Path.home() / "Developer" / "ALVR"))
    parser.add_argument("--out", default=str(repo_root / "docs" / "alvr-lock.json"))
    args = parser.parse_args()

    alvr_dir = Path(args.alvr_dir).expanduser().resolve()
    out_path = Path(args.out).resolve()

    if not alvr_dir.exists():
        print(f"ERROR: ALVR directory not found: {alvr_dir}")
        return 1

    try:
        lock = gather_lock(alvr_dir)
    except subprocess.CalledProcessError as error:
        print("ERROR: failed to query ALVR git state")
        print(error)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote lock file: {out_path}")
    print(f"ALVR HEAD: {lock['head']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
