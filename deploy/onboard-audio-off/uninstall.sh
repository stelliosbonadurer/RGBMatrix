#!/bin/sh
# Remove the onboard-audio blacklist. Switch led_gpio_mapping in
# config/matrix-controller.json back to adafruit-hat first: once snd_bcm2835 loads
# again, adafruit-hat-pwm exits at startup and the panel stays dark.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FILES_DIR="$SCRIPT_DIR/files"
MODPROBE_DEST=/etc/modprobe.d/rgb-matrix-onboard-audio-off.conf

fail() {
    echo "onboard-audio-off uninstall: $*" >&2
    exit 1
}

[ "$(id -u)" -ne 0 ] || fail "run as the desktop user, not root"
for command in sudo cmp rm; do
    command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
done

if sudo test -e "$MODPROBE_DEST"; then
    sudo cmp -s "$FILES_DIR/modprobe.conf" "$MODPROBE_DEST" || \
        fail "refusing to remove modified file $MODPROBE_DEST"
    sudo rm -f -- "$MODPROBE_DEST"
fi

echo "Onboard audio blacklist removed; it takes effect at the next boot."
