#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bottle_name=${CROSSOVER_BOTTLE_NAME:-Steam}
bottle=${CROSSOVER_BOTTLE:-"$HOME/Library/Application Support/CrossOver/Bottles/$bottle_name"}
crossover_app=${CROSSOVER_APP:-/Applications/CrossOver.app}
alvr_checkout=${ALVR_CHECKOUT:-"$HOME/Developer/alvr"}
alvr_bridge_root=${ALVR_BRIDGE_ROOT:-"$HOME/Library/Application Support/ALVR/macos_bridge"}
game_dir="$bottle/drive_c/Program Files (x86)/Steam/steamapps/common/Freedom Locomotion VR/FreedomLocomotion/Binaries/Win64"
engine_dir="$bottle/drive_c/Program Files (x86)/Steam/steamapps/common/Freedom Locomotion VR/Engine/Binaries/ThirdParty/OpenVR/OpenVRv1_0_2/Win64"
moltenvk="$crossover_app/Contents/SharedSupport/CrossOver/lib64/libMoltenVK.dylib"
patched_moltenvk="$repo/.code/vendor/crossover-26.2.0/source/sources/moltenvk/Package/Release/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"
dxvk_dir="$repo/.code/probes/008-real-openvr-iosurface/dxvk-d93568f1-build/src"
bridge_root="$repo/.code/probes/007-dxvk-d3d11-iosurface/bridge"
shim="$repo/.code/probes/008-real-openvr-iosurface/build/openvr_api.dll"
fake_runtime="$repo/.code/probes/008-real-openvr-iosurface/openvr_api.real.dll"
consumer="$repo/.code/probes/006-moltenvk-iosurface/moltenvk_iosurface_probe"
native_bridge=${ALVR_MACOS_BRIDGE:-"$alvr_checkout/target/release/alvr_macos_bridge"}
cxstart="$crossover_app/Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/cxstart"
probe_dir="$bottle/drive_c/alvr-probes"
ready_file="$probe_dir/real_submit_iosurface_ready.txt"
ready_temp_file="$probe_dir/real_submit_iosurface_ready.tmp"
done_file="$probe_dir/real_submit_iosurface_done.txt"
done_temp_file="$probe_dir/real_submit_iosurface_done.tmp"
shared_memory_file=/tmp/alvr_frame_buffer.shm
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_root=${PROBE_OUTPUT_ROOT:-"$repo/.code/probes/008-real-openvr-iosurface"}
run_dir="$run_root/dxvk-d93568f1-interop-vtable-real-$timestamp"
backup_dir="$run_dir/backups"
launcher_pid=
native_bridge_pid=
restored=0

stock_moltenvk_hash=${STOCK_MOLTENVK_HASH:-5c370edf330a126e4605aaf5cd156521197b0fdbd208b3e0a7931f3b8e6c5056}
stock_openvr_hash=${STOCK_OPENVR_HASH:-d793e2a76a61296dc5bce5e6b8dc32f4f3096743aba10c5bac2eb465e635850c}

hash_file() {
	shasum -a 256 "$1" | awk '{print $1}'
}

require_file() {
	if [[ ! -f $1 ]]; then
		echo "required file is missing: $1" >&2
		exit 1
	fi
}

manifest_value() {
	awk -F= -v key="$2" '$1 == key { print $2; found = 1 } END { exit found ? 0 : 1 }' "$1"
}

cleanup_sidecars() {
	rm -f "$ready_file" "$ready_temp_file" "$done_file" "$done_temp_file"
}

stop_game() {
	pkill -TERM -f '[F]reedomLocomotion-Win64-Shipping.exe' 2>/dev/null || true
	pkill -TERM -f '[F]reedomLocomotion.exe' 2>/dev/null || true
	sleep 1
	pkill -KILL -f '[F]reedomLocomotion-Win64-Shipping.exe' 2>/dev/null || true
	pkill -KILL -f '[F]reedomLocomotion.exe' 2>/dev/null || true
}

stop_native_bridge() {
	if [[ -z ${native_bridge_pid:-} ]]; then
		return
	fi
	kill -TERM "$native_bridge_pid" 2>/dev/null || true
	for _ in $(seq 1 50); do
		if ! kill -0 "$native_bridge_pid" 2>/dev/null; then
			break
		fi
		sleep 0.1
	done
	kill -KILL "$native_bridge_pid" 2>/dev/null || true
	wait "$native_bridge_pid" 2>/dev/null || true
}

