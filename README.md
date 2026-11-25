# RGB Matrix

A Raspberry Pi that drives a 64×64 RGB LED panel, controlled from your phone. Its main
program is a real-time music visualizer that listens through a USB microphone or to a
phone streaming over Bluetooth. There are also teaching visualizations for sorting
algorithms, graph traversal, and the Master Theorem.

Open the Pi's web page on your phone, pick a program, adjust it, and send it to the
panel. You don't need a keyboard, SSH, or a monitor.

## Features

- **Music FFT visualizer.** One audio capture and one FFT per audio block, drawn as up
  to eight stacked bar layers. Each layer has its own frequency range, weighting curve,
  noise floor, and color theme.
- **Phone control page.** Each program has its own page with edit-then-submit
  controls, a shared matrix settings panel (brightness, GPIO mapping, and so on), and
  a "now playing" view.
- **FFT layer editor.** For each layer, the page shows a bin graph with draggable
  weighting points. It highlights bars too narrow to hold an FFT bin, which would stay
  dark.
- **Presets stored on the Pi.** Save a layer set under a name, load it from any phone,
  or copy a single layer out of a preset.
- **Bluetooth audio.** Pair a phone from the FFT page and the visualizer follows the
  music it plays. The Pi only listens to the stream; it doesn't play it back as a
  speaker.
- **Wi-Fi setup and fallback.** If the Pi can't reach a known network, it opens its own
  setup Wi-Fi (`RGB-Matrix-XXXX`) with a provisioning page. Holding a physical button
  for five seconds brings up the setup Wi-Fi on demand.
- **Boot display.** At startup the panel shows the network name, the controller
  address, and a pairing code.

## Hardware

