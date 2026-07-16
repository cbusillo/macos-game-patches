#!/usr/bin/env bash

set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
version=1.0.28
release_root="$repo/.code/vendor/open-brush/$version/desktop/OpenBrush_Desktop_$version"
release_zip="$repo/.code/vendor/open-brush/$version/OpenBrush_Desktop_$version.zip"
runtime_dir="$release_root/OpenBrush_Data/Plugins/x86_64"
alvr_checkout=${ALVR_CHECKOUT:-"$HOME/.code/working/alvr/branches/native-surface-contract"}
output_root="$repo/.code/probes/011-open-brush-controller-smoke"
mirror_width=${OPEN_BRUSH_MIRROR_WIDTH:-1280}
mirror_height=${OPEN_BRUSH_MIRROR_HEIGHT:-720}
eye_width=${OPEN_BRUSH_EYE_WIDTH:-1080}
eye_height=${OPEN_BRUSH_EYE_HEIGHT:-1344}
native_frames=${ALVR_NATIVE_PROBE_FRAMES:-900}
source_width=$((eye_width * 2))
video_file="$HOME/Library/Application Support/CrossOver/Bottles/Steam/drive_c/users/crossover/Documents/Open Brush/Media Library/Videos/animated-logo.mp4"
disabled_video="$video_file.alvr-probe-disabled"
video_disabled=0

hash_file() {
	shasum -a 256 "$1" | awk '{print $1}'
}

restore_video() {
	if [[ $video_disabled -eq 1 ]]; then
		mv "$disabled_video" "$video_file" || return 1
		video_disabled=0
	fi
}

latest_run() {
	find "$output_root" -maxdepth 1 -type d -name 'real-native-encode-*' -print 2>/dev/null |
		LC_ALL=C sort |
		tail -n 1
}

trap 'restore_video || true' EXIT

for variable in mirror_width mirror_height eye_width eye_height; do
	value=${!variable}
	[[ $value =~ ^[1-9][0-9]*$ ]] || {
		echo "$variable must be a positive integer" >&2
		exit 1
	}
done
((mirror_width * 9 == mirror_height * 16)) || {
	echo "Open Brush mirror size must use a 16:9 aspect ratio" >&2
	exit 1
}
((eye_width * 56 == eye_height * 45)) || {
	echo "Open Brush eye size must match the 45:56 ALVR eye aspect ratio" >&2
	exit 1
}

for path in \
	"$release_zip" \
	"$release_root/OpenBrush.exe" \
	"$release_root/OpenBrush_Data/boot.config" \
	"$runtime_dir/openvr_api.dll" \
	"$alvr_checkout/Cargo.toml"; do
	[[ -f $path ]] || {
		echo "missing=$path" >&2
		exit 1
	}
done

[[ $(hash_file "$release_zip") == 5534d2e324e3317232324fea3991d8143da7592b3e0da19e4822755e6df8e371 ]] || {
	echo "Open Brush $version release archive hash mismatch" >&2
	exit 1
}
[[ $(hash_file "$runtime_dir/openvr_api.dll") == bd7a7958bdb647096e5e22cb4d020dd99720983f3af1cd500e8b570cfa9f017b ]] || {
	echo "Open Brush $version OpenVR runtime hash mismatch" >&2
	exit 1
}
rg -q '^vr-device-list=OpenVR$' "$release_root/OpenBrush_Data/boot.config" || {
	echo "Open Brush $version is not configured for OpenVR" >&2
	exit 1
}
[[ ! -e $disabled_video ]] || {
	echo "stale disabled Open Brush sample video: $disabled_video" >&2
	exit 1
}
if [[ -f $video_file ]]; then
	mv "$video_file" "$disabled_video"
	video_disabled=1
fi

previous_run=$(latest_run)
set +e
env \
	ALVR_CHECKOUT="$alvr_checkout" \
	ALVR_NATIVE_PROBE_APP_NAME="Open Brush $version" \
	ALVR_NATIVE_PROBE_PROCESS_PATTERN='[O]penBrush.exe' \
	ALVR_NATIVE_PROBE_GAME_DIR="$release_root" \
	ALVR_NATIVE_PROBE_OPENVR_DIR="$runtime_dir" \
	ALVR_NATIVE_PROBE_EXECUTABLE="$release_root/OpenBrush.exe" \
	ALVR_NATIVE_PROBE_WORKDIR="$release_root" \
	ALVR_NATIVE_PROBE_ARGUMENTS="-force-d3d11 -screen-fullscreen 0 -screen-width $mirror_width -screen-height $mirror_height" \
	ALVR_NATIVE_PROBE_EXTRA_ENV="ALVR_FAKE_RENDER_TARGET_WIDTH=$eye_width ALVR_FAKE_RENDER_TARGET_HEIGHT=$eye_height" \
	ALVR_NATIVE_PROBE_LAUNCHER_SOURCE=tools/run_open_brush_native_probe.sh \
	ALVR_NATIVE_PROBE_OUTPUT_ROOT="$output_root" \
	ALVR_NATIVE_PROBE_STOCK_OPENVR_HASH=bd7a7958bdb647096e5e22cb4d020dd99720983f3af1cd500e8b570cfa9f017b \
	ALVR_NATIVE_PROBE_SOURCE_WIDTH="$source_width" \
	ALVR_NATIVE_PROBE_SOURCE_HEIGHT="$eye_height" \
	ALVR_NATIVE_PROBE_OUTPUT_WIDTH=2880 \
	ALVR_NATIVE_PROBE_OUTPUT_HEIGHT=1792 \
	ALVR_NATIVE_PROBE_FRAMES="$native_frames" \
	ALVR_NATIVE_PROBE_MIN_PRODUCER_FPS=60 \
	ALVR_NATIVE_PROBE_MAX_PRODUCER_FPS=100 \
	"$repo/tools/run_real_native_iosurface_probe.sh"
status=$?
set -e

current_run=$(latest_run)
if [[ -n $current_run && $current_run != "$previous_run" ]]; then
	player_log="$HOME/Library/Application Support/CrossOver/Bottles/Steam/drive_c/users/crossover/AppData/LocalLow/Icosa/Open Brush/Player.log"
	[[ -f $player_log ]] && cp -p "$player_log" "$current_run/open-brush-player.log"
	{
		printf 'animated_logo_disabled=%d\n' "$video_disabled"
		printf 'mirror_size=%sx%s\n' "$mirror_width" "$mirror_height"
		printf 'eye_size=%sx%s\n' "$eye_width" "$eye_height"
		printf 'source_size=%sx%s\n' "$source_width" "$eye_height"
	} >"$current_run/open-brush-profile.txt"
fi
if ! restore_video; then
	status=1
fi
trap - EXIT
exit "$status"
