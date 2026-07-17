#!/usr/bin/env bash

set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bottle="$HOME/Library/Application Support/CrossOver/Bottles/Steam"
installed_root="$bottle/drive_c/Program Files (x86)/Steam/steamapps/common/SteamVR/tools/steamvr_tutorial/win64"
manifest="$bottle/drive_c/Program Files (x86)/Steam/steamapps/appmanifest_250820.acf"
installed_runtime_dir="$installed_root/steamvr_tutorial_Data/Plugins"
stock_runtime="$installed_runtime_dir/openvr_api.real.dll"
work_root="$repo/.code/state/steamvr-tutorial-unity-control-$$"
runtime_dir="$work_root/steamvr_tutorial_Data/Plugins"
output_root="$repo/.code/probes/012-steamvr-tutorial-unity-performance-control"
player_log="$bottle/drive_c/users/crossover/AppData/LocalLow/Valve/SteamVR Tutorial/Player.log"
alvr_checkout=${ALVR_CHECKOUT:-"$HOME/.code/working/alvr/branches/native-surface-contract"}
control_calls=${ALVR_NATIVE_PROBE_FRAMES:-900}
common_runner_pid=

expected_executable_hash=46ae66c3f38952659c56ba4fe4678d157b0f8ca8ee49b29595a1db556b4a02a7
expected_unity_hash=9e0289b7c0abfc5e21d3b1cc90cda0eae1119ee34dedc013819d1a681968dbb7
expected_openvr_hash=bd7a7958bdb647096e5e22cb4d020dd99720983f3af1cd500e8b570cfa9f017b

hash_file() {
	shasum -a 256 "$1" | awk '{print $1}'
}

tree_hash() {
	(
		cd "$installed_root"
		find . -type f -print | LC_ALL=C sort | while IFS= read -r relative; do
			printf '%s  %s\n' "$(hash_file "$relative")" "$relative"
		done
	) | shasum -a 256 | awk '{print $1}'
}

latest_run() {
	find "$output_root" -maxdepth 1 -type d -name 'real-native-encode-*' -print 2>/dev/null |
		LC_ALL=C sort |
		tail -n 1
}

process_uses_bottle() {
	local pid=$1
	lsof -p "$pid" -Fn 2>/dev/null |
		awk -v prefix="n$bottle/" '
			index($0, prefix) == 1 { found = 1 }
			END { exit !found }
		'
}

tutorial_process_pids() {
	local pid
	while IFS= read -r pid; do
		if process_uses_bottle "$pid"; then
			printf '%s\n' "$pid"
		fi
	done < <(pgrep -f '[s]teamvr_tutorial\.exe' 2>/dev/null || true)
}

latest_pacing_calls() {
	rg 'fake pacing summary' /tmp/fake_openvr_real.log 2>/dev/null |
		tail -n 1 |
		sed -n 's/.* calls=\([0-9][0-9]*\).*/\1/p'
}

stop_control_after_calls() {
	local calls=
	local pid
	local saw_tutorial=0
	for _ in $(seq 1 1800); do
		if [[ -n $(tutorial_process_pids) ]]; then
			saw_tutorial=1
		fi
		if [[ $saw_tutorial -eq 1 ]]; then
			calls=$(latest_pacing_calls)
			if [[ $calls =~ ^[0-9]+$ ]] && ((calls >= control_calls)); then
				for pid in $(tutorial_process_pids); do
					kill -TERM "$pid" 2>/dev/null || true
				done
				return 0
			fi
		fi
		if [[ -n $common_runner_pid ]] && ! kill -0 "$common_runner_pid" 2>/dev/null; then
			return 1
		fi
		sleep 0.1
	done
	return 1
}

status_value() {
	local key=$1
	local file=$2
	awk -F= -v key="$key" '$1 == key { print $2; exit }' "$file" 2>/dev/null
}

cleanup() {
	if [[ -n $common_runner_pid ]] && kill -0 "$common_runner_pid" 2>/dev/null; then
		kill -TERM "$common_runner_pid" 2>/dev/null || true
		wait "$common_runner_pid" 2>/dev/null || true
	fi
	rm -rf "$work_root"
}
trap cleanup EXIT

if ! [[ $control_calls =~ ^[1-9][0-9]*$ ]] ||
	! ((control_calls >= 600 && control_calls % 300 == 0)); then
	echo "ALVR_NATIVE_PROBE_FRAMES must be a multiple of 300 and at least 600" >&2
	exit 1
fi

