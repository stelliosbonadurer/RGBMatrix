# Network Fallback and Wi-Fi Provisioning Operations

Status: implemented for Raspberry Pi OS Bookworm with NetworkManager; deployed to the
current Pi on 2026-10-18. The inactive AP profile and LAN-safe service path are
verified. A real fallback transition is intentionally not triggered over the only SSH
connection because it would disconnect the operator.

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
shell. NetworkManager stores working credentials. Provisioning creates a temporary
candidate, attempts it for 45 seconds, and promotes it only after a local IPv4 address
exists. Failure deletes only that candidate and restores the setup AP. The active or
last saved profile cannot be forgotten.

Passwords are suppressed from exceptions, jobs, and HTTP responses. `nmcli` receives
them as an argv element, which can briefly be visible to a privileged same-host
process; moving credential writes to NetworkManager's D-Bus secret-agent API is a
future hardening improvement.

## Install and inspect

```bash
sudo install -m 0644 deploy/systemd/rgb-matrix-network@.service \
  /etc/systemd/system/rgb-matrix-network@.service
sudo install -m 0644 deploy/systemd/rgb-matrix-web@.service \
  /etc/systemd/system/rgb-matrix-web@.service
sudo systemctl daemon-reload
sudo systemctl enable --now "rgb-matrix-network@$USER.service"
sudo systemctl restart "rgb-matrix-web@$USER.service"

systemctl status "rgb-matrix-network@$USER.service"
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

## Recovery and validation

Never force AP mode over the only remote connection unless physical access or a
tested timed rollback path is available. A complete hardware acceptance run still
needs to verify: unreachable saved networks, wrong WPA password, successful
provisioning, reboot in both modes, DHCP failure, and sustained connection loss.

Captive-portal probe paths redirect when a phone already reaches the Pi, but captive
DNS interception is not implemented. The fixed setup URL is authoritative. There is
also no physical reset button yet; preserve console/SD-card access during rollout.
