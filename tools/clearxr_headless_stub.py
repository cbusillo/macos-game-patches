#!/usr/bin/env python3

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any

SERVICE_TYPE = "_apple-foveated-streaming._tcp"
SERVICE_DOMAIN = "local."
BUNDLE_ID_KEY = "Application-Identifier"
SUPPORTED_PROTOCOL_VERSION = "1"
SESSION_STATUS_WAITING = "WAITING"
SESSION_STATUS_DISCONNECTED = "DISCONNECTED"
DEFAULT_ARCHIVE_ROOT = Path("temp/clearxr_stub_runs")


def hostname_label() -> str:
    for key in ("COMPUTERNAME", "HOSTNAME"):
        value = os_getenv_trimmed(key)
        if value:
            return value[:30]
    return "clearxr-stub"


def os_getenv_trimmed(key: str) -> str | None:
    value = os.environ.get(key, "").strip()
    return value or None


def detect_host_address() -> str:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
            if address and not address.startswith("127."):
                return address
        except OSError:
            pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if isinstance(address, str) and address and not address.startswith("127."):
                return address
    except OSError:
        pass

    return "127.0.0.1"


def make_barcode(client_id: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:12]
    return (f"stub-token-{suffix}", f"stub-sha256-{client_id}-{suffix}")


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(4)
    length = int.from_bytes(header, "little")
    return await reader.readexactly(length)


async def write_frame(writer: asyncio.StreamWriter, payload: bytes) -> None:
    writer.write(len(payload).to_bytes(4, "little"))
    writer.write(payload)
    await writer.drain()


@dataclass
class ActiveSession:
    session_id: str
    client_id: str
    client_token: str
    certificate_fingerprint: str
    peer_name: str