| Part | Used here |
| --- | --- |
| Computer | Raspberry Pi 4 |
| Panel | 64×64 HUB75 RGB LED matrix |
| Interface | [Adafruit RGB Matrix Bonnet](https://learn.adafruit.com/adafruit-rgb-matrix-bonnet-for-raspberry-pi) (`adafruit-hat-pwm` mapping, with the PWM jumper) |
| Audio | A USB microphone, or a phone over Bluetooth |
| Recovery button (optional) | Momentary button between GPIO 19 (pin 35) and ground (pin 39) |

Other panel sizes and HATs that
[`rpi-rgb-led-matrix`](https://github.com/hzeller/rpi-rgb-led-matrix) supports should
work after you change the `matrix` block in
[`config/matrix-controller.json`](config/matrix-controller.json), but only the setup
above has been tested.

## How it fits together

```text
 phone browser ──HTTP :8080──▶ matrix_controller (web service, no hardware access)
                                  │  launches exactly one allowlisted program at a time
                                  ▼
                               program child (sudo) ──▶ rpi-rgb-led-matrix ──▶ panel
                                  ▲
                 USB mic / Bluetooth loopback ──▶ FFT capture

 matrix_network (root helper) ── NetworkManager: known Wi-Fi, Ethernet, setup AP
```

- [`matrix_controller/`](matrix_controller/) is a standard-library HTTP service with
  the phone pages. It runs a single allowlisted program at a time and passes FFT
  layer changes to the running program over a private per-run Unix socket. The
  program applies each change between frames.
- [`matrix_network/`](matrix_network/) is a separate root-owned helper for Wi-Fi,
  Ethernet, and the setup access point. The web service reaches it over a local
  socket.
- [`my_programs/fft_program/`](my_programs/fft_program/) is the modular music
  visualizer: capture, a shared FFT, per-layer bar mappings, themes, and two output
  backends.
- [`my_programs/dsa_visualizations/`](my_programs/dsa_visualizations/) holds the
  graph-traversal and Master Theorem visualizations.
  [`my_programs/visualize_sorts.py`](my_programs/visualize_sorts.py) holds the
  sorting animations.

## Programs

| Program | ID | What it shows |
| --- | --- | --- |
| Connection info | `system_info` | Wi-Fi name, controller address, and pairing code. Shown at boot. |
| Music FFT | `fft` | Layered spectrum bars from the mic or a Bluetooth phone |
| Sorting algorithms | `sorts` | Animated sorts, one at a time or in a random loop |
| Graph traversal | `graph_traversal` | BFS, DFS, Dijkstra, components, and Prim/Kruskal on a seeded planar graph |
| Master Theorem | `master_theorem` | The recurrence tree for `T(n) = aT(n/b) + n^k` |

## Setting up a Pi

The Pi needs some one-time setup by hand; there's no installer for the whole thing
yet. This is the path used for the tested Pi.

1. **Build the matrix library.** Clone and build
   [`rpi-rgb-led-matrix`](https://github.com/hzeller/rpi-rgb-led-matrix) and its
   Python binding, following its README. Install the binding into a virtual
   environment, then install the FFT dependencies into that same environment:

   ```bash
   pip install -r my_programs/fft_program/requirements.txt   # numpy, sounddevice, Pillow
   ```

2. **Clone this repository** to `~/rgb_Matrix`.

3. **Point the config at your interpreter.** In
   [`config/matrix-controller.json`](config/matrix-controller.json), set
   `runtime.python_executable` to the virtual environment's `python3`, and adjust the
   `matrix` block if your panel or HAT differs.

4. **Allow non-interactive sudo.** Programs run under `sudo` so the matrix library
   can reach the GPIO, so `sudo -n true` must succeed for the service account. The
   web service itself never runs as root.

5. **Create an API token.** It guards network administration:

   ```bash
   mkdir -p ~/.config/rgb-matrix-controller
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))' \
     > ~/.config/rgb-matrix-controller/api-token
   chmod 600 ~/.config/rgb-matrix-controller/api-token
   ```

6. **Install the services.** Review the paths in
   [`deploy/systemd/`](deploy/systemd/) first, then:

   ```bash
   sudo install -m 0644 deploy/systemd/rgb-matrix-web@.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now "rgb-matrix-web@$USER.service"
   ```

   Install the network helper and recovery button units from the same folder the same
   way, if you want the Wi-Fi fallback and the button.

7. **Optional: Bluetooth audio.** Run `deploy/bluetooth-audio/install.sh` as the
   desktop (autologin) user. See
   [`my_programs/fft_program/docs/bluetooth_audio.md`](my_programs/fft_program/docs/bluetooth_audio.md)
   for what it installs and how to pair a phone.

Then open `http://raspberrypi.local:8080` from a phone on the same network.

## Using it

- **Home page:** shows what's running, the list of programs, and the shared matrix
  settings.
- **A program page** (`/programs/<id>`): edit the settings and press **Send update**.
  Nothing reaches the panel until you submit.
- **FFT page:** add, reorder, hide, or edit layers. Drag the points on a layer's graph
  to shape its weighting. Save or load presets. Under **Analyzer**, pair a Bluetooth
  phone and switch between the mic and Bluetooth.
- **No known network:** join the `RGB-Matrix-XXXX` Wi-Fi and open
  `http://192.168.50.1:8080/setup`. Its password is in
  `/var/lib/rgb-matrix-network/setup-passphrase` on the Pi.

To run something by hand on the Pi, use the launcher. It applies the matrix flags from
the config:

```bash
python3 launch_rgbmatrix.py --list
python3 launch_rgbmatrix.py --program my_programs.fft_program.main
```

> **Security note.** Visualization control is currently open to anyone who can reach
> port 8080. Traffic is plain HTTP, and only network administration needs the API
> token. Use the Pi on a network you trust, and never forward port 8080 from your
> router.

## Development

All tests run without hardware, so you can run them on a laptop. The test environment
uses the Pi's versions: Python 3.11, NumPy 1.24.2, and Pillow 9.4.0.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements-test.txt
.venv/bin/python -m unittest discover -s tests
```

The page JavaScript tests need Node 22 or newer and are skipped without it. The tests
fake the audio, subprocesses, and the matrix, so passing them says nothing about
GPIO timing, the panel, or real audio. Check those on the Pi.

The web service also runs locally on loopback, without a token or hardware:

```bash
python3 -m matrix_controller.web_service --config config/matrix-controller.json
```

Its pages load, but launching a program fails without the Pi's native libraries.

[`tools/pi/pi_cycle.py`](tools/pi/pi_cycle.py) deploys a commit to the Pi and checks
it from the laptop over SSH (`status`, `deploy`, `check`, `restore`).
[`tools/fft_layers.py`](tools/fft_layers.py) sends layer structures to a running
FFT.

## Repository layout

| Path | Contents |
| --- | --- |
| `matrix_controller/` | Web service, supervisor, pairing, presets, Bluetooth, phone pages (`static/`) |
| `matrix_network/` | Root network helper and recovery-button watcher |
| `my_programs/fft_program/` | The music visualizer and its docs (`docs/`) |
| `my_programs/dsa_visualizations/` | Graph traversal and Master Theorem |
| `my_programs/` (other files) | Sorting and older standalone experiments |
| `config/` | Launch defaults and matrix settings |
| `deploy/` | systemd units, the Bluetooth audio bridge, onboard-audio settings |
| `tools/` | Pi deploy/check cycle and the FFT layer tool |
| `tests/` | Hardware-free Python and page JavaScript tests |
| `rpi-rgb-led-matrix/` | A local checkout of the matrix library, ignored by git |
