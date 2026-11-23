#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FILES_DIR="$SCRIPT_DIR/files"
MODULES_DEST=/etc/modules-load.d/rgb-matrix-bluetooth-audio.conf
MODPROBE_DEST=/etc/modprobe.d/rgb-matrix-bluetooth-audio.conf
WP_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/wireplumber/main.lua.d
WP_DEST="$WP_DIR/60-rgb-matrix-bluetooth-audio.lua"
changed=0

fail() {
    echo "bluetooth-audio install: $*" >&2
    exit 1
}

[ "$(id -u)" -ne 0 ] || fail "run as the desktop user, not root"
for command in sudo install cmp systemctl wireplumber wpctl awk grep sleep cat; do
    command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
done
MODPROBE=$(sudo sh -c 'command -v modprobe') || \
    fail "missing privileged command: modprobe"
case "$MODPROBE" in
    /*) ;;
    *) fail "privileged modprobe path is not absolute" ;;
esac

case "$(wireplumber --version 2>/dev/null)" in
    *0.4.*) ;;
    *) fail "this configuration requires WirePlumber 0.4.x" ;;
esac

install_user_file() {
    source_file=$1
    destination=$2
    if [ -e "$destination" ]; then
        cmp -s "$source_file" "$destination" || fail "refusing to replace $destination"
        return
    fi
    install -D -m 0644 "$source_file" "$destination"
    changed=1
}

install_root_file() {
    source_file=$1
    destination=$2
    if sudo test -e "$destination"; then
        sudo cmp -s "$source_file" "$destination" || fail "refusing to replace $destination"
        return
    fi
    sudo install -D -m 0644 "$source_file" "$destination"
    changed=1
}

install_root_file "$FILES_DIR/modules-load.conf" "$MODULES_DEST"
install_root_file "$FILES_DIR/modprobe.conf" "$MODPROBE_DEST"
install_user_file "$FILES_DIR/60-rgb-matrix-bluetooth-audio.lua" "$WP_DEST"

if ! grep -q '^snd_aloop ' /proc/modules; then
    sudo "$MODPROBE" snd-aloop
    changed=1
fi
grep -q '^ *10 .*Loopback' /proc/asound/cards || \
    fail "snd-aloop is not card 10; uninstall or reboot before retrying"
loaded_indexes=$(cat /sys/module/snd_aloop/parameters/index)
loaded_substreams=$(cat /sys/module/snd_aloop/parameters/pcm_substreams)
[ "${loaded_indexes%%,*}" = "10" ] || \
    fail "loaded snd-aloop index does not match the managed configuration"
[ "${loaded_substreams%%,*}" = "1" ] || \
    fail "loaded snd-aloop substream count does not match the managed configuration"

if [ "$changed" -eq 1 ]; then
    systemctl --user restart wireplumber
fi

sink_id=
attempt=0
while [ "$attempt" -lt 20 ]; do
    sink_ids=$(wpctl status 2>/dev/null | awk '
        /Bluetooth loopback \(RGB Matrix\)/ {
            line = $0
            sub(/^[^0-9]*/, "", line)
            sub(/\..*$/, "", line)
            if (line ~ /^[0-9]+$/) print line
        }
    ')
    # Word splitting is intentional: exactly one matching node is required.
    set -- $sink_ids
    if [ "$#" -eq 1 ]; then
        sink_id=$1
        break
    fi
    [ "$#" -eq 0 ] || fail "multiple managed loopback sinks were found"
    attempt=$((attempt + 1))
    sleep 1
done
[ -n "$sink_id" ] || fail "managed loopback sink did not appear"

wpctl set-default "$sink_id"
wpctl set-volume "$sink_id" 1.0
wpctl set-mute "$sink_id" 0

echo "Bluetooth audio bridge installed; loopback sink is default at 100%."
if [ "$changed" -eq 1 ]; then
    echo "WirePlumber restarted; reselect the Pi on the phone if A2DP does not return."
fi
