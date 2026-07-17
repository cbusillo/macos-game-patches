#!/usr/bin/env bash

set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bottle_name=Steam
bottle="$HOME/Library/Application Support/CrossOver/Bottles/$bottle_name"
crossover_app=${CROSSOVER_APP:-/Applications/CrossOver.app}
alvr_checkout="${ALVR_CHECKOUT:-$repo/../alvr}"
alvr_bridge_root="$HOME/Library/Application Support/ALVR/macos_bridge"
alvr_state_root="${ALVR_NATIVE_RUNTIME_ROOT:-$repo/.code/state/alvr-native-runtime}"
probe_app_name=${ALVR_NATIVE_PROBE_APP_NAME:-Freedom Locomotion}
probe_process_pattern=${ALVR_NATIVE_PROBE_PROCESS_PATTERN:-'[F]reedomLocomotion'}
game_dir=${ALVR_NATIVE_PROBE_GAME_DIR:-"$bottle/drive_c/Program Files (x86)/Steam/steamapps/common/Freedom Locomotion VR/FreedomLocomotion/Binaries/Win64"}
engine_dir=${ALVR_NATIVE_PROBE_OPENVR_DIR:-"$bottle/drive_c/Program Files (x86)/Steam/steamapps/common/Freedom Locomotion VR/Engine/Binaries/ThirdParty/OpenVR/OpenVRv1_0_2/Win64"}
game_executable=${ALVR_NATIVE_PROBE_EXECUTABLE:-"$bottle/drive_c/Program Files (x86)/Steam/steamapps/common/Freedom Locomotion VR/FreedomLocomotion.exe"}
game_workdir=${ALVR_NATIVE_PROBE_WORKDIR:-"$(dirname "$game_executable")"}
probe_arguments_text=${ALVR_NATIVE_PROBE_ARGUMENTS:-}
legacy_probe_argument=${ALVR_NATIVE_PROBE_ARGUMENT:-}
probe_extra_env=${ALVR_NATIVE_PROBE_EXTRA_ENV:-}
probe_launcher_source=${ALVR_NATIVE_PROBE_LAUNCHER_SOURCE:-}
moltenvk="$crossover_app/Contents/SharedSupport/CrossOver/lib64/libMoltenVK.dylib"
patched_moltenvk="$repo/.code/vendor/crossover-26.2.0/source/sources/moltenvk/Package/Release/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"
dxvk_dir="$repo/.code/probes/008-real-openvr-iosurface/dxvk-d93568f1-build/src"
fake_runtime_source="$repo/tools/fake_openvr_real.cpp"
native_bridge="$alvr_checkout/target/release/alvr_macos_bridge"
native_bridge_codesign_identity='Developer ID Application: Shiny Computers Leasing LLC (MM5YXC7T6E)'
native_bridge_bundle_id=com.alvr.macos-bridge.iosurface
native_bridge_bundle="$repo/.code/state/alvr-macos-bridge/ALVRMacOSBridge.app"
legacy_native_bridge_bundles=(
	"$HOME/Applications/ALVRMacOSBridge.app"
	"$repo/.code/state/ALVRMacOSBridge.app"
)
launch_services_register=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
wine_source="$repo/.code/vendor/crossover-26.2.0/source/sources/wine"
wine_build="$repo/.code/vendor/crossover-26.2.0/build"
bridge_build="$wine_build/dlls/alvr_iosurface_bridge"
wine_bridge_source="$wine_source/dlls/alvr_iosurface_bridge"
cxstart="$crossover_app/Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/cxstart"
wineserver="$crossover_app/Contents/SharedSupport/CrossOver/CrossOver-Hosted Application/wineserver"
probe_dir="$bottle/drive_c/alvr-probes"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_root=${ALVR_NATIVE_PROBE_OUTPUT_ROOT:-"$repo/.code/probes/009-production-iosurface-pool"}
run_dir="$run_root/real-native-encode-$timestamp"
desktop_warmup_file="$probe_dir/desktop-warmup-$timestamp.txt"
backup_dir="$run_dir/backups"
build_dir="$run_dir/build"
bridge_root="$build_dir/bridge"
alvr_runtime_root="$run_dir/alvr-root"
shim="$build_dir/openvr_api.dll"
fake_runtime="$build_dir/openvr_api.real.dll"
native_bridge_evidence_dir="$build_dir/native-bridge-evidence"
native_bridge_evidence="$native_bridge_evidence_dir/alvr_macos_bridge"
native_bridge_info_evidence="$native_bridge_evidence_dir/Info.plist"
native_bridge_install_staging="$build_dir/native-bridge-install"
native_bridge_install_program="$native_bridge_install_staging/Contents/MacOS/alvr_macos_bridge"
native_bridge_program="$native_bridge_bundle/Contents/MacOS/alvr_macos_bridge"
native_bridge_signature_identifier=
native_bridge_signature_team=
native_bridge_signature_cdhash=
oversize_probe="$build_dir/mach_service_oversize_probe"
wine_bridge_backup="$backup_dir/wine-alvr-iosurface-bridge"
launch_agent_label=com.alvr.macos-bridge.iosurface
launch_agent_domain="gui/$UID"
launch_agent_target="$launch_agent_domain/$launch_agent_label"
runtime_state_root="$HOME/Library/Application Support/alvr/macos_bridge"
run_lock="$runtime_state_root/native-probe.lock"
launch_agent_plist="$runtime_state_root/$launch_agent_label.plist"
launch_agent_evidence="$run_dir/$launch_agent_label.plist"
service_name=$launch_agent_label
nonce="$(date +%s)$$"
moltenvk_signature_identifier=
moltenvk_signature_cdhash=
moltenvk_prewarm_seconds=
native_frames=${ALVR_NATIVE_PROBE_FRAMES:-300}
native_connect=${ALVR_NATIVE_PROBE_CONNECT:-false}
pressure_pause_ms=${ALVR_NATIVE_PROBE_PRESSURE_PAUSE_MS:-0}
fake_wait_get_poses_sleep_ms=${ALVR_FAKE_WAIT_GET_POSES_SLEEP_MS:-}
expected_source_transition=${ALVR_NATIVE_PROBE_EXPECT_SOURCE_TRANSITION:-}
producer_min_fps=${ALVR_NATIVE_PROBE_MIN_PRODUCER_FPS:-89.5}
producer_max_fps=${ALVR_NATIVE_PROBE_MAX_PRODUCER_FPS:-90.5}
fake_pacing_mode=deadline
[[ -n $fake_wait_get_poses_sleep_ms ]] && fake_pacing_mode=fixed-sleep
avp_device_selector=${ALVR_AVP_DEVICE_ID:-}
avp_bundle_id=com.shinycomputers.probe.alvrclient
avp_console_log="$run_dir/avp-client-live.log"
avp_console_normalized_log="$run_dir/avp-client-console-normalized.log"
avp_device_id=
avp_device_udid=
avp_device_name=
avp_app_url=
avp_app_version=
avp_app_build=
avp_app_executable=
avp_expected_protocol=
avp_client_id=
avp_client_ip=
avp_client_protocol=
avp_console_pid=
avp_remote_pid=
avp_client_owned=0
avp_client_ready=0
avp_client_stopped=0
avp_client_launch_epoch_ms=0
avp_client_ready_epoch_ms=0
avp_client_ready_latency_ms=0
avp_connection_gate_epoch_ms=0
avp_client_connection_epoch_ms=0
avp_client_connection_latency_ms=0
avp_sink_connection_epoch_ms=0
avp_sink_connection_latency_ms=0
avp_post_host_observed=0
avp_post_host_stream_stopped=0
avp_post_host_stale_ipd=0
avp_post_host_stale_origin=0
avp_post_host_stale_format=0
avp_post_host_status=0
avp_post_host_baseline_line=0
avp_session_seeded=0
avp_session_identity_ok=0
source_width=${ALVR_NATIVE_PROBE_SOURCE_WIDTH:-3240}
source_height=${ALVR_NATIVE_PROBE_SOURCE_HEIGHT:-1800}
output_width=${ALVR_NATIVE_PROBE_OUTPUT_WIDTH:-2880}
output_height=${ALVR_NATIVE_PROBE_OUTPUT_HEIGHT:-1792}
launcher_pid=
launch_agent_loaded=0
launch_agent_bootout_ok=0
launchd_stale_job_found=0
launchd_stale_job_owned=0
launchd_stale_job_booted_out=0
launchd_start_identity_ok=0
launchd_exit_identity_ok=0
launchd_bootout_identity_ok=0
desktop_warmup_pid=
desktop_explorer_pid=
pressure_applied=0
restored=0
mutations_started=0
run_lock_acquired=0
wine_bridge_source_existed=0
wine_bridge_source_mutated=0
probe_arguments=()

stock_moltenvk_hash=5c370edf330a126e4605aaf5cd156521197b0fdbd208b3e0a7931f3b8e6c5056
stock_openvr_hash=${ALVR_NATIVE_PROBE_STOCK_OPENVR_HASH:-d793e2a76a61296dc5bce5e6b8dc32f4f3096743aba10c5bac2eb465e635850c}

if [[ -n $probe_arguments_text && -n $legacy_probe_argument ]]; then
	echo "set only one of ALVR_NATIVE_PROBE_ARGUMENTS or ALVR_NATIVE_PROBE_ARGUMENT" >&2
	exit 1
fi
if [[ $probe_arguments_text == *$'\n'* ]]; then
	echo "ALVR_NATIVE_PROBE_ARGUMENTS must be a single whitespace-separated line" >&2
	exit 1
fi
if [[ -n $probe_arguments_text ]]; then
	read -r -a probe_arguments <<<"$probe_arguments_text"
elif [[ -n $legacy_probe_argument ]]; then
	probe_arguments+=("$legacy_probe_argument")
fi

[[ $native_frames =~ ^[1-9][0-9]*$ ]] || {
	echo "ALVR_NATIVE_PROBE_FRAMES must be a positive integer" >&2
	exit 1
}
case "$native_connect" in
true | false) ;;
*)
	echo "ALVR_NATIVE_PROBE_CONNECT must be true or false" >&2
	exit 1
	;;
