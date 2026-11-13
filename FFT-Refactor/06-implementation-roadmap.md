# Implementation Roadmap and Acceptance Gates

No phase should begin by rewriting the application in C++. The plan first corrects
work scheduling and state boundaries, then measures rendering, then moves only a
proven hot path if needed.

## Phase 0 — Freeze contracts and baselines

Deliverables:

- Versioned schemas for audio source, FFT analyzer, analysis frame, visual layer,
  complete configuration, favorite, template, and telemetry snapshot.
- Stable UUID/name rules and limits.
- Current baseline analyzer and layer migration fixtures.
- Timing instrumentation design.
- Repeatable audio fixture and Pi benchmark procedure.
- Physical verification task for HAT/Bonnet and quality jumper.

Acceptance:

- Every current setting is either mapped to the new model or deliberately retired
  with rationale.
- Baseline Pi results can be repeated and include exact software/hardware versions.
- Current appearance has reference images/frames for bass, mid, overflow, peaks,
  gradient, and shadow modes.

## Phase 1 — Audio ring and analyzer scheduler

Deliverables:

- `AudioSource` boundary around the current USB mic.
- Preallocated timestamped PCM ring with source generation and sample positions.
- Analyzer registry and compiled-worker canonicalization.
- One immutable analysis frame per due hop.
- Missed-hop and duplicate-execution telemetry.
- Hardware-free deterministic scheduler tests.

Acceptance:

- The same `(source generation, analyzer, end sample)` is never transformed twice.
- Rendering the same analysis frame 1, 10, or 100 times does not increment FFT runs.
- Adding visual consumers does not increment FFT runs.
- Two identical analyzer cells use one compiled transform worker.
- Two different analyzers run at their own hop cadences.
- Ring overflow skips stale work in a bounded way and reports the exact missed count.
- Audio callback performs no FFT, file I/O, network I/O, or unbounded logging.

## Phase 2 — Stable visual-layer objects

Deliverables:

- Layer registry with `fft_bars` as the first type.
- Each layer object owns definition, analyzer reference, projection, temporal state,
  renderer, and coverage behavior.
- Frequency/gain curve compilation.
- Time-based smoothing and peaks.
- Add, duplicate, remove, rename, hide, and reorder operations on candidate state.
- Compatibility migration for current bass/mid layers.

Acceptance:

- No layer behavior depends on matching indexes across unrelated arrays.
- Every layer/analyzer reference is validated before activation.
- Arbitrary structural edits either apply completely or have no effect.
- Many layers may consume one analyzer frame.
- Switching a layer between compatible analyzers rebuilds only affected projection
  and temporal state according to documented rules.

## Phase 3 — Opaque coverage compositor and bulk output

Deliverables:

- Preallocated RGB frame and occupancy mask.
- Top-to-bottom first-claim compositor.
- Hidden-layer, full-coverage, and remaining-mask culling.
- FFT-bar interval/column fast path.
- Bulk `SetImage()` adapter and retained `SwapOnVSync()`.
- Fake-canvas/golden-frame tests.

Acceptance:

- Opaque output matches the equivalent bottom-to-top overwrite reference for all
  supported current effects.
- Black coverage is distinguished from absence/transparent coverage.
- A fully covering top layer causes zero pixel generation in lower layers.
- Partially covered lower bars draw only unclaimed pixels on mask-capable paths.
- User-hidden layers generate no pixels.
- Visible occluded layers maintain the specified temporal state without stale jumps.
- Bulk output improves or preserves p95/p99 frame time on the target Pi.

## Phase 4 — Atomic configuration runtime

Deliverables:

- Complete-candidate validation.
- Cross-reference and compute-budget validation.
- Staged compile/warm/swap lifecycle.
- Revisioned Apply response with normalized state and structured errors.
- Runtime telemetry endpoint.
- Rollback behavior for staging and swap failures.

Acceptance:

- Invalid second/late fields cannot leave earlier changes partially applied.
- Active output continues unchanged after a rejected candidate.
- Analyzer/layer graph swap happens at a safe boundary with no mixed revision.
- A stale browser cannot control a replacement run or overwrite a newer revision.
- Over-budget candidates are rejected with distinct-analyzer cost detail.
- Applying a near-limit valid state does not cause unbounded catch-up work.

## Phase 5 — Mobile draft editor

Deliverables:

- FFT analyzer cells and visual layer stack.
- Analyzer-source selector on each compatible visual layer.
- Frequency/gain graph with two to eight points and exact synchronized fields.
- Add/duplicate/delete/rename/reorder flows.
- Sticky Apply/Discard and dirty-state handling.
- Conflict recovery that preserves draft work.
- Browser interaction and accessibility tests.

Acceptance:

- A multi-field editing session makes no runtime request before Apply.
- One Apply sends one complete desired state.
- Polling never overwrites a dirty draft.
- Phone editing does not lose field focus or rebuild the whole controls tree.
- All graph operations are possible without drag gestures.
- Dependency handling prevents orphaned visual-layer analyzer references.

