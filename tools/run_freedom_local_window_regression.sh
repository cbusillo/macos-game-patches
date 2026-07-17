#!/usr/bin/env bash

set -euo pipefail

usage() {
	cat <<'EOF'
Usage: tools/run_freedom_local_window_regression.sh VARIANT

Variants:
  stock-d3dmetal
  bundled-dxvk
  custom-dxvk-stock-mvk
  custom-dxvk-patched-mvk
EOF
}

if [[ $# -ne 1 ]]; then
	usage >&2
	exit 2
fi

variant=$1
case "$variant" in
stock-d3dmetal | bundled-dxvk | custom-dxvk-stock-mvk | custom-dxvk-patched-mvk) ;;
*)
	usage >&2
	exit 2
	;;
esac

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bottle_name=Steam
bottle="$HOME/Library/Application Support/CrossOver/Bottles/$bottle_name"
crossover_app=/Applications/CrossOver.app
alvr_checkout="${ALVR_CHECKOUT:-$repo/../alvr}"
game_dir="$bottle/drive_c/Program Files (x86)/Steam/steamapps/common/Freedom Locomotion VR/FreedomLocomotion/Binaries/Win64"
engine_dir="$bottle/drive_c/Program Files (x86)/Steam/steamapps/common/Freedom Locomotion VR/Engine/Binaries/ThirdParty/OpenVR/OpenVRv1_0_2/Win64"
moltenvk="$crossover_app/Contents/SharedSupport/CrossOver/lib64/libMoltenVK.dylib"
patched_moltenvk="$repo/.code/vendor/crossover-26.2.0/source/sources/moltenvk/Package/Release/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"
dxvk_dir="$repo/.code/probes/008-real-openvr-iosurface/dxvk-d93568f1-build/src"
fake_runtime="$repo/.code/probes/008-real-openvr-iosurface/openvr_api.real.dll"
cxstart="$crossover_app/Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/cxstart"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$repo/.code/probes/010-freedom-local-window-regression/$variant-$timestamp"
backup_dir="$run_dir/backups"
build_dir="$run_dir/build"
shim="$build_dir/openvr_api.dll"
run_lock="$repo/.code/state/freedom-local-window-regression.lock"
launcher_pid=
mutations_started=0
restored=0
run_lock_acquired=0
capture_delay=${FREEDOM_CAPTURE_DELAY_SECONDS:-8}
capture_interval=${FREEDOM_CAPTURE_INTERVAL_SECONDS:-2}
capture_count=${FREEDOM_CAPTURE_COUNT:-3}
use_metal_argument_buffers=${FREEDOM_MVK_USE_METAL_ARGUMENT_BUFFERS:-0}

stock_moltenvk_hash=5c370edf330a126e4605aaf5cd156521197b0fdbd208b3e0a7931f3b8e6c5056
stock_openvr_hash=d793e2a76a61296dc5bce5e6b8dc32f4f3096743aba10c5bac2eb465e635850c

[[ $capture_delay =~ ^[0-9]+$ ]] || {
	echo "FREEDOM_CAPTURE_DELAY_SECONDS must be a nonnegative integer" >&2
	exit 2
}
[[ $capture_interval =~ ^[0-9]+$ ]] || {
	echo "FREEDOM_CAPTURE_INTERVAL_SECONDS must be a nonnegative integer" >&2
	exit 2
}
[[ $capture_count =~ ^[1-9][0-9]*$ ]] || {
	echo "FREEDOM_CAPTURE_COUNT must be a positive integer" >&2
	exit 2
}
case "$use_metal_argument_buffers" in
0 | 1) ;;
*)
	echo "FREEDOM_MVK_USE_METAL_ARGUMENT_BUFFERS must be 0 or 1" >&2
	exit 2
	;;
esac

hash_file() {
	shasum -a 256 "$1" | awk '{print $1}'
}

stop_game() {
	pkill -TERM -f '[F]reedomLocomotion-Win64-Shipping.exe' 2>/dev/null || true
	pkill -TERM -f '[F]reedomLocomotion.exe' 2>/dev/null || true
	sleep 1
	pkill -KILL -f '[F]reedomLocomotion-Win64-Shipping.exe' 2>/dev/null || true
	pkill -KILL -f '[F]reedomLocomotion.exe' 2>/dev/null || true
}

stop_launcher() {
	if [[ -z $launcher_pid ]]; then
		return
	fi
	kill -TERM "$launcher_pid" 2>/dev/null || true
	for _ in $(seq 1 30); do
		if ! kill -0 "$launcher_pid" 2>/dev/null; then
			break
		fi
		sleep 0.1
	done
	kill -KILL "$launcher_pid" 2>/dev/null || true
	wait "$launcher_pid" 2>/dev/null || true
}

