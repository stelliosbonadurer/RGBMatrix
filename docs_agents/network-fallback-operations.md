# Network Fallback and Wi-Fi Provisioning Operations

Status: implemented for Raspberry Pi OS Bookworm with NetworkManager. Automatic
fallback, D-Bus secret writes, and the physical recovery service have hardware-free
tests. Deployment and non-disruptive service health are verified on the current Pi.
A real radio transition and button press still require an attended acceptance run
because either action intentionally disconnects the operator.

Current deployment: `raspberrypi.local` / last known DHCP address `192.168.1.204` on
`SpectrumSetup-5B`. Provisioning to that network completed successfully; the former
`SPhone` profile remains saved as a lower-priority recovery candidate.

## Behavior

`matrix_network` is a root-owned, NetworkManager-only controller for `wlan0`. It
leaves an existing local IPv4 connection alone. Without one, it tries saved Wi-Fi
profiles, waits 60 seconds after boot (120 seconds after losing a working LAN), and
then activates the project-owned setup AP. Internet reachability is not required.

- Setup SSID: `RGB-Matrix-` plus the final four hexadecimal MAC digits.
- Setup address: `192.168.50.1/24`.
- Setup page: `http://192.168.50.1:8080/setup`.
- AP profile: `rgb-matrix-setup-ap`, autoconnect disabled, priority `-999`.
- Per-device WPA passphrase: `/var/lib/rgb-matrix-network/setup-passphrase`, mode 0600.
- Privileged unit: `rgb-matrix-network@USER.service`.
- Physical recovery unit: `rgb-matrix-recovery-button@USER.service`.
- Local socket: `/run/rgb-matrix-network/control.sock`, accessible to the service
  account group; it is not a TCP listener.

The same per-device value is the setup-page credential. Read it locally before a
planned recovery exercise:

```bash
sudo cat /var/lib/rgb-matrix-network/setup-passphrase
```

Do not paste that value into logs, issues, or shared shell history.

## Architecture and safety boundary

The web service remains unprivileged and proxies narrowly structured requests over a
Unix socket. Only the helper invokes `nmcli`; it uses fixed argument vectors and no
shell. NetworkManager stores working credentials. Candidate creation, including its
WPA key, is one atomic D-Bus `AddConnection2` transaction. Provisioning attempts that
candidate for 45 seconds and promotes it only after a local IPv4 address exists.
Failure deletes only that candidate and restores the setup AP. The active or last
saved profile cannot be forgotten.

Passwords are suppressed from exceptions, jobs, HTTP responses, logs, and process
arguments. The helper writes WPA keys over the system D-Bus with `dbus-python` and
sanitizes D-Bus failures because serialized settings errors may contain secrets.

## Physical recovery button

The local recovery action is a normally-open momentary button held for five seconds
and then released:

- button terminal 1: BCM GPIO 19, physical header pin 35;
- button terminal 2: ground, physical header pin 39;
- input bias: internal pull-up, configured by `gpiomon`;
- accepted gesture: continuous press for at least five seconds, then release.

GPIO 19 is unused by the project's `adafruit-hat` mapping. Do not casually choose a
different GPIO: the bonnet uses GPIO 4, 5, 6, 12, 13, 16, 17, 20, 21, 22, 23, 24, 26,
and 27 for panel output. The watcher is separate from the network state machine and
uses kernel edge timestamps, so switch bounce and wall-clock changes do not satisfy
the hold.

The watcher sends `local_force_ap` over the existing Unix socket without a bearer
token. The helper accepts that operation only when Linux `SO_PEERCRED` reports UID 0.
It is intentionally absent from the web API; a remote client cannot impersonate the
root GPIO watcher. The ordinary authenticated `force-ap` web operation remains
available for administrators.

The project deliberately does **not** retry known networks periodically while the AP
is active. A single Wi-Fi radio cannot associate elsewhere without disrupting setup
clients, so an automatic timer would make recovery access disappear unexpectedly.
Users explicitly choose **Retry saved networks** on `/setup`; the 60-second boot and
120-second sustained-loss policies still try known profiles before entering AP mode.

## Install and inspect

Raspberry Pi OS Bookworm prerequisites are NetworkManager, the system Python D-Bus
binding, and libgpiod tools. Install and preflight them before replacing a running
network unit:

```bash
sudo apt-get install network-manager python3-dbus gpiod
command -v nmcli
command -v gpiomon
python3 -c 'import dbus'
gpiomon --version
```

The recovery parser is verified with libgpiod 1.6.x, where custom `%e` events are `0`
for falling and `1` for rising. With the configured pull-up, falling means press and
rising means release. Revalidate that convention before deploying a different major
libgpiod version.

```bash
sudo install -m 0644 deploy/systemd/rgb-matrix-network@.service \
  /etc/systemd/system/rgb-matrix-network@.service
sudo install -m 0644 deploy/systemd/rgb-matrix-web@.service \
  /etc/systemd/system/rgb-matrix-web@.service
sudo install -m 0644 deploy/systemd/rgb-matrix-recovery-button@.service \
  /etc/systemd/system/rgb-matrix-recovery-button@.service
sudo systemctl daemon-reload
sudo systemctl enable --now "rgb-matrix-network@$USER.service"
sudo systemctl enable --now "rgb-matrix-recovery-button@$USER.service"
sudo systemctl restart "rgb-matrix-web@$USER.service"

systemctl status "rgb-matrix-network@$USER.service"
systemctl status "rgb-matrix-recovery-button@$USER.service"
journalctl -u "rgb-matrix-network@$USER.service" -f
nmcli -f NAME,UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show
```

Query public status through the web service:

```bash
curl http://PI_ADDRESS:8080/api/v1/network/status
```

Authenticated network administration uses the existing API bearer token. Setup-mode
scan/provision also accepts the per-device setup passphrase. The accepted endpoints
are `GET /api/v1/network/status`, `GET /api/v1/network/scan`,
`GET /api/v1/network/profiles`, `GET /api/v1/network/provision/JOB_ID`, and POST
operations `provision`, `force-ap`, `retry-known`, and `forget` under `/api/v1/network/`.
Disruptive requests require an explicit boolean confirmation in their JSON body.

## Attended hardware acceptance

Never force AP mode over the only remote connection unless physical access or a
tested timed rollback path is available. Before declaring hardware acceptance:

1. Confirm the button service is active and `gpioinfo gpiochip0` reports line 19 as
   claimed by `gpiomon`.
2. Hold the wired button for five seconds and release. Join the `RGB-Matrix-*` SSID,
   then open `http://192.168.50.1:8080/setup`.
3. Submit a wrong WPA password. Confirm the candidate disappears, the setup AP
   returns, and every older saved profile remains.
4. Repeat against a test network that associates but withholds DHCP. Confirm the same
   rollback behavior.
5. Provision a working network, then reboot in LAN mode. Make all saved networks
   unreachable and reboot again; verify AP readiness after approximately 60 seconds.
6. Restore LAN, then remove it without rebooting; verify AP readiness after
   approximately 120 seconds. Repeat one power interruption during provisioning and
   confirm the next boot reaches either a saved LAN or setup AP.

Unit tests simulate each state transition, including restart reconciliation and DHCP
failure, but cannot prove RF association, NetworkManager shared DHCP/DNS, header
wiring, or behavior during physical power loss.

Captive-portal probe paths redirect when a phone already reaches the Pi, but captive
DNS interception is not implemented. The fixed setup URL is authoritative.