class TranscriptRecorder:
    def __init__(self, archive_root: Path, service_name: str) -> None:
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_service_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in service_name)
        self.run_dir = archive_root / f"{timestamp}-{safe_service_name}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.summary_path = self.run_dir / "summary.json"
        self.metadata_path = self.run_dir / "metadata.json"
        self.started_at = dt.datetime.now(dt.timezone.utc)
        self.event_count = 0
        self.last_session_id: str | None = None
        self._metadata: dict[str, Any] = {}

    def write_metadata(self, metadata: dict[str, Any]) -> None:
        self._metadata = dict(metadata)
        self.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._write_summary(ended_at=None)

    def append_event(self, record: dict[str, Any]) -> None:
        if record.get("session_id"):
            self.last_session_id = str(record["session_id"])
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        self.event_count += 1
        self._write_summary(ended_at=None)

    def write_summary(self, metadata: dict[str, Any]) -> None:
        self._metadata = dict(metadata)
        self._write_summary(ended_at=dt.datetime.now(dt.timezone.utc))

    def _write_summary(self, ended_at: dt.datetime | None) -> None:
        current_time = ended_at or dt.datetime.now(dt.timezone.utc)
        summary = {
            "archive_run_dir": str(self.run_dir),
            "started_at_utc": self.started_at.isoformat(),
            "ended_at_utc": ended_at.isoformat() if ended_at else None,
            "duration_seconds": round((current_time - self.started_at).total_seconds(), 3),
            "event_count": self.event_count,
            "last_session_id": self.last_session_id,
            "config": self._metadata,
        }
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ClearXRHeadlessStub:
    def __init__(
        self,
        *,
        bind_host: str,
        advertised_host: str,
        port: int,
        bundle_id: str,
        service_name: str,
        server_id: str,
        force_qr_code: bool,
        send_media_ready_on_waiting: bool,
        enable_bonjour: bool,
        archive_root: Path,
    ) -> None:
        self.bind_host = bind_host
        self.advertised_host = advertised_host
        self.port = port
        self.bundle_id = bundle_id
        self.service_name = service_name
        self.server_id = server_id
        self.force_qr_code = force_qr_code
        self.send_media_ready_on_waiting = send_media_ready_on_waiting
        self.enable_bonjour = enable_bonjour
        self.transcript = TranscriptRecorder(archive_root, service_name)
        self._bonjour_process: subprocess.Popen[str] | None = None
        self._server: asyncio.AbstractServer | None = None
        self._active_session: ActiveSession | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "advertised_host": self.advertised_host,
            "bind_host": self.bind_host,
            "bundle_id": self.bundle_id,
            "bonjour_enabled": self.enable_bonjour,
            "force_qr_code": self.force_qr_code,
            "port": self.port,
            "send_media_ready_on_waiting": self.send_media_ready_on_waiting,
            "server_id": self.server_id,
            "service_name": self.service_name,
        }

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.bind_host,
            port=self.port,
        )
        if self._server.sockets:
            self.port = int(self._server.sockets[0].getsockname()[1])

        if self.enable_bonjour:
            self._start_bonjour()

        self.transcript.write_metadata(self.metadata())

        self._log(
            "stub_ready",
            bind_host=self.bind_host,
            advertised_host=self.advertised_host,
            port=self.port,
            service_name=self.service_name,
            bundle_id=self.bundle_id,
            server_id=self.server_id,
            bonjour=self.enable_bonjour,
        )

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._bonjour_process is not None:
            with contextlib.suppress(ProcessLookupError):
                self._bonjour_process.terminate()
            try:
                self._bonjour_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    self._bonjour_process.kill()
                self._bonjour_process.wait(timeout=3)
            self._bonjour_process = None

        self.transcript.write_summary(self.metadata())

    def _start_bonjour(self) -> None:
        txt_record = f"{BUNDLE_ID_KEY}={self.bundle_id}"
        command = [
            "dns-sd",
            "-R",
            self.service_name,
            SERVICE_TYPE,
            SERVICE_DOMAIN,
            str(self.port),
            txt_record,
        ]
        self._bonjour_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._log("bonjour_started", command=command, pid=self._bonjour_process.pid)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        peer_name = str(peer) if peer else "unknown"
        self._log("client_connected", peer=peer_name)

        try:
            while True:
                payload = await read_frame(reader)
                try:
                    message = json.loads(payload)
                except json.JSONDecodeError as error:
                    self._log("invalid_json", peer=peer_name, error=str(error))
                    continue

                responses = self._process_message(message, peer_name)
                for response in responses:
                    await write_frame(writer, json.dumps(response).encode("utf-8"))
                    self._log(
                        "sent",
                        peer=peer_name,
                        response_event=str(response.get("Event", "unknown")),
                    )
        except asyncio.IncompleteReadError:
            self._log("client_disconnected", peer=peer_name)
        finally:
            if self._active_session and self._active_session.peer_name == peer_name:
                self._log(
                    "session_cleared",
                    session_id=self._active_session.session_id,
                    reason="connection_closed",
                )
                self._active_session = None

            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _process_message(self, message: dict[str, object], peer_name: str) -> list[dict[str, object]]:
        event = str(message.get("Event", ""))
        session_id = str(message.get("SessionID", ""))
        responses: list[dict[str, object]] = []

        self._log("received", peer=peer_name, message_event=event, session_id=session_id or None)

        if event == "RequestConnection":
            protocol_version = str(message.get("ProtocolVersion", ""))
            client_id = str(message.get("ClientID", "unknown-client"))
            if protocol_version != SUPPORTED_PROTOCOL_VERSION:
                self._log(
                    "protocol_rejected",
                    peer=peer_name,
                    session_id=session_id,
                    protocol_version=protocol_version,
                )
                return [self._disconnect_message(session_id)]

            if self._active_session and self._active_session.session_id != session_id:
                self._log(
                    "session_rejected",
                    peer=peer_name,
                    session_id=session_id,
                    active_session_id=self._active_session.session_id,
                )
                return [self._disconnect_message(session_id)]

            client_token, certificate_fingerprint = make_barcode(client_id)
            self._active_session = ActiveSession(
                session_id=session_id,
                client_id=client_id,
                client_token=client_token,
                certificate_fingerprint=certificate_fingerprint,
                peer_name=peer_name,
            )
            responses.append(
                {
                    "Event": "AcknowledgeConnection",
                    "SessionID": session_id,
                    "ServerID": self.server_id,
                    "CertificateFingerprint": None
                    if self.force_qr_code
                    else certificate_fingerprint,
                }
            )
            return responses

        if not self._active_session or self._active_session.session_id != session_id:
            self._log(
                "session_unknown",
                peer=peer_name,
                message_event=event,
                session_id=session_id,
            )
            return [self._disconnect_message(session_id)]

        if event == "RequestBarcodePresentation":
            responses.append(
                {
                    "Event": "AcknowledgeBarcodePresentation",
                    "SessionID": session_id,
                }
            )
            return responses

        if event == "SessionStatusDidChange":
            status = str(message.get("Status", ""))
            self._log("status_changed", session_id=session_id, status=status)
            if status == SESSION_STATUS_WAITING and self.send_media_ready_on_waiting:
                responses.append(
                    {
                        "Event": "MediaStreamIsReady",
                        "SessionID": session_id,
                    }
                )
            if status == SESSION_STATUS_DISCONNECTED:
                self._active_session = None
            return responses

        return responses

    @staticmethod
    def _disconnect_message(session_id: str) -> dict[str, object]:
        return {
            "Event": "RequestSessionDisconnect",
            "SessionID": session_id,
        }

    def _log(self, event: str, **fields: object) -> None:
        record = {
            "event": event,
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            **fields,
        }
        self.transcript.append_event(record)
        print(json.dumps(record, sort_keys=True), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a headless ClearXR-compatible Bonjour and session stub on macOS"
    )
    parser.add_argument("--bind-host", default="0.0.0.0", help="local interface to bind")
    parser.add_argument(
        "--advertised-host",
        default=detect_host_address(),
        help="host/IP to display for manual client entry",
    )
    parser.add_argument("--port", type=int, default=55000, help="session-management port")
    parser.add_argument("--bundle-id", default="app.clearxr.client", help="Bonjour bundle identifier")
    parser.add_argument("--service-name", default=hostname_label(), help="Bonjour service instance name")
    parser.add_argument("--server-id", default=f"stub-{uuid.uuid4()}", help="server id returned to clients")
    parser.add_argument("--force-qr-code", action="store_true", help="omit certificate fingerprint")
    parser.add_argument(
        "--archive-root",
        default=str(DEFAULT_ARCHIVE_ROOT),
        help="directory where run bundles will be written",
    )
    parser.add_argument(
        "--no-media-ready-on-waiting",
        action="store_true",
        help="do not reply with MediaStreamIsReady after WAITING",
    )
    parser.add_argument("--no-bonjour", action="store_true", help="skip Bonjour advertisement")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    stub = ClearXRHeadlessStub(
        bind_host=args.bind_host,
        advertised_host=args.advertised_host,
        port=args.port,
        bundle_id=args.bundle_id,
        service_name=args.service_name,
        server_id=args.server_id,
        force_qr_code=args.force_qr_code,
        send_media_ready_on_waiting=not args.no_media_ready_on_waiting,
        enable_bonjour=not args.no_bonjour,
        archive_root=Path(args.archive_root),
    )
    await stub.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        await stub.close()
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