shared_memory_ready() {
	python3 - "$shared_memory_file" <<'PY'
import struct
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    with path.open("rb") as file:
        data = file.read(88)
    magic, version, initialized, shutdown = struct.unpack_from("<IIII", data)
    session_id, heartbeat = struct.unpack_from("<QQ", data, 72)
except (OSError, struct.error):
    raise SystemExit(1)

ready = (
    magic == 0x414C5652
    and version == 5
    and initialized == 1
    and shutdown == 0
    and session_id != 0
    and heartbeat != 0
)
raise SystemExit(0 if ready else 1)
PY
}

archive_runtime_logs() {
	if [[ -f /tmp/alvr_openvr_submit_shim.log ]]; then
		cp -p /tmp/alvr_openvr_submit_shim.log "$run_dir/openvr-submit-shim.log"
	fi
	if [[ -f /tmp/fake_openvr_real.log ]]; then
		cp -p /tmp/fake_openvr_real.log "$run_dir/fake-openvr.log"
	fi
	if [[ -f "$alvr_bridge_root/session_log.txt" ]]; then
		cp -p "$alvr_bridge_root/session_log.txt" "$run_dir/cpu-bridge-session_log.txt"
	fi
	if [[ -f "$alvr_bridge_root/crash_log.txt" ]]; then
		cp -p "$alvr_bridge_root/crash_log.txt" "$run_dir/cpu-bridge-crash_log.txt"
	fi
	for log in "$game_dir"/*_d3d11.log "$game_dir"/*_dxgi.log; do
		if [[ -f $log ]]; then
			cp -p "$log" "$run_dir/"
		fi
	done
}

restore() {
	if [[ $restored -eq 1 ]]; then
		return 0
	fi
	restored=1
	set +e
	local cleanup_failed=0
	stop_game
	stop_native_bridge
	if [[ -f "$backup_dir/libMoltenVK.dylib" ]]; then
		cp -f "$backup_dir/libMoltenVK.dylib" "$moltenvk" || cleanup_failed=1
	fi
	if [[ -f "$backup_dir/openvr_api.dll" ]]; then
		cp -f "$backup_dir/openvr_api.dll" "$engine_dir/openvr_api.dll" || cleanup_failed=1
	fi
	rm -f "$engine_dir/openvr_api.real.dll"
	rm -f "$game_dir/d3d11.dll" "$game_dir/dxgi.dll" "$game_dir/alvr_iosurface_bridge.dll"
	rm -f "$game_dir"/*_d3d11.log "$game_dir"/*_dxgi.log
	rm -f /tmp/alvr_openvr_submit_shim.log /tmp/fake_openvr_real.log
	cleanup_sidecars || cleanup_failed=1
	rm -f "$shared_memory_file"
	if [[ -n ${launcher_pid:-} ]]; then
		wait "$launcher_pid" 2>/dev/null || true
	fi
	{
		if [[ -f $moltenvk ]]; then
			printf 'moltenvk=%s\n' "$(hash_file "$moltenvk")"
		else
			printf 'missing=%s\n' "$moltenvk"
			cleanup_failed=1
		fi
		if [[ -f "$engine_dir/openvr_api.dll" ]]; then
			printf 'openvr=%s\n' "$(hash_file "$engine_dir/openvr_api.dll")"
		else
			printf 'missing=%s\n' "$engine_dir/openvr_api.dll"
			cleanup_failed=1
		fi
		for path in \
			"$engine_dir/openvr_api.real.dll" \
			"$game_dir/d3d11.dll" \
			"$game_dir/dxgi.dll" \
			"$game_dir/alvr_iosurface_bridge.dll" \
			"$ready_file" \
			"$ready_temp_file" \
			"$done_file" \
			"$done_temp_file" \
			/tmp/alvr_openvr_submit_shim.log \
			/tmp/fake_openvr_real.log \
			"$shared_memory_file"; do
			if [[ -e $path ]]; then
				printf 'unexpected-present=%s\n' "$path"
				cleanup_failed=1
			else
				printf 'absent=%s\n' "$path"
			fi
		done
	} >"$run_dir/restored-state.txt"
	if [[ -f $moltenvk && $(hash_file "$moltenvk") != "$stock_moltenvk_hash" ]]; then
		cleanup_failed=1
	fi
	if [[ -f "$engine_dir/openvr_api.dll" && $(hash_file "$engine_dir/openvr_api.dll") != "$stock_openvr_hash" ]]; then
		cleanup_failed=1
	fi
	return "$cleanup_failed"
}

on_exit() {
	local status=$?
	trap - EXIT
	if ! restore; then
		status=1
	fi
	exit "$status"
}

trap on_exit EXIT

mkdir -p "$backup_dir" "$run_dir/mvk-shaders" "$probe_dir" "$alvr_bridge_root"

for file in \
	"$moltenvk" \
	"$patched_moltenvk" \
	"$dxvk_dir/d3d11/d3d11.dll" \
	"$dxvk_dir/dxgi/dxgi.dll" \
	"$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll" \
	"$shim" \
	"$fake_runtime" \
	"$consumer" \
	"$native_bridge" \
	"$cxstart" \
	"$engine_dir/openvr_api.dll"; do
	require_file "$file"
done

if pgrep -f '[F]reedomLocomotion' >/dev/null 2>&1; then
	echo "Freedom Locomotion is already running" >&2
	exit 1
fi
if pgrep -f '[a]lvr_macos_bridge' >/dev/null 2>&1; then
	echo "alvr_macos_bridge is already running" >&2
	exit 1
fi
if [[ $(hash_file "$moltenvk") != "$stock_moltenvk_hash" ]]; then
	echo "CrossOver MoltenVK is not pristine" >&2
	exit 1
fi
if [[ $(hash_file "$engine_dir/openvr_api.dll") != "$stock_openvr_hash" ]]; then
	echo "Freedom OpenVR DLL is not pristine" >&2
	exit 1
fi
for path in \
	"$engine_dir/openvr_api.real.dll" \
	"$game_dir/d3d11.dll" \
	"$game_dir/dxgi.dll" \
	"$game_dir/alvr_iosurface_bridge.dll"; do
	if [[ -e $path ]]; then
		echo "staging target already exists: $path" >&2
		exit 1
	fi
done

cleanup_sidecars
rm -f "$shared_memory_file" /tmp/alvr_openvr_submit_shim.log /tmp/fake_openvr_real.log

cp -p "$moltenvk" "$backup_dir/libMoltenVK.dylib"
cp -p "$engine_dir/openvr_api.dll" "$backup_dir/openvr_api.dll"

ALVR_BRIDGE_ROOT="$alvr_bridge_root" ALVR_BRIDGE_INPUT=shared-memory \
	"$native_bridge" >"$run_dir/cpu-bridge-stdout.log" 2>&1 &
native_bridge_pid=$!

bridge_ready=0
for _ in $(seq 1 150); do
	if shared_memory_ready; then
		bridge_ready=1
		break
	fi
	if ! kill -0 "$native_bridge_pid" 2>/dev/null; then
		break
	fi
	sleep 0.1
done
if [[ $bridge_ready -ne 1 ]]; then
	archive_runtime_logs
	echo "native shared-memory bridge did not initialize" >&2
	exit 1
fi

cp -f "$patched_moltenvk" "$moltenvk"
cp -f "$fake_runtime" "$engine_dir/openvr_api.real.dll"
cp -f "$shim" "$engine_dir/openvr_api.dll"
cp -f "$dxvk_dir/d3d11/d3d11.dll" "$game_dir/d3d11.dll"
cp -f "$dxvk_dir/dxgi/dxgi.dll" "$game_dir/dxgi.dll"
cp -f "$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll" "$game_dir/alvr_iosurface_bridge.dll"

{
	shasum -a 256 "$moltenvk"
	shasum -a 256 "$engine_dir/openvr_api.dll"
	shasum -a 256 "$engine_dir/openvr_api.real.dll"
	shasum -a 256 "$game_dir/d3d11.dll"
	shasum -a 256 "$game_dir/dxgi.dll"
	shasum -a 256 "$game_dir/alvr_iosurface_bridge.dll"
} >"$run_dir/staged-state.txt"

cx_env="CX_GRAPHICS_BACKEND=dxvk WINEDLLPATH=$bridge_root WINEDLLOVERRIDES=d3d11,dxgi=n DXVK_LOG_LEVEL=debug DXVK_STATE_CACHE=0 MVK_CONFIG_LOG_LEVEL=3 MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=0 MVK_CONFIG_SHADER_DUMP_DIR=$run_dir/mvk-shaders ALVR_SHIM_INNER_CROP_PX=224 WINEDEBUG=-all,+loaddll"
{
	printf 'run_dir=%s\n' "$run_dir"
	printf 'cx_env=%s\n' "$cx_env"
} >"$run_dir/run.info"

"$cxstart" --bottle "$bottle_name" --no-update --no-gui --wait --env "$cx_env" \
	'C:\Program Files (x86)\Steam\steamapps\common\Freedom Locomotion VR\FreedomLocomotion.exe' \
	>"$run_dir/freedom-launch.log" 2>&1 &
launcher_pid=$!
printf 'launcher_pid=%s\n' "$launcher_pid" >>"$run_dir/run.info"

ready_seen=0
for _ in $(seq 1 900); do
	if [[ -s $ready_file ]]; then
		ready_seen=1
		break
	fi
	if ! kill -0 "$launcher_pid" 2>/dev/null; then
		break
	fi
	sleep 0.1
done

consumer_status=-1
if [[ $ready_seen -eq 1 ]]; then
	cp -p "$ready_file" "$run_dir/ready.txt"
	proof_nonce=$(manifest_value "$ready_file" proof_nonce)
	surface_id=$(manifest_value "$ready_file" surface_id)
	submit_sequence=$(manifest_value "$ready_file" submit_sequence)
	width=$(manifest_value "$ready_file" width)
	height=$(manifest_value "$ready_file" height)
	sample_x=$(manifest_value "$ready_file" sample_x)
	sample_y=$(manifest_value "$ready_file" sample_y)
	expected_bgra=$(manifest_value "$ready_file" expected_bgra)
	for value in "$proof_nonce" "$surface_id" "$submit_sequence" "$width" "$height" "$sample_x" "$sample_y"; do
		if [[ ! $value =~ ^[0-9]+$ ]]; then
			echo "invalid numeric manifest value: $value" >&2
			exit 1
		fi
	done
	IFS=, read -r blue green red alpha <<<"$expected_bgra"
	for value in "$blue" "$green" "$red" "$alpha"; do
		if [[ ! $value =~ ^[0-9]+$ ]] || ((value > 255)); then
			echo "invalid BGRA manifest value: $value" >&2
			exit 1
		fi
	done
	set +e
	arch -arm64 "$consumer" --consume-iosurface \
		"$surface_id" "$width" "$height" "$sample_x" "$sample_y" \
		"$blue" "$green" "$red" "$alpha" \
		>"$run_dir/consumer.log" 2>&1
	consumer_status=$?
	set -e
	{
		printf 'proof_nonce=%s\n' "$proof_nonce"
		printf 'submit_sequence=%s\n' "$submit_sequence"
		printf 'surface_id=%s\n' "$surface_id"
		printf 'consumer_status=%d\n' "$consumer_status"
	} >"$done_temp_file"
	mv -f "$done_temp_file" "$done_file"
	cp -p "$done_file" "$run_dir/done.txt"
	for _ in $(seq 1 200); do
		if rg -q 'iosurface proof result .*result=(pass|fail)' /tmp/alvr_openvr_submit_shim.log 2>/dev/null; then
			break
		fi
		sleep 0.01
	done
fi

cpu_frame_seen=0
for _ in $(seq 1 500); do
	if rg -q 'read shared-memory frame' "$alvr_bridge_root/session_log.txt" 2>/dev/null; then
		cpu_frame_seen=1
		break
	fi
	if ! kill -0 "$native_bridge_pid" 2>/dev/null; then
		break
	fi
	sleep 0.01
done

archive_runtime_logs

{
	printf 'ready_seen=%d\n' "$ready_seen"
	printf 'consumer_status=%d\n' "$consumer_status"
	printf 'cpu_frame_seen=%d\n' "$cpu_frame_seen"
} >"$run_dir/status.txt"

verdict=fail
if [[ $ready_seen -eq 1 && $consumer_status -eq 0 && $cpu_frame_seen -eq 1 ]] &&
	grep -q 'iosurface proof result .*result=pass' "$run_dir/openvr-submit-shim.log" &&
	grep -q 'published Submit pair' "$run_dir/openvr-submit-shim.log" &&
	grep -q 'read shared-memory frame' "$run_dir/cpu-bridge-session_log.txt"; then
	verdict=pass
elif [[ $ready_seen -eq 1 ]]; then
	verdict=ready_without_pass
else
	verdict=no_ready
fi
printf '%s\n' "$verdict" >"$run_dir/verdict.txt"
printf '%s\n' "$run_dir"

if [[ $verdict != pass ]]; then
	exit 1
fi
