# Web Service MVP Operations

Status: visualization control deployed and tested on 2026-10-16; network setup proxy
and minimal mobile setup page added on 2026-10-18.

## Current Pi deployment

- Hostname: `raspberrypi`
- Last known address: `192.168.1.204` on `SpectrumSetup-5B` (DHCP; rediscover if it changes)
- Application path: `/home/stelliosbonadurer/rgb_Matrix`
- Unit: `rgb-matrix-web@stelliosbonadurer.service`
- Listener: `0.0.0.0:8080`
- API token: `/home/stelliosbonadurer/.config/rgb-matrix-controller/api-token`
- Mutable state: `/var/lib/rgb-matrix-controller`
- Visualization interpreter:
  `/home/stelliosbonadurer/rpi-rgb-led-matrix/bindings/python/samples/fftdemo/.venv/bin/python3`

Verified on the Pi:

- all 34 hardware-free tests pass under `/usr/bin/python3`;
- the unit is both `enabled` and `active`;
- `/healthz`, visualization listing, and status return successfully;
- an authenticated finite `sorts`/`quick` launch reached the real native matrix path,
  exited with code 0, cleared the display, and returned the supervisor to `idle`.

The FFT child was not launched during deployment validation because the configured
`iMM-6C` input was not present in the earlier audio-device check. The existing FFT
latency remains accepted; this is an audio-availability caveat, not a performance gate.

## What is implemented

The visualization surface remains an API. A minimal phone page now exists only for
Wi-Fi provisioning at `/setup`. The service provides:

- an explicit allowlist containing `fft` and `sorts`;
- typed parameter metadata and validation;
- list, status, run/switch, and stop endpoints;
- one supervised visualization process at a time;
- process-group SIGTERM with timeout/SIGKILL escalation;
- bearer-token protection for state-changing requests; and
- a systemd unit template for boot startup.

Live FFT parameter changes, presets, polished visualization controls, mDNS,
portable installation, and container work remain deferred. Fallback Wi-Fi is a
separate privileged service documented in
[network-fallback-operations.md](network-fallback-operations.md). For this MVP, changing a
parameter means calling `run` again; the supervisor stops the active process fully
before launching the replacement.

## Code map

- `matrix_controller/schema.py`: immutable typed parameter and visualization contracts.
- `matrix_controller/definitions.py`: shared matrix, FFT, and sorting definitions.
- `matrix_controller/registry.py`: explicit product allowlist; no hardware imports.
- `matrix_controller/supervisor.py`: exclusive subprocess ownership/state machine.
- `matrix_controller/web_service.py`: standard-library HTTP and authentication layer.
- `matrix_network/`: known-network policy, NetworkManager adapter, and privileged
  Unix-socket service.
- `matrix_controller/static/setup.html`: minimal phone Wi-Fi setup flow.
- `deploy/systemd/rgb-matrix-web@.service`: boot service template.
- `deploy/systemd/rgb-matrix-network@.service`: root network-helper template.
- `tests/`: registry, supervisor, and loopback HTTP tests using fake processes.

The web service never calls `launch_rgbmatrix.py`, accepts a script path, or uses a
shell. The interactive launcher remains useful for a developer, while the public API
can execute only commands assembled from the reviewed registry.

## API

Read-only endpoints:

```text
GET /healthz
GET /api/v1/visualizations
GET /api/v1/status
```

State-changing endpoints require `Authorization: Bearer TOKEN`:

```text
POST /api/v1/run
POST /api/v1/stop
```

Example run body:

```json
{
  "visualization_id": "fft",
  "parameters": {
    "theme": "ocean",
    "gradient": true,
    "led_brightness": 70
  }
}
```

Omitted values receive manifest defaults. Unknown names, wrong JSON types, values
outside ranges, and unknown visualization IDs are rejected. Starting a new ID or
calling `run` with new values is the switch operation.

## Local development

Run on loopback without a token or hardware launch:

```bash
python3 -m matrix_controller.web_service
```

