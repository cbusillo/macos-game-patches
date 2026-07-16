#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_root="$repo_root/.code/probes/009-production-iosurface-pool"
run_dir="$run_root/wine-mach-right-$timestamp"
build_dir="$run_dir/build"
bridge_root="$run_dir/bridge"
wine_source="$repo_root/.code/vendor/crossover-26.2.0/source/sources/wine"
wine_build="$repo_root/.code/vendor/crossover-26.2.0/build"
bridge_build="$wine_build/dlls/alvr_iosurface_bridge"
bottle_probe="$HOME/Library/Application Support/CrossOver/Bottles/Steam/drive_c/alvr-probes"
cxstart="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart"
server_binary="$build_dir/iosurface-mach-server"
client_binary="$build_dir/wine_iosurface_mach_client_probe.exe"
staged_client="$bottle_probe/wine_iosurface_mach_client_probe.exe"
staged_bridge="$bottle_probe/alvr_iosurface_bridge.dll"
service_name="com.alvr.handoff.wine-probe.$UID.$$"
nonce="$(date +%s)$$"
server_pid=""

cleanup() {
	local exit_status=$?

	if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
		kill "$server_pid" 2>/dev/null || true
		wait "$server_pid" 2>/dev/null || true
	fi
	rm -f "$staged_client" "$staged_bridge"
	exit "$exit_status"
}
trap cleanup EXIT INT TERM

for path in "$wine_source" "$wine_build" "$cxstart"; do
	if [[ ! -e "$path" ]]; then
		echo "missing prerequisite: $path" >&2
		exit 1
	fi
done
for path in "$staged_client" "$staged_bridge"; do
	if [[ -e "$path" ]]; then
		echo "staging target already exists: $path" >&2
		exit 1
	fi
done

mkdir -p \
	"$build_dir" \
	"$bridge_root/x86_64-windows" \
	"$bridge_root/x86_64-unix" \
	"$bottle_probe"

clang -arch arm64 \
	-std=c11 -Wall -Wextra -Wpedantic \
	-I "$repo_root/tools" \
	"$repo_root/tools/iosurface_mach_port_probe.c" \
	-framework CoreFoundation -framework IOSurface \
	-o "$server_binary"

x86_64-w64-mingw32-gcc \
	-std=c11 -Wall -Wextra -Werror -O2 \
	"$repo_root/tools/wine_iosurface_mach_client_probe.c" \
	-o "$client_binary"

rsync -a --delete \
	"$repo_root/tools/alvr_iosurface_bridge/" \
	"$wine_source/dlls/alvr_iosurface_bridge/"
arch -x86_64 make -C "$wine_build" -j8 \
	dlls/alvr_iosurface_bridge/x86_64-windows/alvr_iosurface_bridge.dll \
	dlls/alvr_iosurface_bridge/alvr_iosurface_bridge.so \
	>"$run_dir/bridge-build.log" 2>&1

cp "$bridge_build/x86_64-windows/alvr_iosurface_bridge.dll" \
	"$bridge_root/x86_64-windows/"
cp "$bridge_build/alvr_iosurface_bridge.so" \
	"$bridge_root/x86_64-unix/"
cp "$client_binary" "$staged_client"
cp "$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll" "$staged_bridge"

{
	printf 'timestamp=%s\n' "$timestamp"
	printf 'service_name=%s\n' "$service_name"
	printf 'nonce=%s\n' "$nonce"
	file "$server_binary"
	file "$client_binary"
	file "$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll"
	file "$bridge_root/x86_64-unix/alvr_iosurface_bridge.so"
} >"$run_dir/run.info"

{
	printf 'git_head=%s\n' "$(git -C "$repo_root" rev-parse HEAD)"
	printf '%s\n' 'git_status_begin'
	git -C "$repo_root" status --short --untracked-files=all
	printf '%s\n' 'git_status_end'
	shasum -a 256 \
		"$repo_root/tools/iosurface_mach_port_probe.c" \
		"$repo_root/tools/wine_iosurface_mach_client_probe.c" \
		"$repo_root/tools/alvr_iosurface_bridge/iosurface_handoff_protocol.h" \
		"$repo_root/tools/alvr_iosurface_bridge/bridge.c" \
		"$repo_root/tools/alvr_iosurface_bridge/unixlib.c" \
		"$repo_root/tools/alvr_iosurface_bridge/unixlib.h" \
		"$repo_root/tools/alvr_iosurface_bridge/alvr_iosurface_bridge.spec" \
		"$repo_root/tools/run_wine_iosurface_mach_port_probe.sh" \
		"$server_binary" \
		"$client_binary" \
		"$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll" \
		"$bridge_root/x86_64-unix/alvr_iosurface_bridge.so"
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
"$cxstart" \
	--bottle Steam \
	--no-update \
	--no-gui \
	--wait \
	--env "WINEDLLPATH=$bridge_root WINEDEBUG=-all" \
	'C:\alvr-probes\wine_iosurface_mach_client_probe.exe' \
	"$service_name" \
	"$nonce" \
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
