#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FILES_DIR="$SCRIPT_DIR/files"
MODULES_DEST=/etc/modules-load.d/rgb-matrix-bluetooth-audio.conf
MODPROBE_DEST=/etc/modprobe.d/rgb-matrix-bluetooth-audio.conf
WP_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/wireplumber/main.lua.d
WP_DEST="$WP_DIR/60-rgb-matrix-bluetooth-audio.lua"
needs_restart=0
wireplumber_stopped=0

fail() {
    echo "bluetooth-audio uninstall: $*" >&2
    exit 1
}

[ "$(id -u)" -ne 0 ] || fail "run as the desktop user, not root"
for command in sudo cmp modprobe systemctl wpctl awk grep rm rmdir; do
    command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
done

verify_user_file() {
    source_file=$1
    destination=$2
    [ ! -e "$destination" ] || cmp -s "$source_file" "$destination" || \
        fail "refusing to remove modified file $destination"
}

verify_root_file() {
    source_file=$1
    destination=$2
    if sudo test -e "$destination"; then
        sudo cmp -s "$source_file" "$destination" || \
            fail "refusing to remove modified file $destination"
    fi
}

verify_user_file "$FILES_DIR/60-rgb-matrix-bluetooth-audio.lua" "$WP_DEST"
verify_root_file "$FILES_DIR/modules-load.conf" "$MODULES_DEST"
verify_root_file "$FILES_DIR/modprobe.conf" "$MODPROBE_DEST"

sink_ids=$(wpctl status 2>/dev/null | awk '
    /Bluetooth loopback \(RGB Matrix\)/ {
        line = $0
        sub(/^[^0-9]*/, "", line)
        sub(/\..*$/, "", line)
        if (line ~ /^[0-9]+$/) print line
    }
')
set -- $sink_ids
[ "$#" -le 1 ] || fail "multiple managed loopback sinks were found"
if [ "$#" -eq 1 ]; then
    wpctl clear-default "$1" || fail "could not clear the managed default sink"
fi

if [ -e "$WP_DEST" ]; then
    rm -f -- "$WP_DEST"
    needs_restart=1
fi
if sudo test -e "$MODULES_DEST"; then
    sudo rm -f -- "$MODULES_DEST"
fi
if sudo test -e "$MODPROBE_DEST"; then
    sudo rm -f -- "$MODPROBE_DEST"
fi

if grep -q '^snd_aloop ' /proc/modules; then
    needs_restart=1
fi

restart_wireplumber() {
    if [ "$wireplumber_stopped" -eq 1 ]; then
        systemctl --user start wireplumber
        wireplumber_stopped=0
    fi
}
trap restart_wireplumber EXIT HUP INT TERM

if [ "$needs_restart" -eq 1 ]; then
    # WirePlumber retains /dev/snd/controlC10 after its rule disappears. This exact
    # stop -> unload -> start order is required for a clean, bounded reversal.
    systemctl --user stop wireplumber
    wireplumber_stopped=1
    if grep -q '^snd_aloop ' /proc/modules; then
        sudo modprobe -r snd-aloop
    fi
    restart_wireplumber
fi

trap - EXIT HUP INT TERM
rmdir -- "$WP_DIR" 2>/dev/null || true
rmdir -- "$(dirname -- "$WP_DIR")" 2>/dev/null || true

grep -q '^snd_aloop ' /proc/modules && fail "snd-aloop is still loaded"
echo "Bluetooth audio bridge removed; WirePlumber is running in USB-only state."
