# Web Control Panel Plan (Performance-First)

Last updated: 2026-04-15
Status: Planning approved, implementation not started

## 1. Goal
Create a standalone web GUI for tuning FFT visualizer variables while preserving maximum visualizer performance and independence.

## 2. Non-Negotiable Constraints
- Visualizer performance is paramount.
- No setting needs to update mid-frame.
- Slight delay in setting application is acceptable.
- Web UI must not receive FFT/frame data.
- Visualizer must run independently if control panel is down or absent.

## 3. Architecture Overview
Two-process model:
- Process A: FFT visualizer (real-time data plane)
- Process B: Web control panel service (control plane)

Control flow:
- Browser UI -> Control panel service -> Visualizer command ingress

No reverse FFT stream:
- Control panel does not consume FFT bins, frame buffers, or per-frame telemetry.

## 4. Runtime State Model in Visualizer
Use dual settings state:
- Active state: read-only for current frame render.
- Pending state: receives validated incoming updates.

Apply policy:
- Apply updates only at frame boundary (or fixed apply cadence), never mid-frame.
- Prefer atomic swap or lock-minimized commit from pending -> active.

Coalescing:
- If multiple updates for the same field arrive before apply window, keep latest value only.

## 5. Update Cadence and Throttling
Default conservative behavior:
- UI sliders commit on release by default.
- Optional live slider mode allowed but capped at low rate.

Suggested limits:
- Service ingress cap: low global request rate.
- Per-parameter update cap: low rate.
- Visualizer apply cadence: 100-250 ms (tunable).

Reasoning:
- Slower control updates reduce contention and stabilize frame timing.

## 6. Control Contract (Schema-First)
Define a versioned parameter contract before coding UI:
- Name/path (for example, categories and layer-scoped fields)
- Type (bool/int/float/enum/string)
- Range or enum set
- Default value
- Apply class

Apply classes:
- Deferred: apply at next apply window.
- Quiescent: apply only at safe mode boundary.
- Restart-required: explicit restart needed.

Validation rules:
- Reject unknown fields.
- Clamp or reject out-of-range values per field policy.
- Reject wrong types.

## 7. Failure Isolation and Recovery
Hard requirement:
- Visualizer continues running if control service fails, restarts, or disconnects.

Operational behavior:
- Last known valid active settings remain in effect.
- Reconnection resumes command intake without forcing restart.

## 8. Suggested Repository Layout
Within my_programs:
- fft_program/
- fft_control_panel/

Proposed control-panel structure:
- fft_control_panel/service/ (API server, validation, throttling, command publish)
- fft_control_panel/web/ (static UI assets)
- fft_control_panel/schemas/ (parameter and preset schemas)
- fft_control_panel/presets/ (saved tuning profiles)
- fft_control_panel/config/ (service config)
- fft_control_panel/systemd/ (service units)
- fft_control_panel/tests/ (API/contract/validation tests)
- fft_control_panel/docs/ (operations and API docs)

Note:
- fft_program remains independently executable with optional control adapter.

## 9. Presets Strategy
Support preset operations as batched parameter updates:
- Save preset
- Load preset
- Revert to last snapshot

Rules:
- Apply as one logical batch to avoid partial visual artifacts.
- Store schema version in each preset.

## 10. Security and Exposure
Default mode:
- Local/LAN-only access.

Minimum protections if exposed beyond local machine:
- Authentication
- Network restrictions/firewall
- Reverse proxy/TLS where applicable

## 11. Phased Implementation Plan
Phase 1: Contract and classification
- Freeze parameter schema and apply classes.

Phase 2: Visualizer control adapter
- Add ingress queue, pending state, boundary apply loop.

Phase 3: Control service
- Add API, validation, coalescing, throttling, command publish.

Phase 4: Minimal UI
- Start with highest-value controls only.

Phase 5: Presets and hardening
- Add save/load presets, resilience, and service ops.

## 12. Acceptance Criteria
Must pass before rollout:
- Independence test: visualizer runs unchanged with service stopped.
- Stability test: rapid UI changes do not cause noticeable frame-time regression.
- Consistency test: no mid-frame setting changes.
- Recovery test: service restart does not require visualizer restart.
- Determinism test: preset load yields consistent state.

## 13. Resume Checklist (Next Session)
Start from this order:
1. Build and approve parameter inventory sheet.
2. Assign apply class for each field.
3. Finalize update cadence defaults.
4. Confirm IPC mechanism choice.
5. Begin Phase 2 visualizer adapter.

## 14. Open Decisions to Lock
- IPC transport choice (local HTTP bridge, Unix socket, or other lightweight channel).
- Exact apply cadence default (100 ms vs 250 ms).
- Final per-parameter rate limits.
- Which settings are in MVP UI vs later phases.

## 15. Related Planning Document
- Mobile phone UX and customer setup distribution plan: [mobile_onboarding_strategy.md](mobile_onboarding_strategy.md)
