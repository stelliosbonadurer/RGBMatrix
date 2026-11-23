#!/bin/sh
# Keep the Pi's onboard audio driver from loading.
#
# The adafruit-hat-pwm matrix mapping uses the hardware PWM pulser, and the
# rpi-rgb-led-matrix library exits at startup whenever snd_bcm2835 appears in
# /proc/modules. dtparam=audio=off removes the headphone card but the module still
# loads, so a blacklist is required for the panel to start after a reboot.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FILES_DIR="$SCRIPT_DIR/files"
MODPROBE_DEST=/etc/modprobe.d/rgb-matrix-onboard-audio-off.conf
BOOT_CONFIG=/boot/firmware/config.txt

fail() {
    echo "onboard-audio-off install: $*" >&2
    exit 1
}

[ "$(id -u)" -ne 0 ] || fail "run as the desktop user, not root"
for command in sudo install cmp grep; do
    command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
done
MODPROBE=$(sudo sh -c 'command -v modprobe') || \
    fail "missing privileged command: modprobe"
case "$MODPROBE" in
    /*) ;;
    *) fail "privileged modprobe path is not absolute" ;;
esac

if sudo test -e "$MODPROBE_DEST"; then
    sudo cmp -s "$FILES_DIR/modprobe.conf" "$MODPROBE_DEST" || \
        fail "refusing to replace $MODPROBE_DEST"
else
    sudo install -D -m 0644 "$FILES_DIR/modprobe.conf" "$MODPROBE_DEST"
fi

if grep -q '^dtparam=audio=on' "$BOOT_CONFIG" 2>/dev/null; then
    echo "note: $BOOT_CONFIG still has dtparam=audio=on; set it to off as well."
fi

if grep -q '^snd_bcm2835 ' /proc/modules; then
    # Unloading fails while a client (usually WirePlumber) holds the headphone
    # card. That is fine: the blacklist applies from the next boot.
    if sudo "$MODPROBE" -r snd_bcm2835 2>/dev/null; then
        echo "snd_bcm2835 unloaded."
    else
        echo "snd_bcm2835 is in use; reboot to apply the blacklist."
        exit 0
    fi
fi

echo "Onboard audio blacklisted; adafruit-hat-pwm can use the hardware pulser."
