"""Diagnose, inspect, install, uninstall, and stop the Mac ALVR runtime."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, NoReturn

from runtime_control import (
    DEFAULT_BINDINGS,
    ControlError,
    DoctorReport,
    RuntimeContext,
    StatusReport,
    StopReport,
    doctor_runtime,
    status_runtime,
    stop_runtime,
)
from runtime_install import MutationReport, install_runtime, uninstall_runtime


_JSON_ERRORS = False


class RuntimeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        if _JSON_ERRORS:
            payload = {
                "schemaVersion": 1,
                "ok": False,
                "error": {
                    "code": "usage.error",
                    "message": message,
                    "context": {},
                },
            }
            print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
            raise SystemExit(2)
        super().error(message)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bindings",
        type=pathlib.Path,
        default=DEFAULT_BINDINGS,
        help="runtime plan bindings JSON (defaults are used when the file is absent)",
    )
    parser.add_argument("--json", action="store_true", help="emit stable machine-readable JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = RuntimeArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="validate the artifact and host prerequisites")
    add_common_arguments(doctor_parser)
    doctor_parser.add_argument("--artifact", required=True, type=pathlib.Path, help="sealed runtime artifact")

    status_parser = subparsers.add_parser("status", help="report synchronized live runtime state")
    add_common_arguments(status_parser)
    status_parser.add_argument(
        "--artifact",
        type=pathlib.Path,
        help="sealed artifact used to distinguish installed from ready",
    )

    stop_parser = subparsers.add_parser("stop", help="idempotently remove exact owned runtime state")
    add_common_arguments(stop_parser)

    install_parser = subparsers.add_parser(
        "install",
        help="transactionally install a verified runtime artifact",
    )
    add_common_arguments(install_parser)
    install_parser.add_argument("--artifact", required=True, type=pathlib.Path, help="runtime artifact")

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="transactionally restore and remove a verified runtime artifact",
    )
    add_common_arguments(uninstall_parser)
    uninstall_parser.add_argument("--artifact", required=True, type=pathlib.Path, help="runtime artifact")
    return parser


def render_doctor(report: DoctorReport) -> str:
    lines = [f"doctor={'pass' if report.ok else 'fail'}"]
    for check in report.checks:
        lines.append(f"[{check.status.upper()}] {check.id}: {check.message}")
        if check.status != "pass":
            lines.append(f"  remediation: {check.remediation}")
    summary = report.to_dict()["summary"]
    lines.append(
        f"summary pass={summary['pass']} fail={summary['fail']} unknown={summary['unknown']}"
    )
    return "\n".join(lines)


def render_status(report: StatusReport) -> str:
    lines = [
        f"state={report.state}",
        f"reason={report.reason_code}",
        f"message={report.message}",
    ]
    if report.artifact:
        if report.artifact.get("sealId"):
            lines.append(f"artifact_seal={report.artifact['sealId']}")
        if report.artifact.get("path"):
            lines.append(f"artifact_path={report.artifact['path']}")
    lines.extend(
        [
            f"service_present={str(bool(report.service.get('present'))).lower()}",
            f"service_owned={str(bool(report.service.get('owned'))).lower()}",
            f"service_pid={report.service.get('pid') or ''}",
            f"owner_pid={report.owner.get('pid') or ''}",
            f"owner_alive={str(bool(report.owner.get('alive'))).lower()}",
        ]
    )
    for check in report.diagnostics:
        if check.status != "pass":
            lines.append(f"[{check.status.upper()}] {check.id}: {check.message}")
    return "\n".join(lines)


def render_stop(report: StopReport) -> str:
    lines = [
        f"state={report.state}",
        f"reason={report.reason_code}",
        f"message={report.message}",
    ]
    for action in report.actions:
        lines.append(f"action={action}")
    return "\n".join(lines)


def render_mutation(report: MutationReport) -> str:
    lines = [
        f"state={report.state}",
        f"reason={report.reason_code}",
        f"message={report.message}",
    ]
    if report.artifact is not None:
        if report.artifact.get("sealId"):
            lines.append(f"artifact_seal={report.artifact['sealId']}")
        if report.artifact.get("path"):
            lines.append(f"artifact_path={report.artifact['path']}")
    if report.transaction_id:
        lines.append(f"transaction_id={report.transaction_id}")
    if report.plan_digest:
        lines.append(f"plan_digest={report.plan_digest}")
    if report.journal:
        lines.append(f"journal={report.journal}")
    if report.archived_journal:
        lines.append(f"archived_journal={report.archived_journal}")
    for blocker in report.blockers:
        lines.append(f"blocker={blocker.get('id')}: {blocker.get('reason')}")
    for action in report.stop_actions:
        lines.append(f"stop_action={action}")
    for operation in report.applied:
        lines.append(f"applied={operation}")
    for operation in report.rolled_back:
        lines.append(f"rolled_back={operation}")
    for failure in report.cleanup_failures:
        lines.append(f"cleanup_failure={failure}")
    return "\n".join(lines)


def emit(value: dict[str, Any], *, as_json: bool, text: str, stream: Any = None) -> None:
    if stream is None:
        stream = sys.stdout
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True), file=stream)
    else:
        print(text, file=stream)


def error_payload(code: str, message: str, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "context": context,
        },
    }


def main(argv: list[str] | None = None) -> int:
    global _JSON_ERRORS
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    _JSON_ERRORS = "--json" in effective_argv
    parser = build_parser()
    arguments = parser.parse_args(effective_argv)
    context = RuntimeContext(bindings_path=arguments.bindings)
    try:
        if arguments.command == "doctor":
            doctor_report = doctor_runtime(context, arguments.artifact)
            emit(
                doctor_report.to_dict(),
                as_json=arguments.json,
                text=render_doctor(doctor_report),
            )
            return 0 if doctor_report.ok else 1
        if arguments.command == "status":
            status_report = status_runtime(context, arguments.artifact)
            emit(
                status_report.to_dict(),
                as_json=arguments.json,
                text=render_status(status_report),
            )
            return 0 if status_report.ok else 1
        if arguments.command == "install":
            install_report = install_runtime(context, arguments.artifact)
            emit(
                install_report.to_dict(),
                as_json=arguments.json,
                text=render_mutation(install_report),
            )
            return 0 if install_report.ok else 1
        if arguments.command == "uninstall":
            uninstall_report = uninstall_runtime(context, arguments.artifact)
            emit(
                uninstall_report.to_dict(),
                as_json=arguments.json,
                text=render_mutation(uninstall_report),
            )
            return 0 if uninstall_report.ok else 1
        stop_report = stop_runtime(context)
        emit(stop_report.to_dict(), as_json=arguments.json, text=render_stop(stop_report))
        return 0 if stop_report.ok else 1
    except ControlError as error:
        payload = error_payload(error.code, error.message, error.context)
        emit(
            payload,
            as_json=arguments.json,
            text=f"error={error.code}: {error.message}",
            stream=sys.stderr,
        )
        return 2
    except Exception as error:
        payload = error_payload(
            "internal.error",
            "Unexpected runtime control-plane failure",
            {"type": type(error).__name__},
        )
        emit(
            payload,
            as_json=arguments.json,
            text="error=internal.error: Unexpected runtime control-plane failure",
            stream=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
