# FFT refactor testing record — 2026-11-17

> **Status: superseded qualification record for a failed refactor.** These results
> show that contracts, APIs, deployment, and a throttled 30 FPS profile worked; they
> do not establish that the architecture met the product's performance goal. The
> November 18 A/B comparison found the refactor substantially slower and less
> efficient than legacy, and the implementation was rolled back to `f23a34a`.

## Purpose

This record separates the FFT refactor's verification evidence from its architecture
summary. Testing covered contract validation, atomic graph replacement, analyzer
sharing and scheduling, projection, composition, hardware output, controller APIs,
durable boot behavior, live editing, and sustained operation on the target Pi.

This record is intentionally retained because it demonstrates an important failure
mode in the verification strategy: a broad passing test suite, isolated transform
benchmarks, and a sustainable reduced-rate profile can all coexist with a major
end-to-end regression.

## What this testing failed to prove

- The hardware-independent benchmark isolated FFT/renderer components and omitted
  the complete callback-to-panel pipeline, GIL contention, representation changes,
  Pillow/buffer copies, and blocking native output interaction.
- Qualifying the refactor at 30 FPS with a 2048-sample hop changed the workload and
  relaxed both content and analyzer deadlines. It proved a fallback could run, not
  that the replacement matched the 512/512 legacy behavior.
- The suite verified correctness invariants but did not enforce legacy-relative FPS,
  CPU, RSS, or render-to-swap gates.
- The live run did not test whether increasing renderer demand starved the analyzer
  or audio callback. The November 18 no-wait run exposed exactly that coupling.
- Bulk `SetImage` activation was treated as evidence of an optimized output path,
  but the full compose/copy/upload path was not compared against legacy end to end.

Future refactors should require an early, same-hardware A/B test against legacy before
building the complete editor, persistence, compositor, and telemetry surface.

## Target environment

The final hardware tests used:

- Raspberry Pi host `stelliosbonadurer@10.8.184.98`;
- Debian 12 Bookworm, AArch64, kernel 6.12.25;
- Python 3.11 from the deployed FFT virtual environment;
- NumPy 1.24.2;
- sounddevice 0.5.4;
- Pillow 9.4.0;
- the installed native `rgbmatrix` binding;
- an iMM-6C USB capture device;
- one 64×64 HUB75 panel;
- `adafruit-hat` GPIO mapping; and
- GPIO slowdown 2.

The managed process ran through
`rgb-matrix-web@stelliosbonadurer.service` using the same interpreter, matrix flags,
privilege drop, controller environment, audio input, and panel used in normal service.

## Automated suite

The complete deployed suite was run from the Pi checkout with bytecode writes
disabled:

```bash
cd /home/stelliosbonadurer/rgb_Matrix
PYTHONDONTWRITEBYTECODE=1 \
  /home/stelliosbonadurer/rpi-rgb-led-matrix/bindings/python/samples/fftdemo/.venv/bin/python3 \
  -B -m unittest discover -s tests -p "test_*.py"
```

Result:

```text
Ran 214 tests in 9.785s
OK
```

The suite included the new music model, audio runtime, projection, runtime graph,
compositor, benchmark, library, supervisor, web-service, and entry-point ordering
tests. The final privilege-drop fixes also received a focused local run of 27 tests,
plus compile checks for `main.py`, `refactored_main.py`, and the entry-point regression
test.

## Hardware-independent benchmark

`my_programs/fft_program/benchmark_refactor.py` exercised 13 render/output scenarios
and one through four distinct analyzer transforms on the Pi. Analyzer p99 results
were:

| Distinct analyzers | p99 processing time | Hop deadline utilization |
|---:|---:|---:|
| 1 | 0.213 ms | 8.00% |
| 2 | 0.344 ms | 12.89% |
| 3 | 0.703 ms | 26.37% |
| 4 | 1.420 ms | 53.24% |

All analyzer cases remained below the 80% deadline gate. The report is stored on the
Pi at:

```text
/home/stelliosbonadurer/fft-refactor-benchmark-20261117T2120Z.json
```

This benchmark isolates transform and renderer costs. It does not include the native
panel's blocking VSync path, so live telemetry was also required.

## Deployment and startup testing

The first managed launch found an import-order defect that desktop tests could not
reproduce: `RGBMatrix` dropped privileges before `refactored_main.py` was imported.
The deployed child could no longer traverse the home directory and exited with
`ModuleNotFoundError`. Moving the import before matrix initialization resolved the
failure.