for path in \
	"$manifest" \
	"$installed_root/steamvr_tutorial.exe" \
	"$installed_root/UnityPlayer.dll" \
	"$installed_root/steamvr_tutorial_Data/boot.config" \
	"$stock_runtime" \
	"$alvr_checkout/Cargo.toml"; do
	[[ -f $path ]] || {
		echo "missing=$path" >&2
		exit 1
	}
done

[[ $(hash_file "$installed_root/steamvr_tutorial.exe") == "$expected_executable_hash" ]] || {
	echo "SteamVR Tutorial executable hash mismatch" >&2
	exit 1
}
[[ $(hash_file "$installed_root/UnityPlayer.dll") == "$expected_unity_hash" ]] || {
	echo "SteamVR Tutorial UnityPlayer hash mismatch" >&2
	exit 1
}
[[ $(hash_file "$stock_runtime") == "$expected_openvr_hash" ]] || {
	echo "SteamVR Tutorial saved stock OpenVR runtime hash mismatch" >&2
	exit 1
}
rg -q '^vr-device-list=OpenVR$' "$installed_root/steamvr_tutorial_Data/boot.config" || {
	echo "SteamVR Tutorial is not configured for OpenVR" >&2
	exit 1
}

installed_tree_before=$(tree_hash)
mkdir -p "$(dirname "$work_root")" "$output_root"
cp -cR "$installed_root" "$work_root"
rm -f \
	"$work_root/openvr_api.dll" \
	"$work_root/openvr_api.real.dll" \
	"$work_root/d3d11.dll" \
	"$work_root/dxgi.dll" \
	"$work_root/alvr_iosurface_bridge.dll" \
	"$work_root"/*_d3d11.log \
	"$work_root"/*_dxgi.log \
	"$runtime_dir/openvr_api.dll" \
	"$runtime_dir/openvr_api.fake.dll" \
	"$runtime_dir/openvr_api.real.dll"
cp -p "$stock_runtime" "$runtime_dir/openvr_api.dll"

[[ $(hash_file "$work_root/steamvr_tutorial.exe") == "$expected_executable_hash" ]] || exit 1
[[ $(hash_file "$work_root/UnityPlayer.dll") == "$expected_unity_hash" ]] || exit 1
[[ $(hash_file "$runtime_dir/openvr_api.dll") == "$expected_openvr_hash" ]] || exit 1

previous_run=$(latest_run)
set +e
env \
	ALVR_CHECKOUT="$alvr_checkout" \
	ALVR_NATIVE_PROBE_APP_NAME="SteamVR Tutorial Unity control" \
	ALVR_NATIVE_PROBE_PROCESS_PATTERN='[s]teamvr_tutorial.exe' \
	ALVR_NATIVE_PROBE_GAME_DIR="$work_root" \
	ALVR_NATIVE_PROBE_OPENVR_DIR="$runtime_dir" \
	ALVR_NATIVE_PROBE_EXECUTABLE="$work_root/steamvr_tutorial.exe" \
	ALVR_NATIVE_PROBE_WORKDIR="$work_root" \
	ALVR_NATIVE_PROBE_ARGUMENTS='-force-d3d11 -screen-fullscreen 0 -screen-width 1280 -screen-height 720' \
	ALVR_NATIVE_PROBE_EXTRA_ENV='ALVR_FAKE_RENDER_TARGET_WIDTH=1080 ALVR_FAKE_RENDER_TARGET_HEIGHT=1344' \
	ALVR_NATIVE_PROBE_LAUNCHER_SOURCE=tools/run_steamvr_tutorial_unity_control.sh \
	ALVR_NATIVE_PROBE_OUTPUT_ROOT="$output_root" \
	ALVR_NATIVE_PROBE_STOCK_OPENVR_HASH="$expected_openvr_hash" \
	ALVR_NATIVE_PROBE_SOURCE_WIDTH=2160 \
	ALVR_NATIVE_PROBE_SOURCE_HEIGHT=1344 \
	ALVR_NATIVE_PROBE_OUTPUT_WIDTH=2880 \
	ALVR_NATIVE_PROBE_OUTPUT_HEIGHT=1792 \
	ALVR_NATIVE_PROBE_FRAMES="$control_calls" \
	ALVR_NATIVE_PROBE_CONNECT=false \
	ALVR_NATIVE_PROBE_MIN_PRODUCER_FPS=60 \
	ALVR_NATIVE_PROBE_MAX_PRODUCER_FPS=100 \
	"$repo/tools/run_real_native_iosurface_probe.sh" &
common_runner_pid=$!
set -e

monitor_status=0
if ! stop_control_after_calls; then
	monitor_status=1
	kill -TERM "$common_runner_pid" 2>/dev/null || true
fi
set +e
wait "$common_runner_pid"
common_status=$?
set -e
common_runner_pid=

current_run=$(latest_run)
installed_tree_after=$(tree_hash)
cleanup
trap - EXIT

control_verdict=fail
control_tail_calls=0
control_tail_frames=0
control_tail_fps=0
source_contract_pass=0
restore_status=1
fake_pacing_calls=0
native_encoded=0
submitted=0
producer_drop_log_count=0
if [[ -n $current_run && $current_run != "$previous_run" ]]; then
	status_file="$current_run/status.txt"
	shim_log="$current_run/openvr-submit-shim.log"
	fake_log="$current_run/fake-openvr.log"
	if [[ -f $fake_log ]]; then
		read -r control_tail_calls control_tail_frames control_tail_fps < <(
			rg 'fake pacing summary' "$fake_log" |
				tail -n 2 |
				awk '
					function field(name, i, parts) {
						for (i = 1; i <= NF; ++i) {
							split($i, parts, "=")
							if (parts[1] == name) return parts[2] + 0
						}
						return 0
					}
					{
						previous_calls = calls
						previous_frame = frame
						calls = field("calls")
						frame = field("frame")
					}
					END {
						call_delta = calls - previous_calls
						frame_delta = frame - previous_frame
						fps = frame_delta > 0 ? call_delta * 90.0 / frame_delta : 0
						printf "%d %d %.3f\n", call_delta, frame_delta, fps
					}'
		)
	fi
	if [[ -f $shim_log ]] &&
		rg -q 'eye=0.*desc=2160x1344 format=27.*raw=\[0\.0000 0\.0000 0\.5000 1\.0000\]' "$shim_log" &&
		rg -q 'eye=1.*desc=2160x1344 format=27.*raw=\[0\.5000 0\.0000 1\.0000 1\.0000\]' "$shim_log"; then
		source_contract_pass=1
	fi
	if [[ -f $status_file ]]; then
		restore_status=$(status_value restore_status "$status_file")
		fake_pacing_calls=$(status_value fake_pacing_calls "$status_file")
		native_encoded=$(status_value native_encoded "$status_file")
		submitted=$(status_value submitted "$status_file")
		producer_drop_log_count=$(status_value producer_drop_log_count "$status_file")
	fi
	if [[ $monitor_status -eq 0 && $common_status -ne 0 &&
		$installed_tree_after == "$installed_tree_before" &&
		$restore_status == 0 && $fake_pacing_calls -ge $control_calls &&
		$native_encoded -eq 0 && $submitted -eq 0 &&
		$producer_drop_log_count -eq 0 && $source_contract_pass -eq 1 ]] &&
		awk -v fps="$control_tail_fps" 'BEGIN { exit !(fps >= 60 && fps <= 100) }'; then
		control_verdict=pass
	fi

	cleanup
	trap - EXIT
	[[ -f $player_log ]] && cp -p "$player_log" "$current_run/steamvr-tutorial-player.log"
	cp -p "$manifest" "$current_run/appmanifest_250820.acf"
	{
		printf 'steamvr_appid=250820\n'
		printf 'steamvr_buildid=23791826\n'
		printf 'unity_version=2019.3.1f1\n'
		printf 'executable_hash=%s\n' "$expected_executable_hash"
		printf 'unity_player_hash=%s\n' "$expected_unity_hash"
		printf 'stock_openvr_hash=%s\n' "$expected_openvr_hash"
		printf 'installed_tree_hash_before=%s\n' "$installed_tree_before"
		printf 'installed_tree_hash_after=%s\n' "$installed_tree_after"
		printf 'work_copy_removed=%s\n' "$([[ ! -e $work_root ]] && echo true || echo false)"
		printf 'expected_common_verdict=fail\n'
		printf 'expected_common_reason=rgba-same-texture-not-native-pool\n'
		printf 'control_target_calls=%s\n' "$control_calls"
		printf 'control_monitor_status=%s\n' "$monitor_status"
		printf 'control_tail_calls=%s\n' "$control_tail_calls"
		printf 'control_tail_frames=%s\n' "$control_tail_frames"
		printf 'control_tail_fps=%s\n' "$control_tail_fps"
		printf 'source_contract_pass=%s\n' "$source_contract_pass"
		printf 'common_runner_status=%s\n' "$common_status"
		printf 'control_verdict=%s\n' "$control_verdict"
	} >"$current_run/steamvr-tutorial-profile.txt"
	printf '%s\n' "$control_verdict" >"$current_run/control-verdict.txt"
else
	cleanup
	trap - EXIT
fi

[[ $control_verdict == pass ]]