archive_logs() {
	[[ -f /tmp/alvr_openvr_submit_shim.log ]] &&
		cp -p /tmp/alvr_openvr_submit_shim.log "$run_dir/openvr-submit-shim.log"
	[[ -f /tmp/fake_openvr_real.log ]] &&
		cp -p /tmp/fake_openvr_real.log "$run_dir/fake-openvr.log"
	for log in "$game_dir"/*_d3d11.log "$game_dir"/*_dxgi.log; do
		[[ -f $log ]] && cp -p "$log" "$run_dir/"
	done
}

restore() {
	if [[ $restored -eq 1 ]]; then
		return 0
	fi
	restored=1
	set +e
	local cleanup_failed=0
	if [[ $mutations_started -eq 1 ]]; then
		stop_game
		stop_launcher
		archive_logs
		cp -f "$backup_dir/libMoltenVK.dylib" "$moltenvk" || cleanup_failed=1
		cp -f "$backup_dir/openvr_api.dll" "$engine_dir/openvr_api.dll" || cleanup_failed=1
		rm -f "$engine_dir/openvr_api.real.dll"
		rm -f "$game_dir/d3d11.dll" "$game_dir/dxgi.dll"
		rm -f "$game_dir"/*_d3d11.log "$game_dir"/*_dxgi.log
		rm -f /tmp/alvr_openvr_submit_shim.log /tmp/fake_openvr_real.log /tmp/alvr_frame_buffer.shm
		{
			printf 'moltenvk=%s\n' "$(hash_file "$moltenvk")"
			printf 'openvr=%s\n' "$(hash_file "$engine_dir/openvr_api.dll")"
			for path in \
				"$engine_dir/openvr_api.real.dll" \
				"$game_dir/d3d11.dll" \
				"$game_dir/dxgi.dll" \
				/tmp/alvr_openvr_submit_shim.log \
				/tmp/fake_openvr_real.log \
				/tmp/alvr_frame_buffer.shm; do
				if [[ -e $path ]]; then
					printf 'unexpected-present=%s\n' "$path"
					cleanup_failed=1
				else
					printf 'absent=%s\n' "$path"
				fi
			done
		} >"$run_dir/restored-state.txt"
		[[ $(hash_file "$moltenvk") == "$stock_moltenvk_hash" ]] || cleanup_failed=1
		[[ $(hash_file "$engine_dir/openvr_api.dll") == "$stock_openvr_hash" ]] || cleanup_failed=1
	fi
	if [[ $run_lock_acquired -eq 1 ]]; then
		rm -rf "$run_lock" || cleanup_failed=1
		run_lock_acquired=0
	fi
	set -e
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

mkdir -p "$backup_dir" "$build_dir" "$run_dir/mvk-shaders" "$(dirname "$run_lock")"

for path in \
	"$alvr_checkout/openvr/headers" \
	"$moltenvk" \
	"$patched_moltenvk" \
	"$dxvk_dir/d3d11/d3d11.dll" \
	"$dxvk_dir/dxgi/dxgi.dll" \
	"$fake_runtime" \
	"$cxstart" \
	"$engine_dir/openvr_api.dll"; do
	[[ -e $path ]] || {
		echo "missing=$path" >&2
		exit 1
	}
done

if pgrep -f '[F]reedomLocomotion' >/dev/null 2>&1; then
	echo "Freedom Locomotion is already running" >&2
	exit 1
fi
[[ $(hash_file "$moltenvk") == "$stock_moltenvk_hash" ]] || {
	echo "CrossOver MoltenVK is not pristine" >&2
	exit 1
}
[[ $(hash_file "$engine_dir/openvr_api.dll") == "$stock_openvr_hash" ]] || {
	echo "Freedom OpenVR DLL is not pristine" >&2
	exit 1
}
for path in \
	"$engine_dir/openvr_api.real.dll" \
	"$game_dir/d3d11.dll" \
	"$game_dir/dxgi.dll" \
	"$game_dir"/*_d3d11.log \
	"$game_dir"/*_dxgi.log; do
	[[ ! -e $path ]] || {
		echo "staging target exists: $path" >&2
		exit 1
	}
done

if ! mkdir "$run_lock" 2>/dev/null; then
	echo "local-window regression probe is already running: $run_lock" >&2
	exit 1
fi
printf '%s\n' "$$" >"$run_lock/pid"
printf '%s\n' "$run_dir" >"$run_lock/run-dir"
run_lock_acquired=1

x86_64-w64-mingw32-g++ \
	-O2 -g -std=c++20 -static -static-libgcc -static-libstdc++ -shared \
	"$repo/tools/openvr_submit_shim.cpp" \
	"$repo/tools/dxvk_iosurface_submit_proof.cpp" \
	-I"$alvr_checkout/openvr/headers" \
	-I"$alvr_checkout/alvr/server_openvr/cpp" \
	-I/opt/homebrew/include \
	-ld3d11 -ld3d10 -ldxgi -lole32 \
	-Wl,--out-implib,"$build_dir/openvr_api_shim.lib" \
	-o "$shim" \
	>"$run_dir/shim-build.log" 2>&1

cp -p "$moltenvk" "$backup_dir/libMoltenVK.dylib"
cp -p "$engine_dir/openvr_api.dll" "$backup_dir/openvr_api.dll"
mutations_started=1
rm -f /tmp/alvr_openvr_submit_shim.log /tmp/fake_openvr_real.log /tmp/alvr_frame_buffer.shm
cp -f "$fake_runtime" "$engine_dir/openvr_api.real.dll"
cp -f "$shim" "$engine_dir/openvr_api.dll"

cx_env="ALVR_SHIM_INNER_CROP_PX=224 WINEDEBUG=-all,+loaddll"
case "$variant" in
stock-d3dmetal) ;;
bundled-dxvk)
	cx_env="CX_GRAPHICS_BACKEND=dxvk DXVK_LOG_LEVEL=debug DXVK_STATE_CACHE=0 MVK_CONFIG_LOG_LEVEL=3 MVK_CONFIG_SHADER_DUMP_DIR=$run_dir/mvk-shaders $cx_env"
	;;
custom-dxvk-stock-mvk | custom-dxvk-patched-mvk)
	cp -f "$dxvk_dir/d3d11/d3d11.dll" "$game_dir/d3d11.dll"
	cp -f "$dxvk_dir/dxgi/dxgi.dll" "$game_dir/dxgi.dll"
	cx_env="CX_GRAPHICS_BACKEND=dxvk WINEDLLOVERRIDES=d3d11,dxgi=n DXVK_LOG_LEVEL=debug DXVK_STATE_CACHE=0 MVK_CONFIG_LOG_LEVEL=3 MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=$use_metal_argument_buffers MVK_CONFIG_SHADER_DUMP_DIR=$run_dir/mvk-shaders $cx_env"
	if [[ $variant == custom-dxvk-patched-mvk ]]; then
		cp -f "$patched_moltenvk" "$moltenvk"
	fi
	;;
esac

{
	printf 'variant=%s\n' "$variant"
	printf 'cx_env=%s\n' "$cx_env"
	printf 'capture_delay_seconds=%s\n' "$capture_delay"
	printf 'capture_interval_seconds=%s\n' "$capture_interval"
	printf 'capture_count=%s\n' "$capture_count"
	printf 'use_metal_argument_buffers=%s\n' "$use_metal_argument_buffers"
	printf 'repo_head=%s\n' "$(git -C "$repo" rev-parse HEAD)"
	shasum -a 256 "$shim" "$fake_runtime" "$moltenvk"
	if [[ $variant == custom-dxvk-* ]]; then
		shasum -a 256 "$dxvk_dir/d3d11/d3d11.dll" "$dxvk_dir/dxgi/dxgi.dll"
	fi
} >"$run_dir/run.info"

"$cxstart" --bottle "$bottle_name" --no-update --no-gui --wait --env "$cx_env" \
	'C:\Program Files (x86)\Steam\steamapps\common\Freedom Locomotion VR\FreedomLocomotion.exe' \
	>"$run_dir/freedom-launch.log" 2>&1 &
launcher_pid=$!

game_started=0
for _ in $(seq 1 400); do
	if pgrep -f '[F]reedomLocomotion-Win64-Shipping.exe' >/dev/null 2>&1; then
		game_started=1
		break
	fi
	if ! kill -0 "$launcher_pid" 2>/dev/null; then
		break
	fi
	sleep 0.1
done
if [[ $game_started -ne 1 ]]; then
	echo "Freedom did not reach the shipping executable" >&2
	exit 1
fi

sleep "$capture_delay"
for index in $(seq 1 "$capture_count"); do
	screencapture -x "$run_dir/screen-$index.png"
	if [[ $index -lt $capture_count ]]; then
		sleep "$capture_interval"
	fi
done
shasum -a 256 "$run_dir"/screen-*.png >"$run_dir/screenshots.sha256"

if ! restore; then
	echo "probe cleanup failed: $run_dir" >&2
	exit 1
fi
trap - EXIT
printf '%s\n' "$run_dir"