The next restart found the same issue for the mode-0600 boot snapshot. The process
ran, but reported the migrated 200 FPS configuration instead of the selected durable
profile. Preloading the bounded JSON document before matrix initialization resolved
that fallback. A subsequent clean restart reported both the expected profile name
and the saved 30 FPS configuration.

These behaviors are now protected by `tests/test_fft_entrypoint.py`, which verifies
that the refactored import and boot-payload preload both precede
`initialize_matrix()`.

## Frame-rate qualification

Testing distinguished configured content rate from achieved content throughput and
from the panel's independent native PWM refresh:

| Configuration | Result |
|---|---|
| Nominal 200 FPS | Actual throughput was roughly 40–50 FPS. The loop saturated, dropped frames, starved control requests, and missed analyzer hops. |
| Nominal 60 FPS | Still above the measured panel path. Render p99 reached about 28 ms, exceeding the 16.67 ms frame budget. |
| Nominal 30 FPS, 512-sample hop | Rendering was sustainable, but the 11.61 ms analyzer deadline was shorter than the blocking native output path and still produced missed hops. |
| Nominal 30 FPS, 2048-sample window/hop | Passed. The 33.33 ms content budget and 46.44 ms analyzer deadline fit the measured hardware behavior. |

The 2048-sample window/hop configuration was saved back into the durable boot profile,
then the FFT was restarted so qualification counters began from zero.

## Clean-start check

The first clean check of run 7 reported:

- 201 analyzer runs;
- 281 rendered frames;
- zero missed analyzer hops;
- zero render drops;
- zero audio overruns;
- zero callback failures; and
- zero transform failures.

This also verified that the process loaded `Pi 64x64 Qualified 30 FPS` from the
controller-owned boot snapshot rather than regenerating the legacy migration.

## Sustained hardware run

After 91 seconds on the physical panel and USB capture device, live telemetry showed:

| Measurement | Result |
|---|---:|
| Analyzer runs | 1,960 |
| Analyzer missed hops | 0 |
| Analyzer p99 | 16.891 ms |
| Analyzer deadline | 46.440 ms |
| Rendered frames | 2,731 |
| Render drops | 1 |
| Render p99 | 19.988 ms |
| Content-frame budget | 33.333 ms |
| Audio overruns | 0 |
| Audio underflows | 0 |
| Audio callback failures | 0 |
| Transform failures | 0 |
| Duplicate analyzer executions | 0 |
| Python child CPU | 99.8% |
| Python child RSS | 68,500 KiB |
| Pi temperature | 60.8 °C |
| Throttle state | `0x0` |

The high process CPU includes the native matrix refresh work. It did not produce
thermal throttling or loss of the qualified audio/render deadlines. The final audit
measured 59.9 °C and still reported `throttled=0x0`.

## Atomic live-edit test

One complete live configuration was applied with brightness 0.9 and then another was
applied to restore brightness 1.0. Both operations used the current run ID and
expected revision.

Results:

- revisions advanced from 0 to 1 to 2;
- both candidates were accepted;
- zero candidates were rejected;
- the run ID remained 7;
- the supervisor PID remained 14471;
- the visualization stayed in the running state; and
- final live brightness returned to 1.0.

This demonstrated that validation, graph compilation, activation, response delivery,
and reversal worked without restarting the child process.

## Final operational audit

The final audit verified:

- the systemd controller service was active;
- `/healthz` returned `{"status":"ok"}`;
- the controller page, `/config/fft`, and `fft-config.js` were served;
- the runtime-control connection was active;
- the durable snapshot contained 30 FPS, brightness 1.0, and a 2048-sample
  window/hop;
- runtime counters still showed zero missed hops, audio overruns, callback failures,
  and transform failures;
- only one render drop had accumulated during the run; and
- the journal contained no post-launch traceback, runtime error, rejected boot
  configuration, or unexpected child exit.

## Qualification limits discovered afterward

This session did not perform the longer 30-minute, four-analyzer/eight-layer stress
run described in `my_programs/fft_program/docs/refactor_qualification.md`. It also did
not capture a calibrated audio-to-photon latency measurement or an automated camera
assessment of visible flicker. Those would have increased coverage, but they are not
the immediate follow-up for this discarded implementation. The more important
missing gate was the standard one-analyzer/one-layer legacy-relative comparison that
failed on November 18.
