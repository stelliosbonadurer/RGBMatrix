# Decisions

## D-001 — comparison baseline

- **Options:** qualified step 5; earlier production pointer.
- **Recommendation:** use qualified step 5 because its exact source and measurements
  are documented; the Pi pointer is operational metadata, not a measured identity.
- **User decision:** approved step 5 as the new rebuild baseline, without implicit
  deployment or production promotion.

## D-002 — first bulk-output candidate

- **Options:** one Pillow-owned image; a NumPy staging buffer plus Pillow copy; a
  native binding extension.
- **Recommendation:** one reusable Pillow image is the smallest reversible comparison
  and avoids an application-level staging copy. A native extension remains the only
  serious later alternative if Pillow loses, but expands the native build surface.
- **User decision:** approved the Pillow candidate and requested more information on
  the deferred native option.

## D-003 — candidate validity under live controls

- **Options:** recompile in the frame loop; allow drift; reject invalidating commands
  at the existing batch boundary.
- **Recommendation:** reject before batch preparation or mutation to preserve the
  compiled hot path and atomic runtime-control semantics.
- **User decision:** approved structured `unavailable` rejection while retaining
  snapshots and analyzer/scaler controls.

## D-004 — instrumentation boundary

- **Options:** a temporary Pi-only qualification patch; a tracked opt-in bounded
  hook.
- **Recommendation:** use a temporary Pi-only patch because this is one narrow
  architecture comparison and measurement code should not become product telemetry.
- **User decision:** approved the recommendation. The patch and deployed file hashes
  must be recorded with the results, and the Pi must be restored afterward.

## D-005 — authoritative comparison workload

- **Options:** representative live music; a deterministic replay or synthetic bar
  trace.
- **Recommendation:** use representative live music for the selection decision so
  the complete analyzer-to-VSync workload matches production. Deterministic traces
  may diagnose output cost but cannot choose the accepted architecture.
- **User decision:** approved representative live music.

## D-006 — duration and counterbalanced ordering

- **Options:** one five-minute window per path; four counterbalanced five-minute
  windows.
- **Recommendation:** run `direct -> Pillow -> Pillow -> direct`, with a 60-second
  settle before each measured window. This forms one direct/Pillow pair and one
  Pillow/direct pair, limits order bias, and retains the prior five-minute window.
- **User decision:** approved the recommendation.

## D-007 — speed-first material-win gate

- **Options:** reject a faster candidate for higher CPU; treat CPU as context while
  gating on speed and service health.
- **Recommendation:** require both counterbalanced pairs to favor Pillow and require
  at least 10% pooled improvement in display/swap FPS and complete draw-through-swap
  p50/p95. Retain the existing analysis-age, failure, throttling, runtime/audio, and
  visible-parity gates. Keep direct when results are mixed or below the threshold.
- **User decision:** approved the conservative speed gate and clarified that CPU use
  is acceptable. CPU and RSS are observational only unless increased load causes
  throttling, thermal instability, or a service-quality failure.

## D-008 — display and unique-content rates

- **Options:** gate only raw swap FPS; measure both raw swap FPS and distinct acquired
  analysis sequences actually swapped.
- **Recommendation:** keep swap/display FPS as the primary 10% speed gate, require
  unique-content FPS not to regress by more than 5%, and record renderer, VSync, and
  combined latency separately. Unique-content rate need not improve because this
  checkpoint changes output rather than analysis.
- **User decision:** approved measuring both rates and the corroboration rule.

## D-009 — exact bounded timing samples

- **Options:** fixed preallocated arrays retaining exact samples; online approximate
  histograms.
- **Recommendation:** preallocate `uint64` arrays for 100,000 frames per five-minute
  window, then write summary JSON and compressed raw samples. This bounds memory,
  covers up to 333 FPS, and keeps exact percentiles reproducible.
- **User decision:** approved the recommendation and requested a separate final
  concepts/findings document suitable for learning, future work, and an engineering
  case study.

## D-010 — reproducible pre-qualification commits

- **Options:** deploy a dirty hash snapshot; create one mixed commit; create a source
  commit followed by a documentation-only commit that records its identity.
- **Recommendation:** use two commits so the candidate has a stable exact identity
  and the records can name it without a circular self-reference. Exclude unrelated
  user-owned files.
- **User decision:** approved the two local commits. This is not approval to deploy,
  restart the service, run hardware tests, or promote the candidate.

The next undecided item is D-011: approval of the completed temporary qualification
patch and remote execution/rollback plan.