esac
[[ $pressure_pause_ms =~ ^[0-9]+$ ]] || {
	echo "ALVR_NATIVE_PROBE_PRESSURE_PAUSE_MS must be a nonnegative integer" >&2
	exit 1
}
while [[ $pressure_pause_ms == 0* && ${#pressure_pause_ms} -gt 1 ]]; do
	pressure_pause_ms=${pressure_pause_ms#0}
done

if [[ ${#pressure_pause_ms} -gt 5 ]] || ((pressure_pause_ms > 60000)); then
	echo "ALVR_NATIVE_PROBE_PRESSURE_PAUSE_MS must not exceed 60000" >&2
	exit 1
fi
if [[ -n $fake_wait_get_poses_sleep_ms ]]; then
	[[ $fake_wait_get_poses_sleep_ms =~ ^[0-9]+$ ]] || {
		echo "ALVR_FAKE_WAIT_GET_POSES_SLEEP_MS must be a nonnegative integer" >&2
		exit 1
	}
	while [[ $fake_wait_get_poses_sleep_ms == 0* && ${#fake_wait_get_poses_sleep_ms} -gt 1 ]]; do
		fake_wait_get_poses_sleep_ms=${fake_wait_get_poses_sleep_ms#0}
	done
	if [[ ${#fake_wait_get_poses_sleep_ms} -gt 5 ]] ||
		((fake_wait_get_poses_sleep_ms > 60000)); then
		echo "ALVR_FAKE_WAIT_GET_POSES_SLEEP_MS must not exceed 60000" >&2
		exit 1
	fi
fi
if [[ -n $expected_source_transition &&
	! $expected_source_transition =~ ^[1-9][0-9]*x[1-9][0-9]*$ ]]; then
	echo "ALVR_NATIVE_PROBE_EXPECT_SOURCE_TRANSITION must be WIDTHxHEIGHT" >&2
	exit 1
fi
if [[ $native_connect == true && $pressure_pause_ms -ne 0 ]]; then
	echo "pressure pause is only supported for disconnected validation" >&2
	exit 1
fi
if ! awk -v minimum="$producer_min_fps" -v maximum="$producer_max_fps" \
	'BEGIN { exit !(minimum > 0 && maximum >= minimum) }'; then
	echo "producer FPS bounds must be positive and ordered" >&2
	exit 1
fi
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

game_process_pids() {
	local pid
	while IFS= read -r pid; do
		if process_uses_bottle "$pid"; then
			printf '%s\n' "$pid"
		fi
	done < <(pgrep -f "$probe_process_pattern" 2>/dev/null || true)
}

shutdown_bottle() {
	local log_file=$1
	local pid
	local remaining

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

start_desktop_warmup() {
	local deadline=$((SECONDS + 300))
	local explorer_pid
	local pid
	local ready=0
	local marker=${desktop_warmup_file##*/}

	printf 'ALVR Wine desktop warmup\r\n' >"$desktop_warmup_file"
	"$cxstart" --bottle "$bottle_name" --no-update --no-gui --no-wait \
		notepad.exe "$desktop_warmup_file" \
		>"$run_dir/bottle-desktop-warmup.log" 2>&1
	while ((SECONDS < deadline)); do
		if [[ -z $desktop_warmup_pid ]]; then
			for pid in $(pgrep -f '[n]otepad.exe' 2>/dev/null || true); do
				if process_uses_bottle "$pid" &&
					ps -p "$pid" -o command= | rg -Fq "$marker"; then
					desktop_warmup_pid=$pid
					break
				fi
			done
		fi
		if [[ -n $desktop_warmup_pid ]]; then
			for explorer_pid in $(pgrep -f '[e]xplorer.exe /desktop' 2>/dev/null || true); do
				if process_uses_bottle "$explorer_pid" &&
					lsof -p "$explorer_pid" 2>/dev/null | rg -Fq "$moltenvk"; then
					desktop_explorer_pid=$explorer_pid
					ready=1
					break 2
				fi
			done
		fi
		sleep 0.25
	done
	if [[ $ready -ne 1 ]]; then
		cat "$run_dir/bottle-desktop-warmup.log" >&2
		echo "Wine desktop warmup did not load patched MoltenVK" >&2
		return 1
	fi
	printf 'desktop_warmup_pid=%s desktop_explorer_pid=%s\n' \
		"$desktop_warmup_pid" "$desktop_explorer_pid" \
		>>"$run_dir/bottle-desktop-warmup.log"
	sleep 1
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

restore_wine_bridge_source() {
	if [[ $wine_bridge_source_mutated -eq 0 ]]; then
		return 0
	fi
	rm -rf "$wine_bridge_source" || return 1
	if [[ $wine_bridge_source_existed -eq 1 ]]; then
		mkdir -p "$wine_bridge_source" || return 1
		rsync -a "$wine_bridge_backup/" "$wine_bridge_source/" || return 1
	fi
	wine_bridge_source_mutated=0
}

write_state_manifest() {
	local destination=$1
	local file
	{
		for file in session.json session_old.json; do
			if [[ -f $alvr_state_root/$file ]]; then
				shasum -a 256 "$alvr_state_root/$file"
			else
				printf 'absent=%s\n' "$alvr_state_root/$file"
			fi
		done
	} >"$destination"
}

epoch_milliseconds() {
	perl -MTime::HiRes=time -e 'printf "%.0f\n", time * 1000'
}

run_devicectl_json() {
	local output=$1
	local log=$2
	shift 2

	rm -f "$output" "$log"
	if ! xcrun devicectl "$@" --json-output "$output" >"$log" 2>&1; then
		return 1
	fi
	jq -e '.info.outcome == "success"' "$output" >/dev/null
}

resolve_alvr_protocol_id() {
	local prerelease
	local version

	version=$(awk '
		$0 == "[workspace.package]" { in_workspace_package = 1; next }
		in_workspace_package && /^\[/ { exit }
		in_workspace_package && $1 == "version" {
			gsub(/"/, "", $3)
			print $3
			exit
		}
	' "$alvr_checkout/Cargo.toml")
	[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-].*)?$ ]] || return 1
	avp_expected_protocol=${version%%.*}
	if [[ $version == *-* ]]; then
		prerelease=${version#*-}
		prerelease=${prerelease%%+*}
		avp_expected_protocol+="-$prerelease"
	fi
}

normalize_avp_console_log() {
	local staging="$avp_console_normalized_log.tmp"

	[[ -f $avp_console_log ]] || return 1
	tr -d '\r' <"$avp_console_log" >"$staging"
	mv -f "$staging" "$avp_console_normalized_log"
}

valid_avp_ipv4() {
	awk -F. '
		NF != 4 { exit 1 }
		{
			for (field = 1; field <= 4; field++) {
				if ($field !~ /^[0-9]+$/ || $field < 0 || $field > 255) exit 1
			}
			if ($1 == 0 || $1 == 127 || $1 >= 224) exit 1
			if ($1 == 169 && $2 == 254) exit 1
		}
	' <<<"$1"
}

snapshot_avp_processes() {
	local destination=$1

	run_devicectl_json "$destination" "$destination.log" \
		device info processes --device "$avp_device_id"
}

avp_exact_process_count() {
	local snapshot=$1

	jq --arg executable "$avp_app_executable" \
		'[.result.runningProcesses[] | select(.executable == $executable)] | length' \
		"$snapshot"
}

avp_exact_process_pid() {
	local snapshot=$1

	jq -r --arg executable "$avp_app_executable" \
		'.result.runningProcesses[] | select(.executable == $executable) | .processIdentifier' \
		"$snapshot"
}

terminate_avp_process() {
	local kill_mode=$1
	local pid=$2
	local output=$3
	local arguments=(device process terminate --device "$avp_device_id" --pid "$pid")

	if [[ $kill_mode == kill ]]; then
		arguments+=(--kill)
	fi
	run_devicectl_json "$output" "$output.log" "${arguments[@]}"
}

resolve_avp_client() {
	local app_count
	local app_name
	local device_count
	local selector_filter
	local selected_filter

	resolve_alvr_protocol_id || {
		echo "could not derive the ALVR protocol ID" >&2
		return 1
	}
	run_devicectl_json "$run_dir/avp-devices.json" "$run_dir/avp-devices.log" \
		list devices || return 1
	selector_filter="(\$selector == \"\" or .identifier == \$selector or .hardwareProperties.udid == \$selector)"
	selected_filter=".hardwareProperties.platform == \"visionOS\" and
		.hardwareProperties.reality == \"physical\" and
		.deviceProperties.bootState == \"booted\" and
		.deviceProperties.ddiServicesAvailable == true and
		.deviceProperties.developerModeStatus == \"enabled\" and
		.connectionProperties.pairingState == \"paired\" and
		.connectionProperties.tunnelState == \"connected\" and $selector_filter"
	device_count=$(jq --arg selector "$avp_device_selector" \
		"[.result.devices[] | select($selected_filter)] | length" \
		"$run_dir/avp-devices.json")
	if [[ $device_count -ne 1 ]]; then
		echo "connected validation requires exactly one eligible physical visionOS device; found $device_count" >&2
		return 1
	fi
	jq --arg selector "$avp_device_selector" \
		".result.devices[] | select($selected_filter)" \
		"$run_dir/avp-devices.json" >"$run_dir/avp-device-selected.json"
	avp_device_id=$(jq -r '.identifier' "$run_dir/avp-device-selected.json")
	avp_device_udid=$(jq -r '.hardwareProperties.udid' "$run_dir/avp-device-selected.json")
	avp_device_name=$(jq -r '.deviceProperties.name' "$run_dir/avp-device-selected.json")

	run_devicectl_json "$run_dir/avp-lock-state.json" "$run_dir/avp-lock-state.log" \
		device info lockState --device "$avp_device_id" || return 1
	jq -e --arg identifier "$avp_device_id" '
		.result.deviceIdentifier == $identifier and
		.result.passcodeRequired == false and
		.result.unlockedSinceBoot == true
	' "$run_dir/avp-lock-state.json" >/dev/null || {
		echo "the selected Vision Pro must be awake and unlocked" >&2
		return 1
	}

	run_devicectl_json "$run_dir/avp-app.json" "$run_dir/avp-app.log" \
		device info apps --device "$avp_device_id" --bundle-id "$avp_bundle_id" || return 1
	app_count=$(jq --arg bundle "$avp_bundle_id" \
		'[.result.apps[] | select(.bundleIdentifier == $bundle)] | length' \
		"$run_dir/avp-app.json")
	if [[ $app_count -ne 1 ]] ||
		! jq -e --arg identifier "$avp_device_id" \
			'.result.deviceIdentifier == $identifier' "$run_dir/avp-app.json" >/dev/null; then
		echo "the selected Vision Pro must have exactly one $avp_bundle_id app installed" >&2
		return 1
	fi
	avp_app_url=$(jq -r --arg bundle "$avp_bundle_id" \
		'.result.apps[] | select(.bundleIdentifier == $bundle) | .url' \
		"$run_dir/avp-app.json")
	avp_app_version=$(jq -r --arg bundle "$avp_bundle_id" \
		'.result.apps[] | select(.bundleIdentifier == $bundle) | .version' \
		"$run_dir/avp-app.json")
	avp_app_build=$(jq -r --arg bundle "$avp_bundle_id" \
		'.result.apps[] | select(.bundleIdentifier == $bundle) | .bundleVersion' \
		"$run_dir/avp-app.json")
	[[ $avp_app_url == file://*.app/ ]] || return 1
	app_name=$(basename "${avp_app_url%/}")
	avp_app_executable="${avp_app_url%/}/${app_name%.app}"
}

stop_existing_avp_client() {
	local pid
	local process_count
	local snapshot="$run_dir/avp-processes-before-launch.json"

	snapshot_avp_processes "$snapshot" || return 1
	while IFS= read -r pid; do
		[[ $pid =~ ^[1-9][0-9]*$ ]] || continue
		terminate_avp_process term "$pid" "$run_dir/avp-terminate-existing-$pid.json" || return 1
	done < <(avp_exact_process_pid "$snapshot")
	for _ in $(seq 1 100); do
		snapshot_avp_processes "$run_dir/avp-processes-after-existing-termination.json" || return 1
		process_count=$(avp_exact_process_count \
			"$run_dir/avp-processes-after-existing-termination.json")
		[[ $process_count -eq 0 ]] && return 0
		sleep 0.1
	done
	echo "the stale ALVR client process did not terminate" >&2
	return 1
}

refresh_avp_publisher_identity() {
	local id_count
	local ip_count
	local listener_state
	local protocol_count
	local published_id
	local published_ip
	local published_protocol

	normalize_avp_console_log || return 1
	id_count=$(sed -nE 's/.*mDNS publish attempt #[0-9]+:.* device_id=([^ ]+).*/\1/p' \
		"$avp_console_normalized_log" | awk 'NF { seen[$0] = 1 } END { for (value in seen) count++; print count + 0 }')
	protocol_count=$(sed -nE 's/.*mDNS publish attempt #[0-9]+:.* protocol=([^ ]+) .*/\1/p' \
		"$avp_console_normalized_log" | awk 'NF { seen[$0] = 1 } END { for (value in seen) count++; print count + 0 }')
	ip_count=$(sed -nE 's/^IP: ([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)$/\1/p' \
		"$avp_console_normalized_log" | awk 'NF { seen[$0] = 1 } END { for (value in seen) count++; print count + 0 }')
	if [[ $id_count -gt 1 || $protocol_count -gt 1 || $ip_count -gt 1 ]]; then
		return 2
	fi
	[[ $id_count -eq 1 && $protocol_count -eq 1 && $ip_count -eq 1 ]] || return 1
	published_id=$(sed -nE 's/.*mDNS publish attempt #[0-9]+:.* device_id=([^ ]+).*/\1/p' \
		"$avp_console_normalized_log" | tail -n 1)
	published_protocol=$(sed -nE 's/.*mDNS publish attempt #[0-9]+:.* protocol=([^ ]+) .*/\1/p' \
		"$avp_console_normalized_log" | tail -n 1)
	published_ip=$(sed -nE 's/^IP: ([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)$/\1/p' \
		"$avp_console_normalized_log" | tail -n 1)
	listener_state=$(sed -nE 's/^mDNS NWListener state: ([^ ]+).*/\1/p' \
		"$avp_console_normalized_log" | tail -n 1)
	[[ $published_id == *.alvr && $published_protocol == "$avp_expected_protocol" ]] || return 2
	valid_avp_ipv4 "$published_ip" || return 2
	[[ $listener_state == ready ]] || return 1
	avp_client_id=$published_id
	avp_client_protocol=$published_protocol
	avp_client_ip=$published_ip
}

avp_console_process_is_owned() {
	local command
	local parent

	[[ $avp_console_pid =~ ^[1-9][0-9]*$ ]] || return 1
	parent=$(ps -p "$avp_console_pid" -o ppid= 2>/dev/null | tr -d ' ')
	command=$(ps -p "$avp_console_pid" -o command= 2>/dev/null || true)
	[[ $parent == "$$" && $command == *"devicectl device process launch"* &&
		$command == *"$avp_bundle_id"* ]]
}

launch_avp_client() {
	local process_count
	local publisher_status

	stop_existing_avp_client || return 1
	: >"$avp_console_log"
	avp_client_launch_epoch_ms=$(epoch_milliseconds)
	xcrun devicectl device process launch \
		--device "$avp_device_id" \
		--activate \
		--terminate-existing \
		--console \
		"$avp_bundle_id" >"$avp_console_log" 2>&1 &
	avp_console_pid=$!
	avp_client_owned=1

	for _ in $(seq 1 100); do
		snapshot_avp_processes "$run_dir/avp-processes-after-launch.json" || return 1
		process_count=$(avp_exact_process_count "$run_dir/avp-processes-after-launch.json")
		if [[ $process_count -eq 1 ]]; then
			avp_remote_pid=$(avp_exact_process_pid "$run_dir/avp-processes-after-launch.json")
			break
		fi
		[[ $process_count -le 1 ]] || return 1
		avp_console_process_is_owned || return 1
		sleep 0.1
	done
	[[ $avp_remote_pid =~ ^[1-9][0-9]*$ ]] || return 1

	for _ in $(seq 1 1800); do
		if ! avp_console_process_is_owned; then
			cat "$avp_console_log" >&2 || true
			echo "the runner-owned ALVR console process exited before readiness" >&2
			return 1
		fi
		if refresh_avp_publisher_identity; then
			break
		else
			publisher_status=$?
			if [[ $publisher_status -ne 1 ]]; then
				cat "$avp_console_log" >&2 || true
				echo "the ALVR client published ambiguous or incompatible identity data" >&2
				return 1
			fi
		fi
		sleep 0.1
	done
	if [[ -z $avp_client_id || -z $avp_client_ip ]]; then
		cat "$avp_console_log" >&2 || true
		echo "the ALVR client did not publish a ready mDNS identity" >&2
		return 1
	fi
	snapshot_avp_processes "$run_dir/avp-processes-after-publisher-ready.json" || return 1
	process_count=$(avp_exact_process_count "$run_dir/avp-processes-after-publisher-ready.json")
	[[ $process_count -eq 1 &&
		$(avp_exact_process_pid "$run_dir/avp-processes-after-publisher-ready.json") == "$avp_remote_pid" ]] || return 1
	avp_client_ready_epoch_ms=$(epoch_milliseconds)
	avp_client_ready_latency_ms=$((avp_client_ready_epoch_ms - avp_client_launch_epoch_ms))
	avp_client_ready=1
}

validate_owned_avp_client_running() {
	local snapshot=$1
	local process_count
	local process_pid

	avp_console_process_is_owned || return 1
	[[ $avp_remote_pid =~ ^[1-9][0-9]*$ ]] || return 1
	snapshot_avp_processes "$snapshot" || return 1
	process_count=$(avp_exact_process_count "$snapshot")
	process_pid=$(avp_exact_process_pid "$snapshot")
	[[ $process_count -eq 1 && $process_pid == "$avp_remote_pid" ]]
}

seed_avp_client_session() {
	local mode
	local session="$alvr_runtime_root/session.json"
	local staging="$alvr_runtime_root/session.json.connected.$$"

	[[ -f $session ]] || {
		echo "connected validation requires an existing artifact-local ALVR session" >&2
		return 1
	}
	jq --arg id "$avp_client_id" --arg ip "$avp_client_ip" '
		.client_connections = (
			(.client_connections // {})
			| del(.wired)
			| with_entries(
				if .key == $id then .
				else
					.value.current_ip = (if .value.current_ip == $ip then null else .value.current_ip end)
					| .value.manual_ips = []
				end
			)
			| .[$id] = (
				(.[$id] // {
					display_name: "Apple Vision Pro",
					current_ip: null,
					manual_ips: [],
					trusted: true,
					connection_state: "Disconnected"
				})
				| .display_name = (if .display_name == null or .display_name == "Unknown" then "Apple Vision Pro" else .display_name end)
				| .current_ip = $ip
				| .manual_ips = [$ip]
				| .trusted = true
				| .connection_state = "Disconnected"
			)
		)
	' "$session" >"$staging"
	jq -e --arg id "$avp_client_id" --arg ip "$avp_client_ip" '
		.client_connections[$id].trusted == true and
		.client_connections[$id].current_ip == $ip and
		.client_connections[$id].manual_ips == [$ip] and
		(.client_connections | has("wired") | not) and
		([.client_connections | to_entries[] | select(.key != $id) | .value.manual_ips[]?] | length == 0) and
		([.client_connections | to_entries[] | select(.key != $id and .value.current_ip == $ip)] | length == 0)
	' "$staging" >/dev/null
	mode=$(stat -f '%Lp' "$session")
	chmod "$mode" "$staging"
	mv -f "$staging" "$session"
	cp -p "$session" "$run_dir/alvr-session-seeded.json"
	avp_session_seeded=1
}

validate_avp_client_before_bootstrap() {
	local current_id=$avp_client_id
	local current_ip=$avp_client_ip
	local current_protocol=$avp_client_protocol

	refresh_avp_publisher_identity || return 1
	[[ $avp_client_id == "$current_id" && $avp_client_ip == "$current_ip" &&
		$avp_client_protocol == "$current_protocol" ]] || return 1
	validate_owned_avp_client_running "$run_dir/avp-processes-before-bootstrap.json"
}

capture_avp_connection_state() {
	local now

	normalize_avp_console_log || return 1
	now=$(epoch_milliseconds)
	if [[ $avp_connection_gate_epoch_ms -eq 0 ]]; then
		if ! rg -q 'native_source producer handshake accepted ' \
			"$run_dir/native-bridge.log"; then
			return 0
		fi
		avp_connection_gate_epoch_ms=$now
	fi
	if [[ $avp_client_connection_epoch_ms -eq 0 ]] &&
		rg -q '^Successful connection!$' "$avp_console_normalized_log"; then
		avp_client_connection_epoch_ms=$now
		avp_client_connection_latency_ms=$((now - avp_connection_gate_epoch_ms))
	fi
	if [[ $avp_sink_connection_epoch_ms -eq 0 ]] &&
		rg -q 'alvr_sink connected epoch=1 ' "$run_dir/native-bridge.log"; then
		avp_sink_connection_epoch_ms=$now
		avp_sink_connection_latency_ms=$((now - avp_connection_gate_epoch_ms))
	fi
	return 0
}

wait_for_avp_connection() {
	capture_avp_connection_state || return 1
	[[ $avp_client_connection_epoch_ms -gt 0 &&
		$avp_sink_connection_epoch_ms -gt 0 &&
		$avp_client_connection_latency_ms -le 2000 &&
		$avp_sink_connection_latency_ms -le 5000 ]] || return 1
	avp_post_host_baseline_line=$(wc -l <"$avp_console_normalized_log" | tr -d ' ')
}

observe_avp_post_host() {
	local normalized_start_line
	local relative_stream_stopped_line
	local segment_start_line
	local deadline
	local now

	normalize_avp_console_log || return 1
	normalized_start_line=$avp_post_host_baseline_line
	if [[ $normalized_start_line -eq 0 ]]; then
		normalized_start_line=$(wc -l <"$avp_console_normalized_log" | tr -d ' ')
	fi
	deadline=$(($(epoch_milliseconds) + 10000))
	while :; do
		normalize_avp_console_log || return 1
		relative_stream_stopped_line=$(tail -n "+$((normalized_start_line + 1))" \
			"$avp_console_normalized_log" | awk '/^streaming stopped/ { print NR; exit }')
		if [[ $relative_stream_stopped_line =~ ^[1-9][0-9]*$ ]]; then
			avp_post_host_stream_stopped=1
			break
		fi
		avp_console_process_is_owned || return 1
		now=$(epoch_milliseconds)
		[[ $now -le $deadline ]] || return 1
		sleep 0.1
	done
	validate_owned_avp_client_running \
		"$run_dir/avp-processes-post-host-before-observation.json" || return 1
	sleep 3
	validate_owned_avp_client_running \
		"$run_dir/avp-processes-post-host-after-observation.json" || return 1
	normalize_avp_console_log || return 1
	segment_start_line=$((normalized_start_line + relative_stream_stopped_line))
	tail -n "+$segment_start_line" "$avp_console_normalized_log" \
		>"$run_dir/avp-client-post-host.log"
	avp_post_host_stale_ipd=$(rg -c 'IPD is bad, no frame' \
		"$run_dir/avp-client-post-host.log" 2>/dev/null || true)
	avp_post_host_stale_origin=$(rg -c 'Origin is bad, no frame' \
		"$run_dir/avp-client-post-host.log" 2>/dev/null || true)
	avp_post_host_stale_format=$(rg -c 'Missing video format, no frame' \
		"$run_dir/avp-client-post-host.log" 2>/dev/null || true)
	avp_post_host_stale_ipd=${avp_post_host_stale_ipd:-0}
	avp_post_host_stale_origin=${avp_post_host_stale_origin:-0}
	avp_post_host_stale_format=${avp_post_host_stale_format:-0}
	avp_post_host_observed=1
	[[ $avp_post_host_stale_ipd -eq 0 && $avp_post_host_stale_origin -eq 0 &&
		$avp_post_host_stale_format -eq 0 ]]
}

validate_avp_session_identity() {
	local session="$alvr_runtime_root/session.json"

	[[ -f $session ]] || return 1
	jq -e --arg id "$avp_client_id" --arg ip "$avp_client_ip" '
		.client_connections[$id].trusted == true and
		.client_connections[$id].current_ip == $ip and
		.client_connections[$id].manual_ips == [$ip] and
		([.client_connections | to_entries[] | select(.key != $id and .value.current_ip == $ip)] | length == 0) and
		([.client_connections | to_entries[] | select(.key != $id) | .value.manual_ips[]?] | length == 0)
	' "$session" >/dev/null
}

stop_owned_avp_client() {
	local current_pid
	local process_count
	local snapshot="$run_dir/avp-processes-before-owned-stop.json"

	if [[ $avp_client_owned -ne 1 ]]; then
		return 0
	fi
	if avp_console_process_is_owned; then
		kill -TERM "$avp_console_pid" 2>/dev/null || true
	fi
	for _ in $(seq 1 100); do
		if snapshot_avp_processes "$snapshot"; then
			process_count=$(avp_exact_process_count "$snapshot")
			[[ $process_count -eq 0 ]] && break
			[[ $process_count -eq 1 ]] || return 1
			current_pid=$(avp_exact_process_pid "$snapshot")
			if [[ $avp_remote_pid =~ ^[1-9][0-9]*$ ]]; then
				[[ $current_pid == "$avp_remote_pid" ]] || return 1
			else
				avp_remote_pid=$current_pid
			fi
		fi
		sleep 0.1
	done
	if [[ ${process_count:-1} -ne 0 ]]; then
		terminate_avp_process term "$avp_remote_pid" "$run_dir/avp-terminate-owned.json" || return 1
		for _ in $(seq 1 50); do
			snapshot_avp_processes "$snapshot" || return 1
			process_count=$(avp_exact_process_count "$snapshot")
			[[ $process_count -eq 0 ]] && break
			[[ $process_count -eq 1 ]] || return 1
			current_pid=$(avp_exact_process_pid "$snapshot")
			[[ $current_pid == "$avp_remote_pid" ]] || return 1
			sleep 0.1
		done
	fi
	if [[ ${process_count:-1} -ne 0 ]]; then
		terminate_avp_process kill "$avp_remote_pid" "$run_dir/avp-kill-owned.json" || return 1
	fi
	for _ in $(seq 1 50); do
		snapshot_avp_processes "$run_dir/avp-processes-after-owned-stop.json" || return 1
		process_count=$(avp_exact_process_count "$run_dir/avp-processes-after-owned-stop.json")
		[[ $process_count -eq 0 ]] && break
		sleep 0.1
	done
	[[ $process_count -eq 0 ]] || return 1
	if avp_console_process_is_owned; then
		kill -TERM "$avp_console_pid" 2>/dev/null || true
		for _ in $(seq 1 50); do
			avp_console_process_is_owned || break
			sleep 0.1
		done
	fi
	if avp_console_process_is_owned; then
		kill -KILL "$avp_console_pid" 2>/dev/null || true
	fi
	wait "$avp_console_pid" 2>/dev/null || true
	avp_client_owned=0
	avp_client_stopped=1
}

snapshot_source_file() {
	local root=$1
	local label=$2
	local relative=$3
	local target="$run_dir/source-snapshot/$label/$relative"
	[[ -f $root/$relative ]] || {
		echo "missing source input: $root/$relative" >&2
		return 1
	}
	mkdir -p "$(dirname "$target")"
	cp -p "$root/$relative" "$target"
	printf '%s  %s/%s\n' "$(hash_file "$root/$relative")" "$label" "$relative"
}

stop_game() {
	local pid
	local remaining
	for pid in $(game_process_pids); do
		kill -TERM "$pid" 2>/dev/null || true
	done
	sleep 1
	remaining=$(game_process_pids)
	for pid in $remaining; do
		kill -KILL "$pid" 2>/dev/null || true
	done
}

stop_pid() {
	local pid=${1:-}
	if [[ -z $pid ]]; then
		return
	fi
	kill -CONT "$pid" 2>/dev/null || true
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

launch_agent_pid() {
	/bin/launchctl print "$launch_agent_target" 2>/dev/null |
		awk '$1 == "pid" && $2 == "=" { print $3; exit }'
}

launch_agent_state_value() {
	local key=$1
	local state_file=$2

	awk -v key="$key" '
		{
			line = $0
			sub(/^[[:space:]]+/, "", line)
			prefix = key " = "
			if (index(line, prefix) == 1) {
				print substr(line, length(prefix) + 1)
				exit
			}
		}
	' "$state_file"
}

paths_refer_to_same_file() {
	local left=$1
	local right=$2

	[[ $left == "$right" ]] ||
		[[ -e $left && -e $right && $left -ef $right ]]
}

probe_owns_stale_launch_agent_state() {
	local state_file=$1
	local legacy_native_bridge_bundle
	local registered_path
	local registered_program

	registered_path=$(launch_agent_state_value path "$state_file")
	registered_program=$(launch_agent_state_value program "$state_file")
	paths_refer_to_same_file "$registered_path" "$launch_agent_plist" || return 1
	paths_refer_to_same_file "$registered_program" "$native_bridge_program" && return 0
	for legacy_native_bridge_bundle in "${legacy_native_bridge_bundles[@]}"; do
		paths_refer_to_same_file "$registered_program" \
			"$legacy_native_bridge_bundle/Contents/MacOS/alvr_macos_bridge" &&
			return 0
	done
	return 1
}

current_run_owns_launch_agent_state() {
	local state_file=$1
	local registered_path
	local registered_program

	registered_path=$(launch_agent_state_value path "$state_file")
	registered_program=$(launch_agent_state_value program "$state_file")
	paths_refer_to_same_file "$registered_path" "$launch_agent_plist" &&
		paths_refer_to_same_file "$registered_program" "$native_bridge_program"
}

launch_agent_state_is_single_run() {
	local state_file=$1

	[[ $(launch_agent_state_value runs "$state_file") == 1 ]]
}

validate_launch_agent_process_identity() {
	local pid=$1
	local actual_cdhash
	local actual_identifier
	local actual_program
	local actual_team

	actual_program=$(lsof -a -p "$pid" -d txt -Fn 2>/dev/null |
		awk '/^n/ && !found { print substr($0, 2); found = 1 }')
	actual_identifier=$(codesign -dv --verbose=4 "$actual_program" 2>&1 |
		sed -n 's/^Identifier=//p')
	actual_team=$(codesign -dv --verbose=4 "$actual_program" 2>&1 |
		sed -n 's/^TeamIdentifier=//p')
	actual_cdhash=$(codesign -dv --verbose=4 "$actual_program" 2>&1 |
		sed -n 's/^CDHash=//p')
	{
		printf 'pid=%s\n' "$pid"
		printf 'program=%s\n' "$actual_program"
		printf 'identifier=%s\n' "$actual_identifier"
		printf 'team=%s\n' "$actual_team"
		printf 'cdhash=%s\n' "$actual_cdhash"
	} >"$run_dir/launchd-process-identity.txt"
	[[ $actual_program == "$native_bridge_program" &&
		$actual_identifier == "$native_bridge_signature_identifier" &&
		$actual_team == "$native_bridge_signature_team" &&
		$actual_cdhash == "$native_bridge_signature_cdhash" ]]
}

signal_owned_launch_agent() {
	local signal_name=$1
	local state_file=$2
	local pid
	local state

	/bin/launchctl print "$launch_agent_target" >"$state_file" 2>&1 || return 1
	if ! current_run_owns_launch_agent_state "$state_file" ||
		! launch_agent_state_is_single_run "$state_file"; then
		echo "refusing to signal launchd job without current-run identity" \
			>>"$state_file"
		return 1
	fi
	pid=$(launch_agent_state_value pid "$state_file")
	state=$(launch_agent_state_value state "$state_file")
	[[ $pid =~ ^[1-9][0-9]*$ && $state == running ]] || return 1
	/bin/launchctl kill "$signal_name" "$launch_agent_target"
}

stop_owned_launch_agent_process() {
	local state_file="$run_dir/launchd-state-before-force-stop.txt"

	if ! /bin/launchctl print "$launch_agent_target" >"$state_file" 2>&1; then
		return 0
	fi
	if ! current_run_owns_launch_agent_state "$state_file" ||
		! launch_agent_state_is_single_run "$state_file"; then
		echo "refusing to stop launchd job without current-run identity" \
			>>"$state_file"
		return 1
	fi
	if [[ $(launch_agent_state_value state "$state_file") == running ]]; then
		signal_owned_launch_agent SIGCONT \
			"$run_dir/launchd-state-before-force-cont.txt" || return 1
		signal_owned_launch_agent SIGTERM \
			"$run_dir/launchd-state-before-force-term.txt" || return 1
		for _ in $(seq 1 50); do
			/bin/launchctl print "$launch_agent_target" >"$state_file" 2>&1 || break
			if ! current_run_owns_launch_agent_state "$state_file" ||
				! launch_agent_state_is_single_run "$state_file"; then
				return 1
			fi
			if [[ $(launch_agent_state_value state "$state_file") != running ]]; then
				break
			fi
			sleep 0.1
		done
		if [[ $(launch_agent_state_value state "$state_file") == running ]]; then
			signal_owned_launch_agent SIGKILL \
				"$run_dir/launchd-state-before-force-kill.txt" || return 1
		fi
	fi
	/bin/launchctl bootout "$launch_agent_domain" "$launch_agent_plist" \
		>"$run_dir/launchd-force-bootout.log" 2>&1 ||
		! /bin/launchctl print "$launch_agent_target" >/dev/null 2>&1
}

production_released_slot_count() {
	local log_file=$1

	awk '
		/iosurface pool release/ && /result=(pass|closed|dropped)/ {
			slot = -1
			for (field = 1; field <= NF; field++) {
				if ($field ~ /^slot=/) {
					split($field, value, "=")
					slot = value[2] + 0
				}
			}
			if (slot >= 0) released_slot[slot] = 1
		}
		END {
			for (slot in released_slot) count++
			print count + 0
		}
	' "$log_file" 2>/dev/null
}

write_launch_agent_plist() {
	local environment_json
	local mach_services_json
	local program_arguments_json

	environment_json=$(jq -cn \
		--arg bridge_root "$alvr_runtime_root" \
		--arg input iosurface \
		--arg width "$output_width" \
		--arg height "$output_height" \
		--arg fps 90 \
		--arg bitrate 50000000 \
		--arg frames "$native_frames" \
		--arg buffer_count 6 \
		--arg telemetry_interval 10 \
		--arg connect "$native_connect" \
		--arg source_width "$source_width" \
		--arg source_height "$source_height" \
		--arg service "$service_name" \
		--arg nonce "$nonce" \
		'{
			ALVR_BRIDGE_ROOT: $bridge_root,
			ALVR_BRIDGE_INPUT: $input,
			ALVR_BRIDGE_WIDTH: $width,
			ALVR_BRIDGE_HEIGHT: $height,
			ALVR_BRIDGE_FPS: $fps,
			ALVR_BRIDGE_BITRATE_BPS: $bitrate,
			ALVR_BRIDGE_FRAMES: $frames,
			ALVR_BRIDGE_BUFFER_COUNT: $buffer_count,
			ALVR_BRIDGE_TELEMETRY_INTERVAL: $telemetry_interval,
			ALVR_BRIDGE_CONNECT: $connect,
			ALVR_IOSURFACE_SOURCE_WIDTH: $source_width,
			ALVR_IOSURFACE_SOURCE_HEIGHT: $source_height,
			ALVR_IOSURFACE_POOL_SERVICE: $service,
			ALVR_IOSURFACE_POOL_NONCE: $nonce
		}')
	mach_services_json=$(jq -cn --arg service "$service_name" '{($service): true}')
	program_arguments_json=$(jq -cn --arg binary "$native_bridge_program" '[$binary]')

	rm -f "$launch_agent_plist"
	/usr/bin/plutil -create xml1 "$launch_agent_plist"
	/usr/bin/plutil -insert Label -string "$launch_agent_label" "$launch_agent_plist"
	/usr/bin/plutil -insert ProgramArguments -json "$program_arguments_json" "$launch_agent_plist"
	/usr/bin/plutil -insert EnvironmentVariables -json "$environment_json" "$launch_agent_plist"
	/usr/bin/plutil -insert MachServices -json "$mach_services_json" "$launch_agent_plist"
	/usr/bin/plutil -insert AssociatedBundleIdentifiers -json \
		"[\"$native_bridge_bundle_id\"]" "$launch_agent_plist"
	/usr/bin/plutil -insert ProcessType -string Interactive "$launch_agent_plist"
	/usr/bin/plutil -insert StandardOutPath -string "$run_dir/native-bridge.log" "$launch_agent_plist"
	/usr/bin/plutil -insert StandardErrorPath -string "$run_dir/native-bridge.log" "$launch_agent_plist"
	/usr/bin/plutil -lint "$launch_agent_plist" >"$run_dir/launchd-plist-lint.log"
	cp -p "$launch_agent_plist" "$launch_agent_evidence"
}

remove_stale_launch_agent() {
	local stale_path

	if /bin/launchctl print "$launch_agent_target" \
		>"$run_dir/launchd-stale-state.txt" 2>&1; then
		launchd_stale_job_found=1
		if ! probe_owns_stale_launch_agent_state "$run_dir/launchd-stale-state.txt"; then
			{
				echo "refusing to boot out unowned launchd job: $launch_agent_target"
				launch_agent_state_value path "$run_dir/launchd-stale-state.txt"
				launch_agent_state_value program "$run_dir/launchd-stale-state.txt"
			} >"$run_dir/launchd-bootstrap.log"
			return 1
		fi
		launchd_stale_job_owned=1
		stale_path=$(launch_agent_state_value path \
			"$run_dir/launchd-stale-state.txt")
		/bin/launchctl bootout "$launch_agent_domain" "$stale_path" \
			>"$run_dir/launchd-stale-bootout.log" 2>&1 || return 1
		launchd_stale_job_booted_out=1
		if /bin/launchctl print "$launch_agent_target" >/dev/null 2>&1; then
			echo "owned stale launchd job remained after bootout" \
				>>"$run_dir/launchd-stale-bootout.log"
			return 1
		fi
	fi
}

bootstrap_launch_agent() {
	local pid

	if /bin/launchctl print "$launch_agent_target" \
		>"$run_dir/launchd-unexpected-state.txt" 2>&1; then
		echo "launchd job appeared after stale-job cleanup" \
			>"$run_dir/launchd-bootstrap.log"
		return 1
	fi
	: >"$run_dir/native-bridge.log"
	/bin/launchctl bootstrap "$launch_agent_domain" "$launch_agent_plist" \
		>"$run_dir/launchd-bootstrap.log" 2>&1 || return 1
	launch_agent_loaded=1
	/bin/launchctl kickstart -k "$launch_agent_target" \
		>>"$run_dir/launchd-bootstrap.log" 2>&1 || return 1

	for _ in $(seq 1 200); do
		if ! /bin/launchctl print "$launch_agent_target" \
			>"$run_dir/launchd-state-after-start.txt" 2>&1; then
			sleep 0.05
			continue
		fi
		pid=$(launch_agent_state_value pid \
			"$run_dir/launchd-state-after-start.txt")
		if [[ $pid =~ ^[1-9][0-9]*$ ]] &&
			current_run_owns_launch_agent_state \
				"$run_dir/launchd-state-after-start.txt" &&
			launch_agent_state_is_single_run \
				"$run_dir/launchd-state-after-start.txt" &&
			rg -q 'native_source launchd service checked in' "$run_dir/native-bridge.log" 2>/dev/null &&
			validate_launch_agent_process_identity "$pid"; then
			launchd_start_identity_ok=1
			return 0
		fi
		sleep 0.05
	done
	/bin/launchctl print "$launch_agent_target" \
		>"$run_dir/launchd-state-after-start.txt" 2>&1 || true
	return 1
}

bootout_launch_agent() {
	local status

	if [[ $launch_agent_loaded -ne 1 ]]; then
		return 0
	fi
	if ! /bin/launchctl print "$launch_agent_target" \
		>"$run_dir/launchd-state-before-bootout.txt" 2>&1; then
		launch_agent_loaded=0
		launch_agent_bootout_ok=1
		printf 'absent=%s\n' "$launch_agent_target" \
			>"$run_dir/launchd-state-after-bootout.txt"
		return 0
	fi
	if ! current_run_owns_launch_agent_state \
		"$run_dir/launchd-state-before-bootout.txt"; then
		echo "refusing to boot out launchd job without current-run identity" \
			>>"$run_dir/launchd-state-before-bootout.txt"
		return 1
	fi
	if launch_agent_state_is_single_run \
		"$run_dir/launchd-state-before-bootout.txt"; then
		launchd_bootout_identity_ok=1
	else
		echo "booting out exact current-run plist after unexpected launchd restart" \
			>>"$run_dir/launchd-state-before-bootout.txt"
	fi
	/bin/launchctl bootout "$launch_agent_domain" "$launch_agent_plist" \
		>"$run_dir/launchd-bootout.log" 2>&1
	status=$?
	if [[ $status -ne 0 ]]; then
		if ! /bin/launchctl print "$launch_agent_target" >/dev/null 2>&1; then
			launch_agent_loaded=0
			launch_agent_bootout_ok=1
			printf 'absent=%s\n' "$launch_agent_target" \
				>"$run_dir/launchd-state-after-bootout.txt"
			return 0
		fi
		return "$status"
	fi
	for _ in $(seq 1 100); do
		if ! /bin/launchctl print "$launch_agent_target" >/dev/null 2>&1; then
			launch_agent_loaded=0
			launch_agent_bootout_ok=1
			printf 'absent=%s\n' "$launch_agent_target" \
				>"$run_dir/launchd-state-after-bootout.txt"
			return 0
		fi
		sleep 0.05
	done
	/bin/launchctl print "$launch_agent_target" \
		>"$run_dir/launchd-state-after-bootout.txt" 2>&1 || true
	return 1
}

archive_logs() {
	[[ -f /tmp/alvr_openvr_submit_shim.log ]] &&
		cp -p /tmp/alvr_openvr_submit_shim.log "$run_dir/openvr-submit-shim.log"
	[[ -f /tmp/fake_openvr_real.log ]] &&
		cp -p /tmp/fake_openvr_real.log "$run_dir/fake-openvr.log"
	for log in "$game_dir"/*_d3d11.log "$game_dir"/*_dxgi.log; do
		[[ -f $log ]] && cp -p "$log" "$run_dir/"
	done
	if [[ -f $avp_console_log ]]; then
		cp -p "$avp_console_log" "$run_dir/avp-client-console.raw.log"
		if normalize_avp_console_log; then
			cp -p "$avp_console_normalized_log" "$run_dir/avp-client-console.log"
		fi
	fi
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
		stop_pid "$launcher_pid"
		if ! bootout_launch_agent; then
			cleanup_failed=1
			stop_owned_launch_agent_process || cleanup_failed=1
		fi
		if /bin/launchctl print "$launch_agent_target" >/dev/null 2>&1; then
			cleanup_failed=1
		else
			rm -f "$launch_agent_plist" || cleanup_failed=1
		fi
		stop_owned_avp_client || cleanup_failed=1
		shutdown_bottle "$run_dir/bottle-shutdown-after.log" || cleanup_failed=1
		archive_logs
		mkdir -p "$alvr_state_root" || cleanup_failed=1
		for file in session.json session_old.json; do
			if [[ -f $alvr_runtime_root/$file ]]; then
				cp -p "$alvr_runtime_root/$file" "$alvr_state_root/$file" || cleanup_failed=1
			fi
		done
		write_state_manifest "$run_dir/alvr-state-after.txt" || cleanup_failed=1
		restore_wine_bridge_source || cleanup_failed=1
		if [[ -f $backup_dir/libMoltenVK.dylib ]]; then
			replace_file_atomically "$backup_dir/libMoltenVK.dylib" "$moltenvk" || cleanup_failed=1
		fi
		if [[ -f $backup_dir/openvr_api.dll ]]; then
			cp -f "$backup_dir/openvr_api.dll" "$engine_dir/openvr_api.dll" || cleanup_failed=1
		fi
		rm -f "$engine_dir/openvr_api.real.dll"
		rm -f "$game_dir/d3d11.dll" "$game_dir/dxgi.dll" "$game_dir/alvr_iosurface_bridge.dll"
		rm -f "$game_dir"/*_d3d11.log "$game_dir"/*_dxgi.log
		rm -f /tmp/alvr_openvr_submit_shim.log /tmp/fake_openvr_real.log /tmp/alvr_frame_buffer.shm
		rm -f "$probe_dir"/real_submit_iosurface_{ready,ready.tmp,done,done.tmp}.txt
		rm -f "$desktop_warmup_file"
		{
			printf 'moltenvk=%s\n' "$(hash_file "$moltenvk")"
			printf 'openvr=%s\n' "$(hash_file "$engine_dir/openvr_api.dll")"
			for path in \
				"$engine_dir/openvr_api.real.dll" \
				"$game_dir/d3d11.dll" \
				"$game_dir/dxgi.dll" \
				"$game_dir/alvr_iosurface_bridge.dll" \
				"$desktop_warmup_file" \
				"$launch_agent_plist" \
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
			if /bin/launchctl print "$launch_agent_target" >/dev/null 2>&1; then
				printf 'unexpected-present=%s\n' "$launch_agent_target"
				cleanup_failed=1
			else
				printf 'absent=%s\n' "$launch_agent_target"
			fi
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

mkdir -p \
	"$backup_dir" \
	"$build_dir" \
	"$bridge_root/x86_64-windows" \
	"$bridge_root/x86_64-unix" \
	"$run_dir/mvk-shaders" \
	"$probe_dir" \
	"$alvr_runtime_root" \
	"$alvr_state_root" \
	"$runtime_state_root" \
	"$(dirname "$native_bridge_bundle")"

for path in \
	"$alvr_checkout/Cargo.toml" \
	"$wine_source" \
	"$wine_build" \
	"$repo/tools/alvr_iosurface_bridge" \
	"$moltenvk" \
	"$patched_moltenvk" \
	"$dxvk_dir/d3d11/d3d11.dll" \
	"$dxvk_dir/dxgi/dxgi.dll" \
	"$fake_runtime_source" \
	"$cxstart" \
	"$wineserver" \
	"$game_executable" \
	"$engine_dir/openvr_api.dll"; do
	[[ -e $path ]] || {
		echo "missing=$path" >&2
		exit 1
	}
done

codesign --verify --strict --all-architectures "$patched_moltenvk"
moltenvk_signature_identifier=$(codesign -dv --verbose=4 "$patched_moltenvk" 2>&1 |
	sed -n 's/^Identifier=//p')
moltenvk_signature_cdhash=$(codesign -dv --verbose=4 "$patched_moltenvk" 2>&1 |
	sed -n 's/^CDHash=//p')
[[ -n $moltenvk_signature_identifier && -n $moltenvk_signature_cdhash ]] || {
	echo "patched MoltenVK signature metadata is incomplete" >&2
	exit 1
}

cmp -s \
	"$repo/tools/alvr_iosurface_bridge/iosurface_handoff_protocol.h" \
	"$alvr_checkout/alvr/macos_bridge/src/iosurface_handoff_protocol.h" || {
	echo "IOSurface protocol headers differ between producer and native bridge" >&2
	exit 1
}

if [[ -n $(game_process_pids) ]]; then
	echo "$probe_app_name is already running" >&2
	exit 1
fi
if pgrep -f '[a]lvr_macos_bridge' >/dev/null 2>&1; then
	echo "alvr_macos_bridge is already running" >&2
	exit 1
fi
[[ $(hash_file "$moltenvk") == "$stock_moltenvk_hash" ]] || {
	echo "CrossOver MoltenVK is not pristine" >&2
	exit 1
}
[[ $(hash_file "$engine_dir/openvr_api.dll") == "$stock_openvr_hash" ]] || {
	echo "$probe_app_name OpenVR DLL is not pristine" >&2
	exit 1
}
for path in \
	"$engine_dir/openvr_api.real.dll" \
	"$game_dir/d3d11.dll" \
	"$game_dir/dxgi.dll" \
	"$game_dir/alvr_iosurface_bridge.dll" \
	"$game_dir"/*_d3d11.log \
	"$game_dir"/*_dxgi.log; do
	[[ ! -e $path ]] || {
		echo "staging target exists: $path" >&2
		exit 1
	}
done

acquire_run_lock
remove_stale_launch_agent || {
	echo "owned stale launchd job could not be removed safely" >&2
	exit 1
}
if [[ $native_connect == true ]]; then
	resolve_avp_client || {
		echo "physical Vision Pro client preflight failed" >&2
		exit 1
	}
fi
shutdown_bottle "$run_dir/bottle-shutdown-before.log" || {
	echo "Steam bottle did not shut down cleanly" >&2
	exit 1
}
write_state_manifest "$run_dir/alvr-state-before.txt"

if [[ -f $alvr_state_root/session.json ]]; then
	rsync -a "$alvr_state_root/" "$alvr_runtime_root/"
elif [[ -d $alvr_bridge_root ]]; then
	rsync -a "$alvr_bridge_root/" "$alvr_runtime_root/"
fi

cargo build \
	--manifest-path "$alvr_checkout/Cargo.toml" \
	-p alvr_macos_bridge \
	--release \
	>"$run_dir/alvr-build.log" 2>&1
for legacy_native_bridge_bundle in "${legacy_native_bridge_bundles[@]}"; do
	if [[ -d $legacy_native_bridge_bundle ]]; then
		"$launch_services_register" -u "$legacy_native_bridge_bundle" \
			>>"$run_dir/native-bridge-legacy-unregister.log" 2>&1 || true
		rm -rf "$legacy_native_bridge_bundle"
	fi
done
rm -rf "$native_bridge_install_staging"
mkdir -p "$native_bridge_install_staging/Contents/MacOS"
cp -p "$native_bridge" "$native_bridge_install_program"
/usr/bin/plutil -create xml1 "$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleIdentifier -string "$native_bridge_bundle_id" \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleName -string ALVRMacOSBridge \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleDisplayName -string 'ALVR macOS Bridge' \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleExecutable -string alvr_macos_bridge \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert CFBundlePackageType -string APPL \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleVersion -string 1 \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleShortVersionString -string 1.0 \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert LSBackgroundOnly -bool true \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert NSLocalNetworkUsageDescription -string \
	'Connect to the ALVR client on the local network.' \
	"$native_bridge_install_staging/Contents/Info.plist"
/usr/bin/plutil -insert NSBonjourServices -json '["_alvr._tcp"]' \
	"$native_bridge_install_staging/Contents/Info.plist"
codesign --force --deep \
	--sign "$native_bridge_codesign_identity" \
	--identifier "$native_bridge_bundle_id" \
	--timestamp=none \
	"$native_bridge_install_staging" \
	>"$run_dir/native-bridge-codesign.log" 2>&1
codesign --verify --strict --deep "$native_bridge_install_staging"
native_bridge_signature_identifier=$(codesign -dv --verbose=4 "$native_bridge_install_program" 2>&1 |
	sed -n 's/^Identifier=//p')
native_bridge_signature_team=$(codesign -dv --verbose=4 "$native_bridge_install_program" 2>&1 |
	sed -n 's/^TeamIdentifier=//p')
native_bridge_signature_cdhash=$(codesign -dv --verbose=4 "$native_bridge_install_program" 2>&1 |
	sed -n 's/^CDHash=//p')
[[ $native_bridge_signature_identifier == "$native_bridge_bundle_id" &&
	$native_bridge_signature_team == MM5YXC7T6E &&
	-n $native_bridge_signature_cdhash ]] || {
	echo "native bridge stable code-signing identity is incomplete" >&2
	exit 1
}
rm -rf "$native_bridge_bundle"
mv "$native_bridge_install_staging" "$native_bridge_bundle"
codesign --verify --strict --deep "$native_bridge_bundle"
"$launch_services_register" -f "$native_bridge_bundle" \
	>"$run_dir/native-bridge-register.log" 2>&1
"$launch_services_register" -dump 2>/dev/null | awk \
	-v identifier="$native_bridge_bundle_id" '
		/^--------------------------------------------------------------------------------$/ {
			if (index(record, "identifier:                 " identifier) > 0) {
				printf "%s", record
			}
			record = ""
			next
		}
		{ record = record $0 ORS }
		END {
			if (index(record, "identifier:                 " identifier) > 0) {
				printf "%s", record
			}
		}
	' >"$run_dir/native-bridge-launch-services.txt"
native_bridge_registration_count=$(rg -c \
	"^identifier:[[:space:]]+$native_bridge_bundle_id$" \
	"$run_dir/native-bridge-launch-services.txt" || true)
if [[ ${native_bridge_registration_count:-0} -ne 1 ]] ||
	! rg -Fq "path:                       $native_bridge_bundle" \
		"$run_dir/native-bridge-launch-services.txt" ||
	! rg -Fq "teamID:                     $native_bridge_signature_team" \
		"$run_dir/native-bridge-launch-services.txt" ||
	! rg -Fq "$native_bridge_signature_cdhash" \
		"$run_dir/native-bridge-launch-services.txt"; then
	echo "native bridge Launch Services registration is ambiguous" >&2
	exit 1
fi
mkdir -p "$native_bridge_evidence_dir"
cp -p "$native_bridge_program" "$native_bridge_evidence"
cp -p "$native_bridge_bundle/Contents/Info.plist" "$native_bridge_info_evidence"
/usr/bin/clang \
	-O2 -Wall -Wextra -Werror -std=c11 \
	-I"$repo/tools" \
	"$repo/tools/mach_service_oversize_probe.c" \
	-o "$oversize_probe" \
	>"$run_dir/oversize-probe-build.log" 2>&1
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
	"$fake_runtime_source" \
	-I"$alvr_checkout/openvr/headers" \
	-I"$alvr_checkout/alvr/server_openvr/cpp" \
	-o "$fake_runtime" \
	>"$run_dir/fake-runtime-build.log" 2>&1

if [[ -d $wine_bridge_source ]]; then
	wine_bridge_source_existed=1
	mkdir -p "$wine_bridge_backup"
	rsync -a "$wine_bridge_source/" "$wine_bridge_backup/"
fi
mutations_started=1
wine_bridge_source_mutated=1
rsync -a --delete \
	"$repo/tools/alvr_iosurface_bridge/" \
	"$wine_bridge_source/"
rm -f \
	"$bridge_build/unixlib.o" \
	"$bridge_build/alvr_iosurface_bridge.so" \
	"$bridge_build/x86_64-windows/bridge.o" \
	"$bridge_build/x86_64-windows/alvr_iosurface_bridge.dll"
arch -x86_64 make -C "$wine_build" -j8 \
	dlls/alvr_iosurface_bridge/x86_64-windows/alvr_iosurface_bridge.dll \
	dlls/alvr_iosurface_bridge/alvr_iosurface_bridge.so \
	>"$run_dir/bridge-build.log" 2>&1
cp -f "$bridge_build/x86_64-windows/alvr_iosurface_bridge.dll" \
	"$bridge_root/x86_64-windows/"
cp -f "$bridge_build/alvr_iosurface_bridge.so" \
	"$bridge_root/x86_64-unix/"
restore_wine_bridge_source

for file in \
	"$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll" \
	"$bridge_root/x86_64-unix/alvr_iosurface_bridge.so" \
	"$shim" \
	"$native_bridge_evidence" \
	"$native_bridge_program" \
	"$oversize_probe"; do
	[[ -f $file ]] || {
		echo "missing=$file" >&2
		exit 1
	}
done

rm -f /tmp/alvr_frame_buffer.shm /tmp/alvr_openvr_submit_shim.log /tmp/fake_openvr_real.log
cp -p "$moltenvk" "$backup_dir/libMoltenVK.dylib"
cp -p "$engine_dir/openvr_api.dll" "$backup_dir/openvr_api.dll"

replace_file_atomically "$patched_moltenvk" "$moltenvk"
codesign --verify --strict --all-architectures "$moltenvk"
if ! /usr/bin/time -p arch -x86_64 /usr/bin/python3 -c \
	'import ctypes, sys; ctypes.CDLL(sys.argv[1])' \
	"$moltenvk" >"$run_dir/moltenvk-prewarm.log" 2>&1; then
	cat "$run_dir/moltenvk-prewarm.log" >&2
	echo "MoltenVK Rosetta prewarm failed" >&2
	exit 1
fi
moltenvk_prewarm_seconds=$(awk '$1 == "real" { print $2 }' "$run_dir/moltenvk-prewarm.log")
cp -f "$fake_runtime" "$engine_dir/openvr_api.real.dll"
cp -f "$shim" "$engine_dir/openvr_api.dll"
cp -f "$dxvk_dir/d3d11/d3d11.dll" "$game_dir/d3d11.dll"
cp -f "$dxvk_dir/dxgi/dxgi.dll" "$game_dir/dxgi.dll"
cp -f "$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll" "$game_dir/alvr_iosurface_bridge.dll"
start_desktop_warmup
if [[ $native_connect == true ]]; then
	launch_avp_client || {
		echo "physical Vision Pro client launch failed" >&2
		exit 1
	}
	seed_avp_client_session || {
		echo "artifact-local ALVR client seeding failed" >&2
		exit 1
	}
fi

cx_env="CX_GRAPHICS_BACKEND=dxvk WINEDLLPATH=$bridge_root WINEDLLOVERRIDES=d3d11,dxgi=n DXVK_LOG_LEVEL=debug DXVK_STATE_CACHE=0 MVK_CONFIG_LOG_LEVEL=3 MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=0 MVK_CONFIG_SHADER_DUMP_DIR=$run_dir/mvk-shaders ALVR_IOSURFACE_POOL_SERVICE=$service_name ALVR_IOSURFACE_POOL_NONCE=$nonce ALVR_IOSURFACE_SOURCE_WIDTH=$source_width ALVR_IOSURFACE_SOURCE_HEIGHT=$source_height ALVR_MOLTENVK_PATH=$moltenvk WINEDEBUG=-all,+loaddll"
if [[ -n $probe_extra_env ]]; then
	cx_env+=" $probe_extra_env"
fi
if [[ -n $fake_wait_get_poses_sleep_ms ]]; then
	cx_env+=" ALVR_FAKE_WAIT_GET_POSES_SLEEP_MS=$fake_wait_get_poses_sleep_ms"
fi
write_launch_agent_plist
{
	printf 'run_dir=%s\n' "$run_dir"
	printf 'probe_app_name=%s\n' "$probe_app_name"
	printf 'probe_process_pattern=%s\n' "$probe_process_pattern"
	printf 'game_dir=%s\n' "$game_dir"
	printf 'engine_dir=%s\n' "$engine_dir"
	printf 'game_executable=%s\n' "$game_executable"
	printf 'game_workdir=%s\n' "$game_workdir"
	printf 'probe_arguments='
	if [[ ${#probe_arguments[@]} -eq 0 ]]; then
		printf 'none'
	else
		printf ' %q' "${probe_arguments[@]}"
	fi
	printf '\n'
	printf 'probe_launcher_source=%s\n' "$probe_launcher_source"
	printf 'service_type=launchd-mach\n'
	printf 'service_name=%s\n' "$service_name"
	printf 'launch_agent_label=%s\n' "$launch_agent_label"
	printf 'launch_agent_domain=%s\n' "$launch_agent_domain"
	printf 'launch_agent_target=%s\n' "$launch_agent_target"
	printf 'launch_agent_plist=%s\n' "$launch_agent_plist"
	printf 'launch_agent_evidence=%s\n' "$launch_agent_evidence"
	printf 'nonce=%s\n' "$nonce"
	printf 'native_bridge_codesign_identity=%s\n' "$native_bridge_codesign_identity"
	printf 'native_bridge_bundle=%s\n' "$native_bridge_bundle"
	printf 'native_bridge_program=%s\n' "$native_bridge_program"
	printf 'native_bridge_signature_identifier=%s\n' "$native_bridge_signature_identifier"
	printf 'native_bridge_signature_team=%s\n' "$native_bridge_signature_team"
	printf 'native_bridge_signature_cdhash=%s\n' "$native_bridge_signature_cdhash"
	printf 'moltenvk_signature_identifier=%s\n' "$moltenvk_signature_identifier"
	printf 'moltenvk_signature_cdhash=%s\n' "$moltenvk_signature_cdhash"
	printf 'moltenvk_prewarm_seconds=%s\n' "$moltenvk_prewarm_seconds"
	printf 'desktop_warmup_pid=%s\n' "$desktop_warmup_pid"
	printf 'desktop_explorer_pid=%s\n' "$desktop_explorer_pid"
	printf 'native_frames=%s\n' "$native_frames"
	printf 'native_connect=%s\n' "$native_connect"
	printf 'pressure_pause_ms=%s\n' "$pressure_pause_ms"
	printf 'fake_pacing_mode=%s\n' "$fake_pacing_mode"
	printf 'fake_wait_get_poses_sleep_ms=%s\n' "$fake_wait_get_poses_sleep_ms"
	printf 'expected_source_transition=%s\n' "$expected_source_transition"
	printf 'producer_min_fps=%s\n' "$producer_min_fps"
	printf 'producer_max_fps=%s\n' "$producer_max_fps"
	printf 'source_size=%sx%s\n' "$source_width" "$source_height"
	printf 'producer_pool_size=%sx%s\n' "$source_width" "$source_height"
	printf 'output_size=%sx%s\n' "$output_width" "$output_height"
	printf 'horizontal_mapping=full-eye-scale\n'
	printf 'avp_device_selector=%s\n' "$avp_device_selector"
	printf 'avp_device_id=%s\n' "$avp_device_id"
	printf 'avp_device_udid=%s\n' "$avp_device_udid"
	printf 'avp_device_name=%s\n' "$avp_device_name"
	printf 'avp_bundle_id=%s\n' "$avp_bundle_id"
	printf 'avp_app_url=%s\n' "$avp_app_url"
	printf 'avp_app_executable=%s\n' "$avp_app_executable"
	printf 'avp_app_version=%s\n' "$avp_app_version"
	printf 'avp_app_build=%s\n' "$avp_app_build"
	printf 'avp_expected_protocol=%s\n' "$avp_expected_protocol"
	printf 'avp_client_id=%s\n' "$avp_client_id"
	printf 'avp_client_ip=%s\n' "$avp_client_ip"
	printf 'avp_client_protocol=%s\n' "$avp_client_protocol"
	printf 'avp_console_pid=%s\n' "$avp_console_pid"
	printf 'avp_remote_pid=%s\n' "$avp_remote_pid"
	printf 'avp_client_ready_latency_ms=%s\n' "$avp_client_ready_latency_ms"
	printf 'avp_session_seeded=%s\n' "$avp_session_seeded"
	printf 'avp_console_log=%s\n' "$avp_console_log"
	printf 'alvr_runtime_root=%s\n' "$alvr_runtime_root"
	printf 'alvr_state_root=%s\n' "$alvr_state_root"
	printf 'cx_env=%s\n' "$cx_env"
	shasum -a 256 \
		"$shim" \
		"$native_bridge_evidence" \
		"$native_bridge_info_evidence" \
		"$oversize_probe" \
		"$bridge_root/x86_64-windows/alvr_iosurface_bridge.dll" \
		"$bridge_root/x86_64-unix/alvr_iosurface_bridge.so" \
		"$launch_agent_evidence" \
		"$patched_moltenvk" \
		"$dxvk_dir/d3d11/d3d11.dll" \
		"$dxvk_dir/dxgi/dxgi.dll" \
		"$fake_runtime"
	printf 'macos_game_patches_head=%s\n' "$(git -C "$repo" rev-parse HEAD)"
	printf 'alvr_head=%s\n' "$(git -C "$alvr_checkout" rev-parse HEAD)"
	git -C "$repo" status --short
	git -C "$alvr_checkout" status --short
} >"$run_dir/run.info"

: >"$run_dir/source-state.txt"
while IFS= read -r relative; do
	snapshot_source_file "$repo" macos-game-patches "$relative" \
		>>"$run_dir/source-state.txt"
done <<'EOF'
tools/openvr_submit_shim.cpp
tools/fake_openvr_real.cpp
tools/dxvk_iosurface_submit_proof.cpp
tools/dxvk_iosurface_submit_proof.h
tools/shared/alvr_shm_protocol.h
tools/alvr_iosurface_bridge/Makefile.in
tools/alvr_iosurface_bridge/alvr_iosurface_bridge.spec
tools/alvr_iosurface_bridge/bridge.c
tools/alvr_iosurface_bridge/unixlib.c
tools/alvr_iosurface_bridge/unixlib.h
tools/alvr_iosurface_bridge/iosurface_handoff_protocol.h
tools/mach_service_oversize_probe.c
tools/run_real_native_iosurface_probe.sh
EOF
if [[ -n $probe_launcher_source ]]; then
	snapshot_source_file "$repo" macos-game-patches "$probe_launcher_source" \
		>>"$run_dir/source-state.txt"
fi
while IFS= read -r relative; do
	snapshot_source_file "$alvr_checkout" alvr "$relative" \
		>>"$run_dir/source-state.txt"
done < <(
	cd "$alvr_checkout"
	{
		printf '%s\n' Cargo.toml Cargo.lock
		find alvr/macos_bridge -type f \( \
			-name '*.rs' -o -name '*.c' -o -name '*.h' -o \
			-name '*.mm' -o -name '*.metal' -o -name 'Cargo.toml' \)
		printf '%s\n' \
			alvr/server_core/Cargo.toml \
			alvr/server_core/src/connection.rs \
			alvr/server_core/src/lib.rs
	} | LC_ALL=C sort -u
)
git -C "$repo" diff --binary HEAD >"$run_dir/macos-game-patches.patch"
git -C "$alvr_checkout" diff --binary HEAD >"$run_dir/alvr.patch"

if [[ $native_connect == true ]] && ! validate_avp_client_before_bootstrap; then
	echo "the physical Vision Pro client changed before host bootstrap" >&2
	exit 1
fi
if ! bootstrap_launch_agent; then
	cat "$run_dir/launchd-bootstrap.log" >&2 2>/dev/null || true
	cat "$run_dir/native-bridge.log" >&2 2>/dev/null || true
	echo "native bridge launchd check-in failed" >&2
	exit 1
fi

"$oversize_probe" "$service_name" import >"$run_dir/oversize-probe.log" 2>&1
oversize_import_rejected=0
for _ in $(seq 1 100); do
	if rg -q 'native_source rejected import request slot=0 reason=message-too-large' \
		"$run_dir/native-bridge.log" 2>/dev/null; then
		oversize_import_rejected=1
		break
	fi
	sleep 0.05
done
if [[ $oversize_import_rejected -ne 1 ]]; then
	echo "native bridge did not drain the oversized import request" >&2
	exit 1
fi

launch_command=(
	"$cxstart"
	--bottle "$bottle_name"
	--no-update
	--no-gui
	--wait
	--workdir "$game_workdir"
	--env "$cx_env"
	"$game_executable"
)
if [[ ${#probe_arguments[@]} -gt 0 ]]; then
	launch_command+=("${probe_arguments[@]}")
fi
"${launch_command[@]}" >"$run_dir/game-launch.log" 2>&1 &
launcher_pid=$!

startup_self_tests_seen=0
for _ in $(seq 1 6000); do
	if [[ $native_connect == true ]] && ! capture_avp_connection_state; then
		break
	fi
	if rg -q 'native_source startup self-tests passed slots=3' \
		"$run_dir/native-bridge.log" 2>/dev/null; then
		startup_self_tests_seen=1
		break
	fi
	if rg -q '^Error:' "$run_dir/native-bridge.log" 2>/dev/null ||
		! kill -0 "$launcher_pid" 2>/dev/null ||
		! /bin/launchctl print "$launch_agent_target" \
			>"$run_dir/launchd-state-during-startup.txt" 2>&1 ||
		! current_run_owns_launch_agent_state \
			"$run_dir/launchd-state-during-startup.txt" ||
		! launch_agent_state_is_single_run \
			"$run_dir/launchd-state-during-startup.txt"; then
		break
	fi
	sleep 0.1
done
if [[ $startup_self_tests_seen -ne 1 ]]; then
	echo "native bridge did not complete startup self-tests" >&2
	exit 1
fi
if [[ $native_connect == true ]] && ! wait_for_avp_connection; then
	echo "the physical Vision Pro client did not connect within the bounded startup gate" >&2
	exit 1
fi
"$oversize_probe" "$service_name" frame >>"$run_dir/oversize-probe.log" 2>&1
oversize_frame_rejected=0
for _ in $(seq 1 100); do
	if rg -q 'native_source rejected frame-ready reason=message-too-large' \
		"$run_dir/native-bridge.log" 2>/dev/null; then
		oversize_frame_rejected=1
		break
	fi
	sleep 0.05
done
if [[ $oversize_frame_rejected -ne 1 ]]; then
	echo "native bridge did not drain the oversized frame-ready message" >&2
	exit 1
fi

if [[ $pressure_pause_ms -gt 0 ]]; then
	all_slots_released=0
	for _ in $(seq 1 200); do
		if [[ $(production_released_slot_count \
			/tmp/alvr_openvr_submit_shim.log) -ge 3 ]]; then
			all_slots_released=1
			break
		fi
		if ! kill -0 "$launcher_pid" 2>/dev/null; then
			break
		fi
		sleep 0.05
	done
	if [[ $all_slots_released -ne 1 ]]; then
		echo "pressure probe did not observe all three released slots" >&2
		exit 1
	fi
	if ! signal_owned_launch_agent SIGSTOP \
		"$run_dir/launchd-state-before-pressure-stop.txt"; then
		echo "pressure probe could not stop the owned launchd job" >&2
		exit 1
	fi
	pressure_seconds=$(awk -v milliseconds="$pressure_pause_ms" \
		'BEGIN { printf "%.3f", milliseconds / 1000 }')
	sleep "$pressure_seconds"
	if ! signal_owned_launch_agent SIGCONT \
		"$run_dir/launchd-state-before-pressure-cont.txt"; then
		echo "pressure probe could not resume the owned launchd job" >&2
		exit 1
	fi
	pressure_applied=1
fi

bridge_finished=0
bridge_status=124
bridge_summary_seen=0
native_bridge_exited=0
native_bridge_exit_status=124
bridge_wait_seconds=$(((native_frames + 19) / 20 + 30))
if [[ $bridge_wait_seconds -lt 120 ]]; then
	bridge_wait_seconds=120
fi
for _ in $(seq 1 $((bridge_wait_seconds * 10))); do
	if rg -q '^native_source summary ' "$run_dir/native-bridge.log" 2>/dev/null; then
		bridge_summary_seen=1
	fi
	if ! /bin/launchctl print "$launch_agent_target" \
		>"$run_dir/launchd-state-after-exit.txt" 2>&1; then
		break
	fi
	if ! current_run_owns_launch_agent_state \
		"$run_dir/launchd-state-after-exit.txt" ||
		! launch_agent_state_is_single_run \
			"$run_dir/launchd-state-after-exit.txt"; then
		echo "launchd identity changed while waiting for native exit" \
			>>"$run_dir/launchd-state-after-exit.txt"
		break
	fi
	current_launch_pid=$(launch_agent_state_value pid \
		"$run_dir/launchd-state-after-exit.txt")
	launchd_state=$(launch_agent_state_value state \
		"$run_dir/launchd-state-after-exit.txt")
	if [[ -z $current_launch_pid && $launchd_state == "not running" ]]; then
		launchd_exit_identity_ok=1
		native_bridge_exited=1
		launchd_exit_status=$(launch_agent_state_value "last exit code" \
			"$run_dir/launchd-state-after-exit.txt")
		if [[ $launchd_exit_status =~ ^[0-9]+$ ]]; then
			native_bridge_exit_status=$launchd_exit_status
			bridge_status=$launchd_exit_status
		fi
		if [[ $bridge_summary_seen -eq 1 && $native_bridge_exit_status -eq 0 ]]; then
			bridge_finished=1
		fi
		break
	fi
	sleep 0.1
done
if [[ $native_bridge_exited -ne 1 ]]; then
	/bin/launchctl print "$launch_agent_target" \
		>"$run_dir/launchd-state-after-wait.txt" 2>&1 || true
fi

if [[ $native_connect == true ]]; then
	if validate_avp_session_identity; then
		avp_session_identity_ok=1
	else
		avp_post_host_status=1
	fi
	if [[ -f $alvr_runtime_root/session.json ]]; then
		cp -p "$alvr_runtime_root/session.json" "$run_dir/alvr-session-connected-final.json"
	fi
	if [[ $native_bridge_exited -eq 1 ]]; then
		observe_avp_post_host || avp_post_host_status=1
	else
		avp_post_host_status=1
	fi
fi

restore_status=0
if ! restore; then
	restore_status=1
fi

count_matches() {
	local count
	count=$(rg -c "$1" "$2" 2>/dev/null || true)
	printf '%s\n' "${count:-0}"
}

self_tests=$(count_matches 'iosurface pool self-test .*result=pass' "$run_dir/openvr-submit-shim.log")
producer_resize_self_tests=$(count_matches \
	'iosurface pool self-test .*transfer=stereo-(linear-clamped|nearest).*result=pass' \
	"$run_dir/openvr-submit-shim.log")
producer_linear_resize_self_tests=$(count_matches \
	'iosurface pool self-test .*transfer=stereo-linear-clamped.*result=pass' \
	"$run_dir/openvr-submit-shim.log")
producer_closed_frame_id=$(awk '
	/iosurface pool release/ && /result=closed/ {
		for (field = 1; field <= NF; field++) {
			if ($field ~ /^frame_id=/) {
				split($field, value, "=")
				print value[2] + 0
				exit
			}
		}
	}
' "$run_dir/openvr-submit-shim.log" 2>/dev/null)
producer_closed_frame_id=${producer_closed_frame_id:-0}
read -r submitted producer_post_close_submissions \
	producer_release_failures_before_close producer_post_close_release_failures < <(
		awk -v closed_frame_id="$producer_closed_frame_id" '
			/iosurface pool submitted/ {
				frame_id = 0
				for (field = 1; field <= NF; field++) {
					if ($field ~ /^frame_id=/) {
						split($field, value, "=")
						frame_id = value[2] + 0
					}
				}
				if (closed_frame_id && frame_id > closed_frame_id) post_submissions++
				else submissions++
			}
			/iosurface pool release/ && /result=fail/ {
				frame_id = 0
				for (field = 1; field <= NF; field++) {
					if ($field ~ /^frame_id=/) {
						split($field, value, "=")
						frame_id = value[2] + 0
					}
				}
				if (closed_frame_id && frame_id > closed_frame_id) post_failures++
				else failures++
			}
			END {
				printf "%d %d %d %d\n", submissions + 0, post_submissions + 0, \
					failures + 0, post_failures + 0
			}
		' "$run_dir/openvr-submit-shim.log" 2>/dev/null
	)
producer_total_submissions=$((submitted + producer_post_close_submissions))
producer_source_transitions=$(count_matches \
	'iosurface pool source geometry transition' "$run_dir/openvr-submit-shim.log")
producer_resized_submissions=$(count_matches \
	'iosurface pool submitted .*transfer=stereo-(linear-clamped|nearest)' \
	"$run_dir/openvr-submit-shim.log")
producer_separate_eye_submissions=$(count_matches \
	'iosurface pool submitted .*transfer=separate-eye-nearest' \
	"$run_dir/openvr-submit-shim.log")
producer_expected_resize_submissions=0
if [[ -n $expected_source_transition ]]; then
	producer_expected_resize_submissions=$(count_matches \
		"iosurface pool submitted .*source=$expected_source_transition output=${source_width}x${source_height} .*transfer=stereo-(linear-clamped|nearest)" \
		"$run_dir/openvr-submit-shim.log")
fi
consumer_validated=$(count_matches 'iosurface pool submitted .*validation=consumer-sample' "$run_dir/openvr-submit-shim.log")
released=$(count_matches 'iosurface pool release .*result=(pass|closed|dropped)' "$run_dir/openvr-submit-shim.log")
closed=$(count_matches 'iosurface pool release .*result=closed' "$run_dir/openvr-submit-shim.log")
release_drops=$(count_matches 'iosurface pool release .*result=dropped' "$run_dir/openvr-submit-shim.log")
read -r producer_startup_drops producer_steady_state_drops < <(
	awk -v startup_slot_count=3 '
		/iosurface pool release/ && /result=(pass|closed|dropped)/ {
			slot = -1
			for (field = 1; field <= NF; field++) {
				if ($field ~ /^slot=/) {
					split($field, value, "=")
					slot = value[2] + 0
				}
			}
			if (slot >= 0 && !released_slot[slot]) {
				released_slot[slot] = 1
				released_slot_count++
			}
			if (released_slot_count >= startup_slot_count) warmed = 1
		}
		/iosurface pool release/ && /result=closed/ { closed = 1 }
		!closed && /iosurface pool drop reason=exhausted/ {
			if (warmed) steady++
			else startup++
		}
		END { printf "%d %d\n", startup + 0, steady + 0 }
	' "$run_dir/openvr-submit-shim.log" 2>/dev/null
)
producer_drops=$((producer_startup_drops + producer_steady_state_drops))
producer_backpressure_waits=$(count_matches \
	'iosurface pool backpressure wait_us=' "$run_dir/openvr-submit-shim.log")
producer_backpressure_max_us=$(awk '
	/iosurface pool backpressure wait_us=/ {
		for (field = 1; field <= NF; field++) {
			if ($field ~ /^wait_us=/) {
				split($field, value, "=")
				if (value[2] + 0 > maximum) maximum = value[2] + 0
			}
		}
	}
	END { print maximum + 0 }
' "$run_dir/openvr-submit-shim.log" 2>/dev/null)
producer_backpressure_max_us=${producer_backpressure_max_us:-0}
read -r producer_pressure_recovery_releases producer_pressure_recovery_slots < <(
	awk '
		/iosurface pool drop reason=exhausted/ {
			after_last_drop = 1
			releases = 0
			delete released_slot
		}
		after_last_drop && /iosurface pool release/ && /result=(pass|closed|dropped)/ {
			slot = -1
			for (field = 1; field <= NF; field++) {
				if ($field ~ /^slot=/) {
					split($field, value, "=")
					slot = value[2] + 0
				}
			}
			releases++
			if (slot >= 0) released_slot[slot] = 1
		}
		END {
			for (slot in released_slot) released_slot_count++
			printf "%d %d\n", releases + 0, released_slot_count + 0
		}
	' "$run_dir/openvr-submit-shim.log" 2>/dev/null
)
nonblack_releases=$(awk '
	/iosurface pool release/ && /result=(pass|closed|dropped)/ {
		for (field = 1; field <= NF; field++) {
			if ($field ~ /^actual_bgra=/) {
				sub(/^actual_bgra=/, "", $field)
				split($field, pixel, ",")
				if (pixel[1] + pixel[2] + pixel[3] > 0) count++
			}
		}
	}
	END { print count + 0 }
' "$run_dir/openvr-submit-shim.log" 2>/dev/null || printf '0\n')
resize_expectation_pass=1
if [[ -n $expected_source_transition ]] &&
	! [[ $producer_source_transitions -gt 0 &&
		$producer_expected_resize_submissions -gt 0 &&
		$consumer_validated -gt 0 && $nonblack_releases -gt 0 ]]; then
	resize_expectation_pass=0
fi
slots_used=$(rg -o 'iosurface pool submitted .*slot=[0-9]+' "$run_dir/openvr-submit-shim.log" 2>/dev/null |
	sed -E 's/.*slot=//' | sort -u | wc -l | tr -d ' ' || true)
slots_used=${slots_used:-0}
producer_pose_paired=$(awk -v closed_frame_id="$producer_closed_frame_id" '
	/iosurface pool submitted/ &&
		/pose_generation=[1-9][0-9]* pose_timestamp_ns=[1-9][0-9]*/ {
		for (field = 1; field <= NF; field++) {
			if ($field ~ /^frame_id=/) {
				split($field, value, "=")
				if (!closed_frame_id || value[2] + 0 <= closed_frame_id) count++
			}
		}
	}
	END { print count + 0 }
' "$run_dir/openvr-submit-shim.log" 2>/dev/null || printf '0\n')
producer_pose_fallback=$(awk -v closed_frame_id="$producer_closed_frame_id" '
	/iosurface pool submitted/ &&
		/pose_source=fallback pose_generation=0 pose_timestamp_ns=[1-9][0-9]*/ {
		for (field = 1; field <= NF; field++) {
			if ($field ~ /^frame_id=/) {
				split($field, value, "=")
				if (!closed_frame_id || value[2] + 0 <= closed_frame_id) count++
			}
		}
	}
	END { print count + 0 }
' "$run_dir/openvr-submit-shim.log" 2>/dev/null || printf '0\n')
read -r producer_timed_submissions producer_duration_ms producer_effective_fps \
	producer_tail_window producer_tail_effective_fps < <(
		awk -v closed_frame_id="$producer_closed_frame_id" '
			/iosurface pool submitted/ {
				for (field = 1; field <= NF; field++) {
					if ($field ~ /^frame_id=/) {
						split($field, value, "=")
						if (!closed_frame_id || value[2] + 0 <= closed_frame_id) print
					}
				}
			}
		' "$run_dir/openvr-submit-shim.log" 2>/dev/null |
			awk '
			function milliseconds(time, parts) {
				split(time, parts, ":")
				return ((parts[1] * 60 + parts[2]) * 60 + parts[3]) * 1000
			}
			{
				current = milliseconds($2)
				count++
				if (count == 1) first = current
				last = current
				timestamps[count] = current
			}
			END {
				duration = last - first
				if (duration < 0) duration += 24 * 60 * 60 * 1000
				fps = count > 1 && duration > 0 ? (count - 1) * 1000 / duration : 0
				tail_window_target = int(count / 3)
				if (tail_window_target < 60) tail_window_target = count
				if (tail_window_target > 300) tail_window_target = 300
				tail_start = count - tail_window_target + 1
				tail_duration = last - timestamps[tail_start]
				if (tail_duration < 0) tail_duration += 24 * 60 * 60 * 1000
				tail_window = count - tail_start + 1
				tail_fps = tail_window > 1 && tail_duration > 0 \
					? (tail_window - 1) * 1000 / tail_duration : 0
				printf "%d %.0f %.3f %d %.3f\n", \
					count + 0, duration + 0, fps, tail_window + 0, tail_fps
			}
		'
	)
producer_timed_submissions=${producer_timed_submissions:-0}
producer_duration_ms=${producer_duration_ms:-0}
producer_effective_fps=${producer_effective_fps:-0}
producer_tail_window=${producer_tail_window:-0}
producer_tail_effective_fps=${producer_tail_effective_fps:-0}
summary_line=$(rg '^native_source summary ' "$run_dir/native-bridge.log" 2>/dev/null | tail -n 1 || true)
native_report_kind=summary
if [[ -z $summary_line ]]; then
	summary_line=$(rg '^native_source cadence ' "$run_dir/native-bridge.log" 2>/dev/null | tail -n 1 || true)
	native_report_kind=cadence
fi
[[ -n $summary_line ]] || native_report_kind=none
summary_value() {
	printf '%s\n' "$summary_line" | tr ' ' '\n' | awk -F= -v key="$1" '$1 == key {print $2}'
}
native_self_tests=$(summary_value self_tests)
native_received=$(summary_value received)
native_submitted=$(summary_value submitted)
native_encoded=$(summary_value encoded)
native_transported=$(summary_value alvr_sent)
native_encoded_bytes=$(summary_value encoded_bytes)
native_transported_bytes=$(summary_value transported_bytes)
native_encoded_mbps=$(summary_value encoded_mbps)
native_keyframes=$(summary_value keyframes)
native_keyframe_bytes=$(summary_value keyframe_bytes)
native_max_frame_bytes=$(summary_value max_frame_bytes)
native_video_span_ms=$(summary_value video_span_ms)
native_dropped=$(summary_value dropped)
native_not_ready_drops=$(summary_value not_ready_drops)
native_pool_exhausted_drops=$(summary_value pool_exhausted_drops)
native_pose_paired=$(summary_value pose_paired)
native_pose_fallback=$(summary_value pose_fallback)
native_pose_bootstrap=$(summary_value pose_bootstrap)
native_pose_generation_gaps=$(summary_value pose_generation_gaps)
native_pose_timestamp_reuses=$(summary_value pose_timestamp_reuses)
native_last_pose_generation=$(summary_value last_pose_generation)
native_wall_ms=$(summary_value wall_ms)
native_conversion_average_us=$(summary_value conversion_avg_us)
native_conversion_max_us=$(summary_value conversion_max_us)
native_conversion_gpu_average_us=$(summary_value conversion_gpu_avg_us)
native_conversion_gpu_max_us=$(summary_value conversion_gpu_max_us)
native_connected=$(summary_value alvr_connected)
native_self_tests=${native_self_tests:-0}
native_received=${native_received:-0}
native_submitted=${native_submitted:-0}
native_encoded=${native_encoded:-0}
native_transported=${native_transported:-0}
native_encoded_bytes=${native_encoded_bytes:-0}
native_transported_bytes=${native_transported_bytes:-0}
native_encoded_mbps=${native_encoded_mbps:-0}
native_keyframes=${native_keyframes:-0}
native_keyframe_bytes=${native_keyframe_bytes:-0}
native_max_frame_bytes=${native_max_frame_bytes:-0}
native_video_span_ms=${native_video_span_ms:-0}
native_dropped=${native_dropped:-0}
native_not_ready_drops=${native_not_ready_drops:-0}
native_pool_exhausted_drops=${native_pool_exhausted_drops:-0}
native_pose_paired=${native_pose_paired:-0}
native_pose_fallback=${native_pose_fallback:-0}
native_pose_bootstrap=${native_pose_bootstrap:-0}
native_pose_generation_gaps=${native_pose_generation_gaps:-0}
native_pose_timestamp_reuses=${native_pose_timestamp_reuses:-0}
native_last_pose_generation=${native_last_pose_generation:-0}
native_wall_ms=${native_wall_ms:-0}
native_conversion_average_us=${native_conversion_average_us:-0}
native_conversion_max_us=${native_conversion_max_us:-0}
native_conversion_gpu_average_us=${native_conversion_gpu_average_us:-0}
native_conversion_gpu_max_us=${native_conversion_gpu_max_us:-0}
native_connected=${native_connected:-false}
native_effective_fps=$(awk -v frames="$native_encoded" -v milliseconds="$native_wall_ms" \
	'BEGIN { if (milliseconds > 0) printf "%.3f", frames * 1000 / milliseconds; else print "0.000" }')
client_connected=$(count_matches 'Successful connection!' "$run_dir/avp-client-console.log")
client_stream_started=$(count_matches 'streaming started ' "$run_dir/avp-client-console.log")
client_decoder_created=$(count_matches 'create decoder ' "$run_dir/avp-client-console.log")
client_format_created=$(count_matches 'Successfully created CMVideoFormatDescription' "$run_dir/avp-client-console.log")
client_dimensions_matched=$(count_matches "dimensions: $output_width x $output_height" "$run_dir/avp-client-console.log")
client_decoder_errors=$(count_matches 'Fatal decoder error' "$run_dir/avp-client-console.log")
client_decoder_resets=$(count_matches 'Force reset decoder' "$run_dir/avp-client-console.log")
openvr_view_feedback_ready=$(count_matches 'OpenVR view feedback ready' "$run_dir/native-bridge.log")
openvr_pose_feedback_ready=$(count_matches 'OpenVR HMD pose feedback ready' "$run_dir/native-bridge.log")
exact_frame_pose_ready=$(count_matches 'alvr_sink exact frame pose ' "$run_dir/native-bridge.log")
native_bootstrap_epochs=$(count_matches 'alvr_sink decoder bootstrap .*index=1/3' "$run_dir/native-bridge.log")
native_bilinear_resampler=$(count_matches 'metal_converter resampler=bilinear eye_boundary=clamped' "$run_dir/native-bridge.log")
native_producer_handshake=$(count_matches 'native_source producer handshake accepted' "$run_dir/native-bridge.log")
native_startup_self_tests=$(count_matches 'native_source startup self-tests passed' "$run_dir/native-bridge.log")
launchd_service_checked_in=$(count_matches \
	'native_source launchd service checked in' "$run_dir/native-bridge.log")
launchd_import_rejections=$(count_matches \
	'native_source rejected import request' "$run_dir/native-bridge.log")
launchd_frame_rejections=$(count_matches \
	'native_source rejected frame-ready' "$run_dir/native-bridge.log")
launchd_oversize_import_rejections=$(count_matches \
	'native_source rejected import request .*reason=message-too-large' \
	"$run_dir/native-bridge.log")
launchd_oversize_frame_rejections=$(count_matches \
	'native_source rejected frame-ready reason=message-too-large' \
	"$run_dir/native-bridge.log")
launchd_unexpected_import_rejections=$((\
	launchd_import_rejections - launchd_oversize_import_rejections))
launchd_unexpected_frame_rejections=$((\
	launchd_frame_rejections - launchd_oversize_frame_rejections))
launchd_job_absent_after_restore=0
if ! /bin/launchctl print "$launch_agent_target" >/dev/null 2>&1; then
	launchd_job_absent_after_restore=1
fi
native_exact_pose_timeout=$(count_matches 'ALVR exact render pose did not become ready' "$run_dir/native-bridge.log")
fake_shared_view_reads=$(count_matches 'using shared view' "$run_dir/fake-openvr.log")
pacing_line=$(rg -a 'fake pacing summary ' "$run_dir/fake-openvr.log" 2>/dev/null | tail -n 1 || true)
pacing_value() {
	printf '%s\n' "$pacing_line" | tr ' ' '\n' | awk -F= -v key="$1" '$1 == key {print $2}'
}
fake_pacing_reported_mode=$(pacing_value mode)
fake_pacing_reported_fixed_sleep_ms=$(pacing_value fixed_sleep_ms)
fake_pacing_calls=$(pacing_value calls)
fake_pacing_frame=$(pacing_value frame)
fake_pacing_skipped_frames=$(pacing_value skipped_frames)
fake_pacing_deadline_misses=$(pacing_value deadline_misses)
fake_pacing_clock_regressions=$(pacing_value clock_regressions)
fake_pacing_average_wait_us=$(pacing_value avg_wait_us)
fake_pacing_max_wait_us=$(pacing_value max_wait_us)
fake_pacing_max_lateness_us=$(pacing_value max_lateness_us)
fake_shared_pose_reads=$(pacing_value shared_pose_reads)
fake_pacing_reported_mode=${fake_pacing_reported_mode:-missing}
fake_pacing_reported_fixed_sleep_ms=${fake_pacing_reported_fixed_sleep_ms:-missing}
fake_pacing_calls=${fake_pacing_calls:-0}
fake_pacing_frame=${fake_pacing_frame:-0}
fake_pacing_skipped_frames=${fake_pacing_skipped_frames:-0}
fake_pacing_deadline_misses=${fake_pacing_deadline_misses:-0}
fake_pacing_clock_regressions=${fake_pacing_clock_regressions:-0}
fake_pacing_average_wait_us=${fake_pacing_average_wait_us:-0}
fake_pacing_max_wait_us=${fake_pacing_max_wait_us:-0}
fake_pacing_max_lateness_us=${fake_pacing_max_lateness_us:-0}
fake_shared_pose_reads=${fake_shared_pose_reads:-0}
pacing_pass=1
if [[ $fake_pacing_mode == deadline ]]; then
	pacing_pass=0
	if [[ $fake_pacing_reported_mode == deadline && $fake_pacing_clock_regressions -eq 0 ]] &&
		[[ $fake_pacing_calls -gt 0 ]] &&
		awk -v frames="$producer_tail_window" -v fps="$producer_tail_effective_fps" \
			-v minimum="$producer_min_fps" -v maximum="$producer_max_fps" \
			'BEGIN { exit !(frames < 60 || (fps >= minimum && fps <= maximum)) }'; then
		pacing_pass=1
	fi
else
	pacing_pass=0
	if [[ $fake_pacing_reported_mode == fixed-sleep &&
		$fake_pacing_reported_fixed_sleep_ms == "$fake_wait_get_poses_sleep_ms" &&
		$fake_pacing_calls -gt 0 ]]; then
		pacing_pass=1
	fi
fi
{
	printf 'bridge_finished=%d\n' "$bridge_finished"
	printf 'bridge_status=%d\n' "$bridge_status"
	printf 'bridge_summary_seen=%d\n' "$bridge_summary_seen"
	printf 'native_bridge_exited=%d\n' "$native_bridge_exited"
	printf 'native_bridge_exit_status=%d\n' "$native_bridge_exit_status"
	printf 'restore_status=%d\n' "$restore_status"
	printf 'service_type=launchd-mach\n'
	printf 'launchd_service_checked_in=%s\n' "$launchd_service_checked_in"
	printf 'launchd_import_rejections=%s\n' "$launchd_import_rejections"
	printf 'launchd_frame_rejections=%s\n' "$launchd_frame_rejections"
	printf 'launchd_oversize_import_rejections=%s\n' "$launchd_oversize_import_rejections"
	printf 'launchd_oversize_frame_rejections=%s\n' "$launchd_oversize_frame_rejections"
	printf 'launchd_unexpected_import_rejections=%s\n' "$launchd_unexpected_import_rejections"
	printf 'launchd_unexpected_frame_rejections=%s\n' "$launchd_unexpected_frame_rejections"
	printf 'launchd_stale_job_found=%s\n' "$launchd_stale_job_found"
	printf 'launchd_stale_job_owned=%s\n' "$launchd_stale_job_owned"
	printf 'launchd_stale_job_booted_out=%s\n' "$launchd_stale_job_booted_out"
	printf 'launchd_start_identity_ok=%s\n' "$launchd_start_identity_ok"
	printf 'launchd_exit_identity_ok=%s\n' "$launchd_exit_identity_ok"
	printf 'launchd_bootout_identity_ok=%s\n' "$launchd_bootout_identity_ok"
	printf 'launchd_bootout_ok=%s\n' "$launch_agent_bootout_ok"
	printf 'launchd_job_absent_after_restore=%s\n' "$launchd_job_absent_after_restore"
	printf 'pressure_pause_ms=%s\n' "$pressure_pause_ms"
	printf 'fake_pacing_mode=%s\n' "$fake_pacing_mode"
	printf 'fake_wait_get_poses_sleep_ms=%s\n' "$fake_wait_get_poses_sleep_ms"
	printf 'expected_source_transition=%s\n' "$expected_source_transition"
	printf 'producer_min_fps=%s\n' "$producer_min_fps"
	printf 'producer_max_fps=%s\n' "$producer_max_fps"
	printf 'pressure_applied=%d\n' "$pressure_applied"
	printf 'self_tests=%s\n' "$self_tests"
	printf 'producer_resize_self_tests=%s\n' "$producer_resize_self_tests"
	printf 'producer_linear_resize_self_tests=%s\n' "$producer_linear_resize_self_tests"
	printf 'submitted=%s\n' "$submitted"
	printf 'producer_total_submissions=%s\n' "$producer_total_submissions"
	printf 'producer_closed_frame_id=%s\n' "$producer_closed_frame_id"
	printf 'producer_post_close_submissions=%s\n' "$producer_post_close_submissions"
	printf 'producer_release_failures_before_close=%s\n' "$producer_release_failures_before_close"
	printf 'producer_post_close_release_failures=%s\n' "$producer_post_close_release_failures"
	printf 'producer_startup_drops=%s\n' "$producer_startup_drops"
	printf 'producer_steady_state_drops=%s\n' "$producer_steady_state_drops"
	printf 'producer_backpressure_waits=%s\n' "$producer_backpressure_waits"
	printf 'producer_backpressure_max_us=%s\n' "$producer_backpressure_max_us"
	printf 'producer_pressure_recovery_releases=%s\n' "$producer_pressure_recovery_releases"
	printf 'producer_pressure_recovery_slots=%s\n' "$producer_pressure_recovery_slots"
	printf 'producer_source_transitions=%s\n' "$producer_source_transitions"
	printf 'producer_resized_submissions=%s\n' "$producer_resized_submissions"
	printf 'producer_separate_eye_submissions=%s\n' "$producer_separate_eye_submissions"
	printf 'producer_expected_resize_submissions=%s\n' "$producer_expected_resize_submissions"
	printf 'resize_expectation_pass=%s\n' "$resize_expectation_pass"
	printf 'consumer_validated=%s\n' "$consumer_validated"
	printf 'production_content_gate=visual-required\n'
	printf 'released=%s\n' "$released"
	printf 'closed=%s\n' "$closed"
	printf 'release_drops=%s\n' "$release_drops"
	printf 'producer_drop_log_count=%s\n' "$producer_drops"
	printf 'producer_timed_submissions=%s\n' "$producer_timed_submissions"
	printf 'producer_duration_ms=%s\n' "$producer_duration_ms"
	printf 'producer_effective_fps=%s\n' "$producer_effective_fps"
	printf 'producer_tail_window=%s\n' "$producer_tail_window"
	printf 'producer_tail_effective_fps=%s\n' "$producer_tail_effective_fps"
	printf 'nonblack_releases=%s\n' "$nonblack_releases"
	printf 'slots_used=%s\n' "$slots_used"
	printf 'producer_pose_paired=%s\n' "$producer_pose_paired"
	printf 'producer_pose_fallback=%s\n' "$producer_pose_fallback"
	printf 'native_self_tests=%s\n' "$native_self_tests"
	printf 'native_report_kind=%s\n' "$native_report_kind"
	printf 'native_producer_handshake=%s\n' "$native_producer_handshake"
	printf 'native_startup_self_tests=%s\n' "$native_startup_self_tests"
	printf 'native_exact_pose_timeout=%s\n' "$native_exact_pose_timeout"
	printf 'native_received=%s\n' "$native_received"
	printf 'native_submitted=%s\n' "$native_submitted"
	printf 'native_encoded=%s\n' "$native_encoded"
	printf 'native_transported=%s\n' "$native_transported"
	printf 'native_encoded_bytes=%s\n' "$native_encoded_bytes"
	printf 'native_transported_bytes=%s\n' "$native_transported_bytes"
	printf 'native_encoded_mbps=%s\n' "$native_encoded_mbps"
	printf 'native_keyframes=%s\n' "$native_keyframes"
	printf 'native_keyframe_bytes=%s\n' "$native_keyframe_bytes"
	printf 'native_max_frame_bytes=%s\n' "$native_max_frame_bytes"
	printf 'native_video_span_ms=%s\n' "$native_video_span_ms"
	printf 'native_dropped=%s\n' "$native_dropped"
	printf 'native_not_ready_drops=%s\n' "$native_not_ready_drops"
	printf 'native_pool_exhausted_drops=%s\n' "$native_pool_exhausted_drops"
	printf 'native_pose_paired=%s\n' "$native_pose_paired"
	printf 'native_pose_fallback=%s\n' "$native_pose_fallback"
	printf 'native_pose_bootstrap=%s\n' "$native_pose_bootstrap"
	printf 'native_pose_generation_gaps=%s\n' "$native_pose_generation_gaps"
	printf 'native_pose_timestamp_reuses=%s\n' "$native_pose_timestamp_reuses"
	printf 'native_last_pose_generation=%s\n' "$native_last_pose_generation"
	printf 'native_wall_ms=%s\n' "$native_wall_ms"
	printf 'native_effective_fps=%s\n' "$native_effective_fps"
	printf 'native_conversion_average_us=%s\n' "$native_conversion_average_us"
	printf 'native_conversion_max_us=%s\n' "$native_conversion_max_us"
	printf 'native_conversion_gpu_average_us=%s\n' "$native_conversion_gpu_average_us"
	printf 'native_conversion_gpu_max_us=%s\n' "$native_conversion_gpu_max_us"
	printf 'native_connected=%s\n' "$native_connected"
	printf 'avp_device_id=%s\n' "$avp_device_id"
	printf 'avp_device_udid=%s\n' "$avp_device_udid"
	printf 'avp_bundle_id=%s\n' "$avp_bundle_id"
	printf 'avp_app_version=%s\n' "$avp_app_version"
	printf 'avp_app_build=%s\n' "$avp_app_build"
	printf 'avp_expected_protocol=%s\n' "$avp_expected_protocol"
	printf 'avp_client_id=%s\n' "$avp_client_id"
	printf 'avp_client_ip=%s\n' "$avp_client_ip"
	printf 'avp_client_protocol=%s\n' "$avp_client_protocol"
	printf 'avp_client_ready=%s\n' "$avp_client_ready"
	printf 'avp_client_ready_latency_ms=%s\n' "$avp_client_ready_latency_ms"
	printf 'avp_client_connection_latency_ms=%s\n' "$avp_client_connection_latency_ms"
	printf 'avp_sink_connection_latency_ms=%s\n' "$avp_sink_connection_latency_ms"
	printf 'avp_session_seeded=%s\n' "$avp_session_seeded"
	printf 'avp_session_identity_ok=%s\n' "$avp_session_identity_ok"
	printf 'avp_post_host_observed=%s\n' "$avp_post_host_observed"
	printf 'avp_post_host_stream_stopped=%s\n' "$avp_post_host_stream_stopped"
	printf 'avp_post_host_baseline_line=%s\n' "$avp_post_host_baseline_line"
	printf 'avp_post_host_stale_ipd=%s\n' "$avp_post_host_stale_ipd"
	printf 'avp_post_host_stale_origin=%s\n' "$avp_post_host_stale_origin"
	printf 'avp_post_host_stale_format=%s\n' "$avp_post_host_stale_format"
	printf 'avp_post_host_status=%s\n' "$avp_post_host_status"
	printf 'avp_client_stopped=%s\n' "$avp_client_stopped"
	printf 'client_connected=%s\n' "$client_connected"
	printf 'client_stream_started=%s\n' "$client_stream_started"
	printf 'client_decoder_created=%s\n' "$client_decoder_created"
	printf 'client_format_created=%s\n' "$client_format_created"
	printf 'client_dimensions_matched=%s\n' "$client_dimensions_matched"
	printf 'client_decoder_errors=%s\n' "$client_decoder_errors"
	printf 'client_decoder_resets=%s\n' "$client_decoder_resets"
	printf 'openvr_view_feedback_ready=%s\n' "$openvr_view_feedback_ready"
	printf 'openvr_pose_feedback_ready=%s\n' "$openvr_pose_feedback_ready"
	printf 'exact_frame_pose_ready=%s\n' "$exact_frame_pose_ready"
	printf 'native_bootstrap_epochs=%s\n' "$native_bootstrap_epochs"
	printf 'native_bilinear_resampler=%s\n' "$native_bilinear_resampler"
	printf 'fake_shared_view_reads=%s\n' "$fake_shared_view_reads"
	printf 'fake_shared_pose_reads=%s\n' "$fake_shared_pose_reads"
	printf 'fake_pacing_reported_mode=%s\n' "$fake_pacing_reported_mode"
	printf 'fake_pacing_reported_fixed_sleep_ms=%s\n' "$fake_pacing_reported_fixed_sleep_ms"
	printf 'fake_pacing_calls=%s\n' "$fake_pacing_calls"
	printf 'fake_pacing_frame=%s\n' "$fake_pacing_frame"
	printf 'fake_pacing_skipped_frames=%s\n' "$fake_pacing_skipped_frames"
	printf 'fake_pacing_deadline_misses=%s\n' "$fake_pacing_deadline_misses"
	printf 'fake_pacing_clock_regressions=%s\n' "$fake_pacing_clock_regressions"
	printf 'fake_pacing_average_wait_us=%s\n' "$fake_pacing_average_wait_us"
	printf 'fake_pacing_max_wait_us=%s\n' "$fake_pacing_max_wait_us"
	printf 'fake_pacing_max_lateness_us=%s\n' "$fake_pacing_max_lateness_us"
	printf 'pacing_pass=%s\n' "$pacing_pass"
} >"$run_dir/status.txt"

verdict=fail
common_pass=0
if [[ $bridge_finished -eq 1 && $bridge_status -eq 0 &&
	$bridge_summary_seen -eq 1 && $native_bridge_exited -eq 1 &&
	$native_bridge_exit_status -eq 0 && $restore_status -eq 0 ]] &&
	[[ $launchd_service_checked_in -eq 1 &&
		$launchd_oversize_import_rejections -eq 1 &&
		$launchd_oversize_frame_rejections -eq 1 &&
		$launchd_unexpected_import_rejections -eq 0 &&
		$launchd_unexpected_frame_rejections -eq 0 &&
		$launchd_start_identity_ok -eq 1 && $launchd_exit_identity_ok -eq 1 &&
		$launchd_bootout_identity_ok -eq 1 &&
		$launch_agent_bootout_ok -eq 1 && $launchd_job_absent_after_restore -eq 1 ]] &&
	[[ $pacing_pass -eq 1 ]] &&
	[[ $self_tests -eq 3 && $producer_resize_self_tests -eq 3 &&
		$submitted -eq $released && $released -eq $native_received && $closed -eq 1 ]] &&
	[[ $producer_source_transitions -eq 0 || $producer_resized_submissions -gt 0 ||
		$producer_separate_eye_submissions -gt 0 ]] &&
	[[ $resize_expectation_pass -eq 1 ]] &&
	[[ $native_self_tests -eq 3 && $native_submitted -eq $native_frames && $native_encoded -eq $native_frames ]] &&
	[[ $native_report_kind == summary && $native_producer_handshake -eq 1 &&
		$native_startup_self_tests -eq 1 && $native_exact_pose_timeout -eq 0 ]] &&
	[[ $native_encoded_bytes -gt 0 && $native_keyframes -gt 0 && $native_max_frame_bytes -gt 0 ]] &&
	[[ $native_bilinear_resampler -gt 0 ]] &&
	[[ $native_received -eq $((native_submitted + native_dropped)) ]] &&
	[[ $((producer_pose_paired + producer_pose_fallback)) -eq $submitted ]] &&
	[[ $((native_pose_paired + native_pose_fallback)) -eq $((native_submitted + native_dropped)) ]] &&
	[[ $native_pose_generation_gaps -eq 0 ]] &&
	[[ $producer_backpressure_max_us -le 100000 ]] &&
	[[ $native_dropped -eq $((native_not_ready_drops + native_pool_exhausted_drops)) ]] &&
	[[ $native_pool_exhausted_drops -eq 0 ]] &&
	[[ $release_drops -eq $native_dropped ]] &&
	[[ $producer_release_failures_before_close -eq 0 ]] &&
	[[ $producer_post_close_submissions -eq $producer_post_close_release_failures ]] &&
	! rg -q 'iosurface pool failed|worker_drain=timeout' "$run_dir/openvr-submit-shim.log" &&
	rg -q 'iosurface pool initialized' "$run_dir/openvr-submit-shim.log" &&
	! rg -q 'source frame stats|published Submit pair' "$run_dir/openvr-submit-shim.log"; then
	common_pass=1
fi
if [[ $common_pass -eq 1 && $native_connect == false ]] &&
	[[ $pressure_pause_ms -eq 0 && $producer_startup_drops -le 3 &&
		$producer_steady_state_drops -eq 0 ]] &&
	[[ $native_dropped -eq 0 && $native_transported -eq 0 && $native_connected == false ]] &&
	[[ $native_transported_bytes -eq 0 ]] &&
	[[ $native_pose_fallback -eq $native_submitted && $native_pose_paired -eq 0 &&
		$native_pose_bootstrap -eq 0 ]]; then
	verdict=pass
elif [[ $common_pass -eq 1 && $native_connect == false ]] &&
	[[ $pressure_pause_ms -gt 0 && $pressure_applied -eq 1 ]] &&
	[[ $producer_startup_drops -le 3 && $producer_steady_state_drops -gt 0 &&
		$producer_pressure_recovery_releases -ge 3 &&
		$producer_pressure_recovery_slots -eq 3 && $slots_used -eq 3 ]] &&
	[[ $native_dropped -eq 0 && $native_transported -eq 0 && $native_connected == false ]] &&
	[[ $native_transported_bytes -eq 0 ]] &&
	[[ $native_pose_fallback -eq $native_submitted && $native_pose_paired -eq 0 &&
		$native_pose_bootstrap -eq 0 ]]; then
	verdict=pass
elif [[ $common_pass -eq 1 && $native_connect == true ]] &&
	[[ $avp_client_ready -eq 1 && $avp_session_seeded -eq 1 &&
		$avp_session_identity_ok -eq 1 && $avp_client_stopped -eq 1 ]] &&
	[[ $avp_client_connection_latency_ms -ge 0 && $avp_client_connection_latency_ms -le 2000 &&
		$avp_sink_connection_latency_ms -ge 0 && $avp_sink_connection_latency_ms -le 5000 ]] &&
	[[ $avp_post_host_status -eq 0 && $avp_post_host_observed -eq 1 &&
		$avp_post_host_stream_stopped -eq 1 && $avp_post_host_stale_ipd -eq 0 &&
		$avp_post_host_stale_origin -eq 0 && $avp_post_host_stale_format -eq 0 ]] &&
	[[ $producer_startup_drops -le 3 && $producer_steady_state_drops -eq 0 ]] &&
	[[ $native_transported -eq $native_encoded && $native_transported -gt 0 && $native_connected == true ]] &&
	[[ $native_transported_bytes -eq $native_encoded_bytes && $native_transported_bytes -gt 0 ]] &&
	[[ $native_pose_bootstrap -ge $native_bootstrap_epochs &&
		$native_pose_bootstrap -le $((native_bootstrap_epochs * 3)) ]] &&
	[[ $native_pose_paired -eq $((native_submitted - native_pose_bootstrap + native_pool_exhausted_drops)) ]] &&
	[[ $native_pose_fallback -eq $((native_not_ready_drops + native_pose_bootstrap)) &&
		$native_last_pose_generation -gt 0 ]] &&
	[[ $openvr_pose_feedback_ready -gt 0 && $exact_frame_pose_ready -gt 0 &&
		$fake_shared_pose_reads -gt 0 ]] &&
	[[ $client_connected -gt 0 && $client_stream_started -gt 0 && $client_decoder_created -gt 0 &&
		$client_format_created -gt 0 &&
		$client_dimensions_matched -gt 0 && $client_decoder_errors -eq 0 &&
		$client_decoder_resets -eq 0 ]]; then
	verdict=pass
fi
printf '%s\n' "$verdict" >"$run_dir/verdict.txt"
printf '%s\n' "$run_dir"
[[ $verdict == pass ]]
