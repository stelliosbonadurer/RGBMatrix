# Connecting to the RGB Matrix Raspberry Pi

The Pi normally connects to a saved Wi-Fi network and runs its control service on
port 8080. Use the hostname first because normal Wi-Fi addresses come from DHCP and
can change.

## Quick connection

When your computer or phone is on the same network as the Pi:

```bash
ssh stelliosbonadurer@raspberrypi.local
```

Open the service in a browser:

```text
http://raspberrypi.local:8080
```

The control page lists every managed visualization, shows what is running, and can
launch, switch, configure, or quit a visualization after pairing. Each visualization
form includes its application settings and a collapsible matrix-hardware section.
Wi-Fi provisioning remains available at `/setup`.

## Addresses by network

| Network or mode | Pi address | Status | How to connect |
| --- | --- | --- | --- |
| `SpectrumSetup-5B` | `192.168.1.204` | Last verified address; assigned by DHCP and may change | `ssh stelliosbonadurer@raspberrypi.local` or use the IP |
| `SPhone` hotspot | Not fixed | Saved as a lower-priority known network; the hotspot assigns an address when connected | Use `raspberrypi.local`, or inspect the hotspot's connected-device list |
| Fallback setup AP, `RGB-Matrix-BF6E` | `192.168.50.1` | Fixed while the Pi is broadcasting its own Wi-Fi | Join the Pi's SSID, then open `http://192.168.50.1:8080/setup` |

Addresses such as `192.168.1.16` and `192.168.1.29` appear in old project notes.
They are historical and should not be assumed to be current.

The Pi's Wi-Fi MAC address is `2c:cf:67:50:bf:6e`. This is useful when finding the
device in a router client list or creating a DHCP reservation. A router reservation
is the simplest way to keep the normal LAN address stable.

## Finding the Pi when its DHCP address changes

Try the hostname before looking for an address:

```bash
ping -c 1 raspberrypi.local
ssh stelliosbonadurer@raspberrypi.local
```

On macOS, resolve the current IPv4 address with:

```bash
dns-sd -G v4 raspberrypi.local
```

Press Ctrl+C after an address appears. You can also look in the router or hotspot's
connected-device list for hostname `raspberrypi` or MAC address
`2c:cf:67:50:bf:6e`.

After connecting over SSH, confirm the Pi's current addresses with:

```bash
hostname -I
nmcli -t -f GENERAL.CONNECTION,IP4.ADDRESS device show wlan0
```

## Web and service checks

Check that the web service is reachable:

```bash
curl http://raspberrypi.local:8080/healthz
curl http://raspberrypi.local:8080/api/v1/network/status
curl http://raspberrypi.local:8080/api/v1/visualizations
```

On the Pi, inspect the services with:

```bash
systemctl status "rgb-matrix-web@$USER.service"
systemctl status "rgb-matrix-network@$USER.service"
systemctl status "rgb-matrix-recovery-button@$USER.service"
```

## Pairing a phone for controls

At every Pi boot, the controller creates a six-digit pairing code and shows three
unlabeled lines together on the matrix: the Wi-Fi SSID, the controller IP and port
without an `http://` prefix, and the code. A line scrolls horizontally only when it
is too wide for the panel.
The phone does not need the permanent API token. If the display is hard to read,
print the same code from a terminal:

```bash
ssh stelliosbonadurer@raspberrypi.local \
  "cd ~/rgb_Matrix && python3 -m matrix_controller.boot_code"
```

1. Open `http://raspberrypi.local:8080` or the Pi's current address.
2. Enter the six digits under **Six-digit phone code**.
3. Tap **Pair phone**.

During current UI development, `000000` is also always accepted. This deliberately
weakens pairing to trusted-LAN access and must be removed before the controller is
used on an untrusted network or exposed outside the LAN. The rotating matrix code
continues to work normally.

To start or switch most visualizations, select **Configure** on a card, adjust the
generated settings, and choose **Launch visualization**. Music FFT has separate
**Configure** and **Launch settings** buttons. **Configure** opens the dedicated music
page at `/config/fft`; **Launch settings** exposes matrix and startup parameters on the
main page. The current visualization is stopped before its replacement starts.

The dedicated music page can start or stop Music FFT and reads current state directly
from the visualization process. It can change themes, display modes, zoom, layers,
layer order/visibility/boost, dynamic-theme behavior, and bounded audio or scaling
parameters without restarting. Keyboard changes made over SSH appear on the next
phone poll. Controls marked `rebuild` refresh derived FFT bins in-process; matrix
hardware and audio-device geometry still require a new launch.