Inspect definitions and status:

```bash
curl http://127.0.0.1:8080/api/v1/visualizations
curl http://127.0.0.1:8080/api/v1/status
```

Do not call `run` on a laptop unless intentionally testing a child startup failure;
the registered programs require Pi hardware/native dependencies.

Run hardware-free tests:

```bash
python3 -m unittest discover -s tests -v
```

The HTTP tests bind an ephemeral loopback port. All subprocess and matrix behavior in
the tests is fake; passing them does not validate GPIO, panel timing, audio, sudo, or
the native binding.

## Pi foreground smoke test

The current known Pi environment uses the virtual-environment interpreter below.
From a checkout at `~/rgb_Matrix`:

```bash
mkdir -p ~/.config/rgb-matrix-controller
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' \
  > ~/.config/rgb-matrix-controller/api-token
chmod 600 ~/.config/rgb-matrix-controller/api-token

RGBMATRIX_STATE_DIR="$HOME/.local/state/rgb-matrix-controller" \
python3 -m matrix_controller.web_service \
  --host 0.0.0.0 \
  --port 8080 \
  --token-file "$HOME/.config/rgb-matrix-controller/api-token" \
  --python "$HOME/rpi-rgb-led-matrix/bindings/python/samples/fftdemo/.venv/bin/python3" \
  --sudo
```

Before using `--sudo`, verify `sudo -n true` succeeds for the service account. The
MVP intentionally does not install or broaden sudoers policy. If non-interactive sudo
is unavailable, stop and decide the hardware privilege boundary rather than running
the HTTP server itself as root.

From another device, read the token locally from the Pi and use it as follows:

```bash
curl http://PI_ADDRESS:8080/api/v1/visualizations

curl -X POST http://PI_ADDRESS:8080/api/v1/run \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"visualization_id":"fft","parameters":{"theme":"ocean"}}'

curl -X POST http://PI_ADDRESS:8080/api/v1/stop \
  -H "Authorization: Bearer TOKEN"
```

The FFT registry passes `--non-interactive`, so it does not touch terminal state.
Sorting defaults to continuous random mode under the service. Use `mode=single` and a
named `sort` for a finite run.

## Enable at boot

The checked-in unit assumes:

- repository: `/home/USER/rgb_Matrix`;
- control service interpreter: `/usr/bin/python3`;
- visualization interpreter:
  `/home/USER/rpi-rgb-led-matrix/bindings/python/samples/fftdemo/.venv/bin/python3`;
- API token: `/home/USER/.config/rgb-matrix-controller/api-token`; and
- passwordless non-interactive sudo already works for `USER`.

Review and adjust the unit if any path differs, then install it:

```bash
sudo install -m 0644 deploy/systemd/rgb-matrix-web@.service \
  /etc/systemd/system/rgb-matrix-web@.service
sudo systemctl daemon-reload
sudo systemctl enable --now "rgb-matrix-web@$USER.service"
systemctl status "rgb-matrix-web@$USER.service"
journalctl -u "rgb-matrix-web@$USER.service" -f
```

The unit uses `network.target`, not `network-online.target`: control and visualization
startup must not wait indefinitely for internet connectivity. `KillMode=control-group`
is a second line of defense if the service exits before normal child teardown.

## Operational cautions

- Binding beyond loopback is refused unless a token is configured.
- HTTP is currently unencrypted. Use only on a trusted local network; do not forward
  port 8080 from a router. A later pairing/UI phase should improve credential UX.
- The token protects POST actions but can be observed by anyone able to sniff plain
  HTTP on the LAN. TLS or another secure local pairing transport is future work.
- The API reports exit codes but intentionally does not buffer arbitrary child output.
  Inspect journald for application logs.
- A successful one-shot sorting run naturally returns the supervisor to `idle`.
- The FFT audio device still defaults to `iMM-6C`; service startup does not make a
  missing microphone available.
- The supplied unit is a deployment template, not a portable installer. That work is
  deliberately deferred.
