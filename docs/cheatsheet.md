# RGB Matrix Operations Cheatsheet

These commands assume the Pi user is `stelliosbonadurer`, the checkout is
`/home/stelliosbonadurer/rgb_Matrix`, and the controller listens on port `8080`.

## Connect to the Pi

From the development computer:

```bash
ssh stelliosbonadurer@raspberrypi.local
```

If hostname discovery is unavailable:

```bash
ssh stelliosbonadurer@PI_IP_ADDRESS
```

## Update from GitHub

Run on the Pi:

```bash
cd /home/stelliosbonadurer/rgb_Matrix
git status --short
git pull --ff-only origin main
```

Update and restart the controller:

```bash
cd /home/stelliosbonadurer/rgb_Matrix
git pull --ff-only origin main && \
  sudo systemctl restart rgb-matrix-web@stelliosbonadurer.service
sudo systemctl status rgb-matrix-web@stelliosbonadurer.service --no-pager
```

`git pull --ff-only` deliberately refuses to merge diverging history. If it
fails, inspect `git status --short` instead of forcing the update.

## Web-controller service

Stop the controller and its supervised visualization:

```bash
sudo systemctl stop rgb-matrix-web@stelliosbonadurer.service
```

Start, restart, or inspect it:

```bash
sudo systemctl start rgb-matrix-web@stelliosbonadurer.service
sudo systemctl restart rgb-matrix-web@stelliosbonadurer.service
sudo systemctl status rgb-matrix-web@stelliosbonadurer.service --no-pager
```

Follow live logs or show the latest 100 lines:

```bash
journalctl -u rgb-matrix-web@stelliosbonadurer.service -f
journalctl -u rgb-matrix-web@stelliosbonadurer.service -n 100 --no-pager
```

Verify the local API:

```bash
curl --fail --silent http://127.0.0.1:8080/api/v1/status
```

Controller pages on the LAN:

```text
http://raspberrypi.local:8080/
http://raspberrypi.local:8080/config/fft
```

Print the current six-digit pairing code:

```bash
cd /home/stelliosbonadurer/rgb_Matrix
python3 -m matrix_controller.boot_code
```

## Network and recovery services

Check all project services:

```bash
systemctl status \
  rgb-matrix-web@stelliosbonadurer.service \
  rgb-matrix-network@stelliosbonadurer.service \
  rgb-matrix-recovery-button@stelliosbonadurer.service \
  --no-pager
```

Restart the privileged network helper and inspect its logs:

```bash
sudo systemctl restart rgb-matrix-network@stelliosbonadurer.service
journalctl -u rgb-matrix-network@stelliosbonadurer.service -f
```

Follow recovery-button logs:

```bash
journalctl -u rgb-matrix-recovery-button@stelliosbonadurer.service -f
```

Fallback setup page:

```text
http://192.168.50.1:8080/setup
```

## Check running matrix processes

```bash
pgrep -af 'matrix_controller.web_service|fft_program/main.py|visualize_sorts|system_info.py|master_theorem.py'
```

Stop the web controller before direct visualization tests so two processes do not
compete for the panel:

```bash
sudo systemctl stop rgb-matrix-web@stelliosbonadurer.service
```

Restore it after testing:

```bash
sudo systemctl start rgb-matrix-web@stelliosbonadurer.service
```

## Master Theorem visualization

```bash
cd /home/stelliosbonadurer/rgb_Matrix
```

Root-heavy quadratic work:

```bash
python3 launch_rgbmatrix.py \
  --program my_programs.dsa_visualizations.master_theorem \
  --example root_heavy \
  --cycles 1
```

Merge sort, with equal work at every level:

```bash
python3 launch_rgbmatrix.py \
  --program my_programs.dsa_visualizations.master_theorem \
  --example merge_sort \
  --cycles 1
```

Binary search, with one shrinking subproblem:

```bash
python3 launch_rgbmatrix.py \
  --program my_programs.dsa_visualizations.master_theorem \
  --example binary_search \
  --cycles 1
```

Custom `T(n) = 4T(n/2) + Theta(n)`:

```bash
python3 launch_rgbmatrix.py \
  --program my_programs.dsa_visualizations.master_theorem \
  --a 4 \
  --b 2 \
  --n 64 \
  --k 1 \
  --cycles 1
```

Omit `--cycles 1` to repeat continuously. Press `Ctrl+C` to stop.

Show the complete classified tree without animation and hold it until `Ctrl+C`:

```bash
python3 launch_rgbmatrix.py \
  --program my_programs.dsa_visualizations.master_theorem \
  --example merge_sort \
  --no-animate
```

## Launcher diagnostics

List discovered programs:

```bash
cd /home/stelliosbonadurer/rgb_Matrix
python3 launch_rgbmatrix.py --list
```

Preview a launch without initializing hardware:

```bash
python3 launch_rgbmatrix.py \
  --program my_programs.dsa_visualizations.master_theorem \
  --no-sudo \
  --dry-run \
  --example merge_sort \
  --cycles 1
```

## Validate the checkout

```bash
cd /home/stelliosbonadurer/rgb_Matrix
python3 -m compileall -q launch_rgbmatrix.py matrix_controller matrix_network tests my_programs/dsa_visualizations my_programs/fft_program my_programs/*.py
python3 -m unittest discover -s tests -q
```

These checks do not replace physical panel, audio, GPIO, or NetworkManager tests.

## Verify the Git deployment

```bash
cd /home/stelliosbonadurer/rgb_Matrix
git remote -v
git branch --show-current
git status --short
git log -1 --oneline
```

Expected fetch remote:

```text
https://github.com/stelliosbonadurer/RGBMatrix
```

## One-time conversion to a Git checkout

Use this only when `/home/stelliosbonadurer/rgb_Matrix` exists but has no `.git`
directory. It keeps the old deployment as a recoverable backup and restores only
files absent from the fresh clone.

```bash
sudo systemctl stop rgb-matrix-web@stelliosbonadurer.service
cd /home/stelliosbonadurer
mv rgb_Matrix rgb_Matrix.pre-git
git clone --branch main --single-branch \
  https://github.com/stelliosbonadurer/RGBMatrix \
  rgb_Matrix
rsync -a --ignore-existing --exclude='.git/' \
  /home/stelliosbonadurer/rgb_Matrix.pre-git/ \
  /home/stelliosbonadurer/rgb_Matrix/
cd /home/stelliosbonadurer/rgb_Matrix
git status --short
sudo systemctl start rgb-matrix-web@stelliosbonadurer.service
```

## Pre-Git deployment backup

The one-time Git conversion retained the former deployment at:

```text
/home/stelliosbonadurer/rgb_Matrix.pre-git
```

Keep it until the panel, audio, web controller, and native driver are verified. To
restore it after a failed migration:

```bash
sudo systemctl stop rgb-matrix-web@stelliosbonadurer.service
cd /home/stelliosbonadurer
mv rgb_Matrix rgb_Matrix.failed-clone
mv rgb_Matrix.pre-git rgb_Matrix
sudo systemctl start rgb-matrix-web@stelliosbonadurer.service
```
