#!/usr/bin/env bash

set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bottle_name=Steam
bottle="$HOME/Library/Application Support/CrossOver/Bottles/$bottle_name"
crossover_app=${CROSSOVER_APP:-/Applications/CrossOver.app}
alvr_checkout=${ALVR_CHECKOUT:-$repo/../alvr}
open_brush_version=1.0.28
release_root="$repo/.code/vendor/open-brush/$open_brush_version/desktop/OpenBrush_Desktop_$open_brush_version"
release_zip="$repo/.code/vendor/open-brush/$open_brush_version/OpenBrush_Desktop_$open_brush_version.zip"
moltenvk="$crossover_app/Contents/SharedSupport/CrossOver/lib64/libMoltenVK.dylib"
patched_moltenvk="$repo/.code/vendor/crossover-26.2.0/source/sources/moltenvk/Package/Release/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"
dxvk_dir="$repo/.code/probes/008-real-openvr-iosurface/dxvk-d93568f1-build/src"
cxstart="$crossover_app/Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/cxstart"
wineserver="$crossover_app/Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/wineserver"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$repo/.code/probes/011-open-brush-controller-smoke/runtime-discovery-$timestamp"
build_dir="$run_dir/build"
work_root="$run_dir/work/OpenBrush_Desktop_$open_brush_version"
game_dir="$work_root"
runtime_dir="$work_root/OpenBrush_Data/Plugins/x86_64"
shim="$build_dir/openvr_api.dll"
fake_runtime="$build_dir/openvr_api.real.dll"
backup_moltenvk="$run_dir/backups/libMoltenVK.dylib"
probe_dir="$bottle/drive_c/alvr-probes"
desktop_warmup_file="$probe_dir/open-brush-warmup-$timestamp.txt"
run_lock="$repo/.code/state/alvr-native-probe.lock"
launcher_pid=
desktop_warmup_pid=
run_lock_acquired=0
mutations_started=0
restored=0

stock_moltenvk_hash=5c370edf330a126e4605aaf5cd156521197b0fdbd208b3e0a7931f3b8e6c5056
release_zip_hash=5534d2e324e3317232324fea3991d8143da7592b3e0da19e4822755e6df8e371
release_openvr_hash=bd7a7958bdb647096e5e22cb4d020dd99720983f3af1cd500e8b570cfa9f017b

hash_file() {
	shasum -a 256 "$1" | awk '{print $1}'
}

replace_file_atomically() {
	local source=$1
	local target=$2
	local staging="$target.alvr-probe.$$"

	rm -f "$staging"
	cp -p "$source" "$staging" || {
		rm -f "$staging"
		return 1
	}
	mv -f "$staging" "$target" || {
		rm -f "$staging"
		return 1
	}
}

process_uses_bottle() {
	local pid=$1
	lsof -p "$pid" -Fn 2>/dev/null |
		awk -v prefix="n$bottle/" '
			index($0, prefix) == 1 { found = 1 }
			END { exit !found }
		'
}

bottle_process_pids() {
	local pid
	while IFS= read -r pid; do
		if process_uses_bottle "$pid"; then
			printf '%s\n' "$pid"
		fi
	done < <(pgrep -f '\.exe' 2>/dev/null || true)
}