## Phase 6 — Durable libraries

Deliverables:

- Controller-owned analyzer favorites, layer templates, and complete configurations.
- Protected `Default - Current` analyzer favorite.
- Current-compatible baseline configuration.
- Explicit boot-default operation.
- Version migrations and atomic persistence.
- Rename, duplicate, delete, and export/import design; export/import may ship later.
- Removal of renderer-owned periodic runtime-state writes.

Acceptance:

- Durable entities survive controller and Pi restart.
- The dropped renderer makes no write attempt to controller state storage.
- Loading a template copies it and does not create surprising live inheritance.
- Deleting a library item cannot corrupt saved configurations.
- Boot loads only a fully validated configuration and falls back safely if migration
  fails.

## Phase 7 — Performance qualification

Deliverables:

- Automated benchmark scenarios from the rendering plan.
- One-through-four-analyzer Pi results.
- Layer coverage/culling results.
- Bulk versus per-pixel results.
- Sustained temperature, CPU, audio, frame, and native-refresh observations.
- Decision record on whether a native compositor is necessary.

Acceptance:

- Zero duplicate FFT identities.
- Zero steady-state audio overruns in supported configurations.
- Analyzer p99 remains below 80% of hop interval.
- Supported 60 FPS configurations remain below 16.7 ms frame p99, or are explicitly
  classified as 30 FPS with visible UI guidance.
- No thermal throttling in the sustained test.
- No supported configuration can starve the matrix control/runtime queue.

## Phase 8 — Bluetooth audio-source track

Deliverables:

- User-owned audio/analysis service process boundary.
- USB source migration without visual regression.
- Narrow BlueZ/PipeWire source management.
- Authenticated bounded pairing/discovery/trust UI.
- Analysis-only A2DP phone source.
- Source switch/fallback transaction and health UI.
- Coexistence and latency measurements.

Acceptance:

- A trusted phone can connect after reboot according to policy and provide PCM.
- Incoming phone audio is analyzed without local playback by default.
- The UI clearly reports that analysis-only mode is silent.
- Source loss cannot wedge analyzers or leave falsely healthy status.
- Bluetooth load does not violate supported analyzer/render/audio gates.
- The matrix child does not need direct access to the user's PipeWire socket.

## Phase 9 — First future feature layer

Use musical note detection as the architecture proof rather than forcing it into the
FFT-bar type.

Deliverables:

- One typed note/pitch analysis source.
- A note visualization layer referencing it.
- Reuse of PCM capture and any compatible spectral work.
- Independent cadence/window where accuracy requires it.

Acceptance:

- Adding the new analyzer/layer type does not require FFT-specific HTTP routes or a
  second configuration system.
- Its compute cost participates in the same Apply budget.
- FFT visual layers continue unchanged.

## Test portfolio

### Unit and property tests

- Ring wrap, source-generation changes, and discontinuities.
- Scheduler hop alignment and skip behavior.
- Duplicate analyzer canonicalization.
- FFT count invariant under arbitrary render rates/consumer counts.
- Response-curve ordering/interpolation and finite-value rejection.
- Stable IDs through rename/reorder.
- Opaque compositor equivalence and coverage correctness.
- Time-based smoothing across variable frame cadence.
- Schema migration and persistence failure recovery.

### Integration tests

- Audio fixture -> analysis frames -> several layers -> deterministic RGB frames.
- Controller Apply -> private runtime transport -> staged swap -> revision response.
- Service restart with stored favorites/configuration.
- Analyzer deletion/reassignment.
- Source switch success, timeout, and rollback.
- Browser conflict and dirty-draft preservation.

### Hardware tests

- Real USB mic and panel.
- One and four analyzers.
- Shared analyzer with many layers.
- Full and partial coverage-culling cases.
- Bulk frame output and visible regression comparison.
- FFT/audio-to-photon stimulus measurement.
- Bluetooth connect/stream/disconnect/reconnect.
- Sustained worst-case thermals, refresh, and audio health.

## Rollout and rollback

- Keep the existing FFT program selectable until the new baseline passes hardware
  equivalence and persistence gates.
- Migrate stored state by copy; never destroy the old file during first import.
- Feature-flag the new bulk compositor and analyzer service during qualification.
- Preserve one known-good baseline configuration in the controller store.
- A failed new-runtime launch returns to the existing supervised visualization or a
  safe system-info display without changing boot default.

## Explicit non-goals for this refactor

- Alpha/opacity and generalized blend modes.
- Simultaneous analysis of multiple physical audio sources.
- Browser-side FFT as the production analyzer.
- Streaming raw audio over the control API.
- A full C++ or Rust rewrite without measured justification.
- Bluetooth-to-Bluetooth audio forwarding.
- Automatic persistence of every experiment.

