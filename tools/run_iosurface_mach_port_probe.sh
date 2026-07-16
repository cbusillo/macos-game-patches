#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_root="$repo_root/.code/probes/009-production-iosurface-pool"
run_dir="$run_root/mach-right-$timestamp"
build_dir="$run_dir/build"
server_binary="$build_dir/iosurface-mach-server"
client_binary="$build_dir/iosurface-mach-client"
service_name="com.alvr.handoff.probe.$UID.$$"
nonce="$(date +%s)$$"
server_pid=""

cleanup() {
	local exit_status=$?

	if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
		kill "$server_pid" 2>/dev/null || true
		wait "$server_pid" 2>/dev/null || true
	fi
	exit "$exit_status"
}
trap cleanup EXIT INT TERM

mkdir -p "$build_dir"

common_flags=(
	-std=c11
	-Wall
	-Wextra
	-Wpedantic
	-I "$repo_root/tools"
	"$repo_root/tools/iosurface_mach_port_probe.c"
	-framework CoreFoundation
	-framework IOSurface
)

clang -arch arm64 "${common_flags[@]}" -o "$server_binary"
clang -arch x86_64 "${common_flags[@]}" -o "$client_binary"

{
	printf 'timestamp=%s\n' "$timestamp"
	printf 'service_name=%s\n' "$service_name"
	printf 'nonce=%s\n' "$nonce"
	file "$server_binary"
	file "$client_binary"
} >"$run_dir/run.info"

{
	printf 'git_head=%s\n' "$(git -C "$repo_root" rev-parse HEAD)"
	printf '%s\n' 'git_status_begin'
	git -C "$repo_root" status --short --untracked-files=all
	printf '%s\n' 'git_status_end'
	shasum -a 256 \
		"$repo_root/tools/iosurface_mach_port_probe.c" \
		"$repo_root/tools/alvr_iosurface_bridge/iosurface_handoff_protocol.h" \
		"$repo_root/tools/run_iosurface_mach_port_probe.sh" \
		"$server_binary" \
		"$client_binary"
} >"$run_dir/source-state.txt"

"$server_binary" server "$service_name" "$nonce" \
	>"$run_dir/server.log" 2>&1 &
server_pid=$!

for _ in {1..100}; do
	if rg -q 'registered=' "$run_dir/server.log" 2>/dev/null; then
		break
	fi
	if ! kill -0 "$server_pid" 2>/dev/null; then
		cat "$run_dir/server.log" >&2
		exit 1
	fi
	sleep 0.05
done

if ! rg -q 'registered=' "$run_dir/server.log"; then
	echo "server did not register within timeout" >&2
	exit 1
fi

set +e
"$client_binary" client "$service_name" "$nonce" \
	>"$run_dir/client.log" 2>&1
client_status=$?
wait "$server_pid"
server_status=$?
server_pid=""
set -e

{
	printf 'client_status=%d\n' "$client_status"
	printf 'server_status=%d\n' "$server_status"
} >"$run_dir/status.txt"

if [[ $client_status -eq 0 && $server_status -eq 0 ]] &&
	rg -q 'result=pass' "$run_dir/client.log" &&
	rg -q 'result=pass' "$run_dir/server.log"; then
	echo pass >"$run_dir/verdict.txt"
else
	echo fail >"$run_dir/verdict.txt"
	cat "$run_dir/server.log" >&2
	cat "$run_dir/client.log" >&2
	exit 1
fi

printf 'PROBE run=%s result=pass\n' "$run_dir"