shutdown_bottle() {
	local log_file=$1
	local pid
	local remaining=

	{
		printf 'shutdown_started=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		CX_ROOT="$crossover_app/Contents/SharedSupport/CrossOver" \
			CX_BOTTLE="$bottle_name" \
			WINEPREFIX="$bottle" \
			"$wineserver" -k || true
		for pid in $(bottle_process_pids); do
			printf 'term_pid=%s command=' "$pid"
			ps -p "$pid" -o command= || true
			kill -TERM "$pid" 2>/dev/null || true
		done
		for _ in $(seq 1 50); do
			remaining=$(bottle_process_pids)
			[[ -z $remaining ]] && break
			sleep 0.1
		done
		for pid in $remaining; do
			printf 'kill_pid=%s command=' "$pid"
			ps -p "$pid" -o command= || true
			kill -KILL "$pid" 2>/dev/null || true
		done
		sleep 0.1
		remaining=$(bottle_process_pids)
		printf 'remaining=%s\n' "${remaining:-none}"
		printf 'shutdown_finished=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	} >>"$log_file" 2>&1

	[[ -z $remaining ]]
}

stop_pid() {
	local pid=${1:-}
	if [[ -z $pid ]]; then
		return
	fi
	kill -TERM "$pid" 2>/dev/null || true
	for _ in $(seq 1 50); do
		if ! kill -0 "$pid" 2>/dev/null; then
			break
		fi
		sleep 0.1
	done
	kill -KILL "$pid" 2>/dev/null || true
	wait "$pid" 2>/dev/null || true
}

acquire_run_lock() {
	local owner_pid=
	if ! mkdir "$run_lock" 2>/dev/null; then
		[[ -f $run_lock/pid ]] && owner_pid=$(<"$run_lock/pid")
		if [[ $owner_pid =~ ^[1-9][0-9]*$ ]] && kill -0 "$owner_pid" 2>/dev/null; then
			echo "native probe is already running with pid=$owner_pid" >&2
		else
			echo "stale or incomplete native probe lock: $run_lock" >&2
		fi
		exit 1
	fi
	printf '%s\n' "$$" >"$run_lock/pid"
	printf '%s\n' "$run_dir" >"$run_lock/run-dir"
	run_lock_acquired=1
}

start_desktop_warmup() {
	local pid
	local ready=0
	local marker=${desktop_warmup_file##*/}

	printf 'Open Brush Wine desktop warmup\r\n' >"$desktop_warmup_file"
	"$cxstart" --bottle "$bottle_name" --no-update --no-gui --no-wait \
		notepad.exe "$desktop_warmup_file" \
		>"$run_dir/bottle-desktop-warmup.log" 2>&1
	for _ in $(seq 1 300); do
		for pid in $(pgrep -f '[n]otepad.exe' 2>/dev/null || true); do
			if process_uses_bottle "$pid" &&
				ps -p "$pid" -o command= | rg -Fq "$marker"; then
				desktop_warmup_pid=$pid
				break
			fi
		done
		if rg -q 'Created VkInstance' "$run_dir/bottle-desktop-warmup.log" 2>/dev/null; then
			ready=1
			break
		fi
		sleep 0.1
	done
	if [[ $ready -ne 1 ]]; then
		cat "$run_dir/bottle-desktop-warmup.log" >&2
		echo "Wine desktop warmup did not initialize Vulkan" >&2
		return 1
	fi
	printf 'desktop_warmup_pid=%s vulkan_initialized=1\n' \
		"${desktop_warmup_pid:-unknown}" \
		>>"$run_dir/bottle-desktop-warmup.log"
	sleep 1
}

archive_logs() {
	[[ -f /tmp/alvr_openvr_submit_shim.log ]] &&
		cp -p /tmp/alvr_openvr_submit_shim.log "$run_dir/openvr-submit-shim.log"
	[[ -f /tmp/fake_openvr_real.log ]] &&
		cp -p /tmp/fake_openvr_real.log "$run_dir/fake-openvr.log"
	for log in "$game_dir"/*_d3d11.log "$game_dir"/*_dxgi.log; do
		[[ -f $log ]] && cp -p "$log" "$run_dir/"
	done
	find "$bottle/drive_c/users" -type f -name Player.log -mmin -10 -print0 2>/dev/null |
		while IFS= read -r -d '' log; do
			cp -p "$log" "$run_dir/unity-player.log"
			break
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
		stop_pid "$launcher_pid"
		shutdown_bottle "$run_dir/bottle-shutdown-after.log" || cleanup_failed=1
		archive_logs
		if [[ -f $backup_moltenvk ]]; then
			replace_file_atomically "$backup_moltenvk" "$moltenvk" || cleanup_failed=1
		fi
		rm -f /tmp/alvr_openvr_submit_shim.log /tmp/fake_openvr_real.log
		rm -f "$desktop_warmup_file"
		rm -rf "$run_dir/work"
		{
			printf 'moltenvk=%s\n' "$(hash_file "$moltenvk")"
			for path in \
				"$desktop_warmup_file" \
				/tmp/alvr_openvr_submit_shim.log \
				/tmp/fake_openvr_real.log \
				"$run_dir/work"; do
				if [[ -e $path ]]; then
					printf 'unexpected-present=%s\n' "$path"
					cleanup_failed=1
				else
					printf 'absent=%s\n' "$path"
				fi
			done
		} >"$run_dir/restored-state.txt"
		[[ $(hash_file "$moltenvk") == "$stock_moltenvk_hash" ]] || cleanup_failed=1
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

mkdir -p "$build_dir" "$run_dir/backups" "$probe_dir" "$(dirname "$run_lock")"

for path in \
	"$alvr_checkout/openvr/headers" \
	"$alvr_checkout/alvr/server_openvr/cpp" \
	"$release_zip" \
	"$release_root/OpenBrush.exe" \
	"$release_root/OpenBrush_Data/boot.config" \
	"$release_root/OpenBrush_Data/Plugins/x86_64/openvr_api.dll" \
	"$moltenvk" \
	"$patched_moltenvk" \
	"$dxvk_dir/d3d11/d3d11.dll" \
	"$dxvk_dir/dxgi/dxgi.dll" \
	"$cxstart" \
	"$wineserver"; do
	[[ -e $path ]] || {
		echo "missing=$path" >&2
		exit 1
	}
done

[[ $(hash_file "$release_zip") == "$release_zip_hash" ]] || {
	echo "Open Brush release archive hash mismatch" >&2
	exit 1
}
[[ $(hash_file "$release_root/OpenBrush_Data/Plugins/x86_64/openvr_api.dll") == "$release_openvr_hash" ]] || {
	echo "Open Brush OpenVR runtime hash mismatch" >&2
	exit 1
}
rg -q '^vr-device-list=OpenVR$' "$release_root/OpenBrush_Data/boot.config" || {
	echo "Open Brush release is not configured for OpenVR" >&2
	exit 1
}
[[ $(hash_file "$moltenvk") == "$stock_moltenvk_hash" ]] || {
	echo "CrossOver MoltenVK is not pristine" >&2
	exit 1
}
if pgrep -f '[O]penBrush.exe' >/dev/null 2>&1; then
	echo "Open Brush is already running" >&2
	exit 1
fi

acquire_run_lock
shutdown_bottle "$run_dir/bottle-shutdown-before.log" || {
	echo "Steam bottle did not shut down cleanly" >&2
	exit 1
}

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
x86_64-w64-mingw32-g++ \
	-O2 -std=c++17 -static -static-libgcc -static-libstdc++ -shared \
	"$repo/tools/fake_openvr_real.cpp" \
	-I"$alvr_checkout/openvr/headers" \
	-I"$alvr_checkout/alvr/server_openvr/cpp" \
	-o "$fake_runtime" \
	>"$run_dir/fake-runtime-build.log" 2>&1

mkdir -p "$run_dir/work"
cp -cR "$release_root" "$work_root"
cp -p "$moltenvk" "$backup_moltenvk"
mutations_started=1
replace_file_atomically "$patched_moltenvk" "$moltenvk"
codesign --verify --strict --all-architectures "$moltenvk"
if ! /usr/bin/time -p arch -x86_64 /usr/bin/python3 -c \
	'import ctypes, sys; ctypes.CDLL(sys.argv[1])' \
	"$moltenvk" >"$run_dir/moltenvk-prewarm.log" 2>&1; then
	cat "$run_dir/moltenvk-prewarm.log" >&2
	echo "MoltenVK Rosetta prewarm failed" >&2
	exit 1
fi

cp -f "$fake_runtime" "$runtime_dir/openvr_api.real.dll"
cp -f "$shim" "$runtime_dir/openvr_api.dll"
cp -f "$dxvk_dir/d3d11/d3d11.dll" "$game_dir/d3d11.dll"
cp -f "$dxvk_dir/dxgi/dxgi.dll" "$game_dir/dxgi.dll"
rm -f /tmp/alvr_openvr_submit_shim.log /tmp/fake_openvr_real.log
start_desktop_warmup

cx_env="CX_GRAPHICS_BACKEND=dxvk WINEDLLOVERRIDES=d3d11,dxgi=n DXVK_LOG_LEVEL=debug DXVK_STATE_CACHE=0 MVK_CONFIG_LOG_LEVEL=3 MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=0 MVK_CONFIG_SHADER_DUMP_DIR=$run_dir/mvk-shaders ALVR_MOLTENVK_PATH=$moltenvk ALVR_FAKE_RENDER_TARGET_WIDTH=1620 ALVR_FAKE_RENDER_TARGET_HEIGHT=1800 WINEDEBUG=-all,+loaddll"
mkdir -p "$run_dir/mvk-shaders"
{
	printf 'run_dir=%s\n' "$run_dir"
	printf 'open_brush_version=%s\n' "$open_brush_version"
	printf 'release_zip_hash=%s\n' "$(hash_file "$release_zip")"
	printf 'release_exe_hash=%s\n' "$(hash_file "$release_root/OpenBrush.exe")"
	printf 'release_openvr_hash=%s\n' "$(hash_file "$release_root/OpenBrush_Data/Plugins/x86_64/openvr_api.dll")"
	printf 'boot_config=%s\n' "$(tr '\n' ';' <"$release_root/OpenBrush_Data/boot.config")"
	printf 'cx_env=%s\n' "$cx_env"
	shasum -a 256 "$shim" "$fake_runtime" "$patched_moltenvk" \
		"$dxvk_dir/d3d11/d3d11.dll" "$dxvk_dir/dxgi/dxgi.dll"
	printf 'macos_game_patches_head=%s\n' "$(git -C "$repo" rev-parse HEAD)"
	printf 'alvr_head=%s\n' "$(git -C "$alvr_checkout" rev-parse HEAD)"
	git -C "$repo" status --short
	git -C "$alvr_checkout" status --short
} >"$run_dir/run.info"
shasum -a 256 \
	"$repo/tools/openvr_submit_shim.cpp" \
	"$repo/tools/dxvk_iosurface_submit_proof.cpp" \
	"$repo/tools/dxvk_iosurface_submit_proof.h" \
	"$repo/tools/fake_openvr_real.cpp" \
	"$repo/tools/run_open_brush_runtime_discovery.sh" \
	>"$run_dir/source-state.txt"
git -C "$repo" diff --binary HEAD >"$run_dir/macos-game-patches.patch"

"$cxstart" --bottle "$bottle_name" --no-update --no-gui --wait-children \
	--workdir "$game_dir" --env "$cx_env" \
	"$game_dir/OpenBrush.exe" -force-d3d11 \
	>"$run_dir/open-brush-launch.log" 2>&1 &
launcher_pid=$!

submit_ready=0
for _ in $(seq 1 300); do
	left_submits=$(rg -c 'Submit diagnostic .*eye=0' /tmp/alvr_openvr_submit_shim.log 2>/dev/null || true)
	right_submits=$(rg -c 'Submit diagnostic .*eye=1' /tmp/alvr_openvr_submit_shim.log 2>/dev/null || true)
	if [[ ${left_submits:-0} -ge 10 && ${right_submits:-0} -ge 10 ]]; then
		submit_ready=1
		break
	fi
	if ! kill -0 "$launcher_pid" 2>/dev/null; then
		break
	fi
	sleep 0.1
done

input_ready=0
if [[ $submit_ready -eq 1 ]]; then
	for _ in $(seq 1 600); do
		input_interfaces=$(rg -c 'VR_GetGenericInterface .*IVRInput_006' /tmp/fake_openvr_real.log 2>/dev/null || true)
		action_handles=$(rg -c 'IVRInput::GetActionHandle name=/actions/tiltbrush/' /tmp/fake_openvr_real.log 2>/dev/null || true)
		if [[ ${input_interfaces:-0} -ge 2 && ${action_handles:-0} -ge 15 ]]; then
			input_ready=1
			break
		fi
		if ! kill -0 "$launcher_pid" 2>/dev/null; then
			break
		fi
		sleep 0.1
	done
fi
[[ $input_ready -eq 1 ]] && sleep 2

restore_status=0
if ! restore; then
	restore_status=1
fi

fake_log="$run_dir/fake-openvr.log"
shim_log="$run_dir/openvr-submit-shim.log"
compositor_interfaces=$(rg -c 'VR_GetGenericInterface IVRCompositor' "$fake_log" 2>/dev/null || true)
input_interfaces=$(rg -c 'VR_GetGenericInterface .*IVRInput' "$fake_log" 2>/dev/null || true)
action_handles=$(rg -c 'IVRInput::GetActionHandle name=/actions/tiltbrush/' "$fake_log" 2>/dev/null || true)
controller_state_queries=$(rg -c 'GetControllerState' "$fake_log" 2>/dev/null || true)
left_submits=$(rg -c 'Submit diagnostic .*eye=0' "$shim_log" 2>/dev/null || true)
right_submits=$(rg -c 'Submit diagnostic .*eye=1' "$shim_log" 2>/dev/null || true)
d3d11_descriptions=$(rg -c 'phase=d3d11-desc' "$shim_log" 2>/dev/null || true)
distinct_handles=$(
	{
		rg 'Submit diagnostic .*phase=d3d11-desc' "$shim_log" 2>/dev/null || true
	} | sed -E 's/.* handle=([^ ]+).*/\1/' | sort -u | wc -l | tr -d ' '
)
distinct_sizes=$(
	{
		rg -o 'desc=[0-9]+x[0-9]+' "$shim_log" 2>/dev/null || true
	} | sort -u | tr '\n' ',' | sed 's/,$//'
)
left_handle=$(
	{
		rg 'Submit diagnostic .*phase=d3d11-desc eye=0' "$shim_log" 2>/dev/null || true
	} | head -n 1 | sed -E 's/.* handle=([^ ]+).*/\1/'
)
right_handle=$(
	{
		rg 'Submit diagnostic .*phase=d3d11-desc eye=1' "$shim_log" 2>/dev/null || true
	} | head -n 1 | sed -E 's/.* handle=([^ ]+).*/\1/'
)
full_bounds=$(rg -c 'phase=d3d11-desc .*raw=\[0\.0000 0\.0000 1\.0000 1\.0000\]' "$shim_log" 2>/dev/null || true)
texture_contract=unknown
if [[ -n $left_handle && -n $right_handle && $left_handle != "$right_handle" &&
	${full_bounds:-0} -ge 2 ]]; then
	texture_contract=separate-eyes
fi

{
	printf 'restore_status=%s\n' "$restore_status"
	printf 'submit_ready=%s\n' "$submit_ready"
	printf 'input_ready=%s\n' "$input_ready"
	printf 'compositor_interfaces=%s\n' "${compositor_interfaces:-0}"
	printf 'input_interfaces=%s\n' "${input_interfaces:-0}"
	printf 'action_handles=%s\n' "${action_handles:-0}"
	printf 'controller_state_queries=%s\n' "${controller_state_queries:-0}"
	printf 'left_submits=%s\n' "${left_submits:-0}"
	printf 'right_submits=%s\n' "${right_submits:-0}"
	printf 'd3d11_descriptions=%s\n' "${d3d11_descriptions:-0}"
	printf 'distinct_handles=%s\n' "${distinct_handles:-0}"
	printf 'distinct_sizes=%s\n' "${distinct_sizes:-none}"
	printf 'left_handle=%s\n' "${left_handle:-none}"
	printf 'right_handle=%s\n' "${right_handle:-none}"
	printf 'full_bounds=%s\n' "${full_bounds:-0}"
	printf 'texture_contract=%s\n' "$texture_contract"
} >"$run_dir/status.txt"

verdict=fail
if [[ $restore_status -eq 0 && $submit_ready -eq 1 && $input_ready -eq 1 &&
	${compositor_interfaces:-0} -gt 0 && ${input_interfaces:-0} -ge 2 &&
	${action_handles:-0} -ge 15 && $texture_contract == separate-eyes &&
	${left_submits:-0} -gt 0 && ${right_submits:-0} -gt 0 &&
	${d3d11_descriptions:-0} -gt 0 ]]; then
	verdict=pass
fi
printf '%s\n' "$verdict" >"$run_dir/verdict.txt"
printf 'artifact=%s\nverdict=%s\n' "$run_dir" "$verdict"
[[ $verdict == pass ]]