The code remains the same for the current Pi boot and can pair multiple local devices;
a full reboot rotates it. The resulting phone session can control visualizations but
cannot change Wi-Fi. It expires after four hours, when the web-service process
restarts, or when **Forget access** is selected. The controller stores it as an
HttpOnly browser cookie, so it survives navigation between the visualization catalog
and music controls without exposing the credential to JavaScript. Cookies are scoped
to the address used while pairing: `raspberrypi.local` and `192.168.1.204` count as
different hosts, so consistently use one address.

Pairing is a trusted-LAN convenience feature: the cookie is HttpOnly, host-only, and
SameSite=Strict, and cookie-authenticated changes require the same HTTP origin. The
service still uses plain HTTP, so do not use it on an untrusted network or expose port
8080 to the internet.

## Changing launch defaults

The versioned administrator config is:

```text
~/rgb_Matrix/config/matrix-controller.json
```

It controls the visualization interpreter, sudo policy, stop timeout, boot
visualization, shared matrix defaults, and FFT/sorting defaults. Matrix fields cover
the fixed native launch flags, including dimensions, chain/parallel counts, PWM,
brightness, GPIO mapping and slowdown, scan mode, color order, pixel mapping, row
addressing, multiplexing, panel type, and privilege dropping.

Edit the JSON over SSH, validate it, then restart the web service:

```bash
cd ~/rgb_Matrix
python3 -c 'from pathlib import Path; from matrix_controller.controller_config import load_controller_config; load_controller_config(Path("config/matrix-controller.json")); print("config ok")'
sudo systemctl restart "rgb-matrix-web@$USER.service"
```

The service refuses invalid JSON, unknown fields, unsupported versions, and values
outside the shared parameter contract. This file contains defaults, not arbitrary
shell commands or script paths. A web form can override its exposed values for one
launch; it does not rewrite the file.

## Starting a visualization through the API

Direct state-changing API calls require the permanent token stored on the Pi. Keep it
private. This example reads it into a temporary shell variable without printing it:

```bash
RGB_MATRIX_TOKEN="$(ssh stelliosbonadurer@raspberrypi.local \
  'cat ~/.config/rgb-matrix-controller/api-token')"
```

Start the music visualizer:

```bash
curl -X POST http://raspberrypi.local:8080/api/v1/run \
  -H "Authorization: Bearer $RGB_MATRIX_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"visualization_id":"fft","parameters":{"theme":"ocean"}}'
```

Start the sorting visualizer:

```bash
curl -X POST http://raspberrypi.local:8080/api/v1/run \
  -H "Authorization: Bearer $RGB_MATRIX_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"visualization_id":"sorts","parameters":{"mode":"random"}}'
```

Stop the current visualization:

```bash
curl -X POST http://raspberrypi.local:8080/api/v1/stop \
  -H "Authorization: Bearer $RGB_MATRIX_TOKEN"
```

Clear the temporary token when finished:

```bash
unset RGB_MATRIX_TOKEN
```

## Using fallback setup Wi-Fi

If no saved network connects during boot, the Pi waits approximately 60 seconds and
then broadcasts `RGB-Matrix-BF6E`. After losing a previously working LAN connection,
it waits approximately 120 seconds before entering fallback mode.

1. Join `RGB-Matrix-BF6E` from the phone or laptop.
2. Open `http://192.168.50.1:8080/setup` exactly. Automatic captive-portal discovery
   is not guaranteed.
3. Select or enter the desired Wi-Fi network and submit its credentials.
4. Rejoin that normal Wi-Fi network after the Pi connects to it.
5. Open `http://raspberrypi.local:8080` or rediscover the DHCP address.

The fallback Wi-Fi password is device-specific and intentionally not stored in this
document. A local operator can read it on the Pi with:

```bash
sudo cat /var/lib/rgb-matrix-network/setup-passphrase
```

If the recovery button is wired, hold it for at least five seconds and release it to
force fallback mode. The button connects BCM GPIO 19 (physical pin 35) to ground
(physical pin 39). Activating it disconnects the Pi from its current Wi-Fi network.

## Troubleshooting

- `raspberrypi.local` works only when the client network supports mDNS. Use the router
  client list or the last known IP if it does not resolve.
- The laptop or phone must be on the same LAN as the Pi, or joined directly to the
  fallback `RGB-Matrix-BF6E` network.
- Port 8080 must stay on a trusted LAN. Do not expose it directly to the internet.
- A 503 response immediately after restarting the network helper can be transient;
  retry after a few seconds.
- The FFT visualization still requires the configured audio input device to be
  attached and recognized by the Pi.
