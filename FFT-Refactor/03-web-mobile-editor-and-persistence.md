# Web/Mobile Editor and Persistence

## Interaction principle

Editing is local; Apply is remote. Dragging a point, changing an analyzer, or
reordering a layer must feel immediate in the browser without sending dozens of
commands or forcing the matrix through every intermediate state.

## Browser state model

The page owns four clearly named values:

- `serverSnapshot`: last authoritative response.
- `appliedConfiguration`: configuration at `serverSnapshot.revision`.
- `draftConfiguration`: mutable browser copy.
- `dirtyPaths`: fields changed relative to applied state.

Polling updates health and detects a newer revision, but it must not overwrite a
dirty draft. If another client applies a change while this browser has edits, show a
conflict banner with:

- Review server changes.
- Reload and discard draft.
- Save draft as a separate named configuration.
- Rebase later; automatic field merging is not required for the first release.

## Mobile behavior

- One-column cards with concise collapsed summaries.
- Sticky Apply/Discard footer whenever dirty.
- Minimum 44 CSS-pixel touch targets.
- Drag handles never overlap graph handles or vertical page scrolling.
- Move Up/Move Down buttons provide a non-drag alternative.
- Analyzer selector uses names plus a short settings summary.
- Advanced analyzer controls remain collapsed by default.
- The graph supports a temporary enlarged editing sheet on small screens.
- Numeric fields use appropriate decimal/numeric keyboards and preserve focus.
- No full controls-DOM replacement after every poll or edit.
- Unsaved changes survive a soft page refresh in browser session storage, keyed by
  controller identity and run ID, but are never treated as applied or durable Pi
  state.

## Analyzer cell behavior

Collapsed:

```text
Fast Full Spectrum                     Enabled
512 window / 512 hop / 8192 FFT / 86.1 Hz
Used by: Full Bars, Treble Peaks
Measured p95: 0.34 ms        Estimated CPU: 2.4% core
```

Expanded controls include favorite selection, rename, window/hop/FFT sizes, output
mode, validation, connected layers, duplicate, save favorite, and delete. Editing a
shared analyzer lists the visual layers affected before Apply.

Deleting a referenced analyzer opens a dependency dialog:

- Cancel.
- Reassign all dependents to another compatible analyzer.
- Delete analyzer and selected dependent layers.

There is no implicit choice.

## Visual-layer cell behavior

Collapsed:

```text
1  Bass Bars                 Visible
FFT input: Long Bass Window
60-1950 Hz / Ocean / Opaque topmost
```

Expanded controls are grouped into:

- Input: layer type and analyzer.
- Frequency/gain graph and exact point table.
- Dynamics: normalization, headroom, attack, release, peaks.
- Appearance: theme, gradient, overflow, shadows.
- Structure: duplicate, save layer, delete.

Changing analyzer updates the draft graph domain and validates existing response
points. Out-of-range points are not silently clamped; the UI offers an explicit
clamp or reset action.

## Proposed controller APIs

Exact paths may follow the existing `/api/v1` conventions, but the operations should
remain resource-oriented.

### Runtime

- `GET /api/v1/music/runtime` — authoritative applied state, revision, capabilities,
  health summary, and limits.
- `PUT /api/v1/music/runtime/configuration` — apply one complete candidate with
  `run_id` and `expected_revision`.
- `GET /api/v1/music/telemetry` — bounded recent aggregate metrics.

### Analyzer favorites

- `GET /api/v1/music/analyzer-favorites`
- `POST /api/v1/music/analyzer-favorites`
- `PUT /api/v1/music/analyzer-favorites/{id}`
- `DELETE /api/v1/music/analyzer-favorites/{id}`

### Layer templates

- `GET /api/v1/music/layer-templates`
- `POST /api/v1/music/layer-templates`
- `PUT /api/v1/music/layer-templates/{id}`
- `DELETE /api/v1/music/layer-templates/{id}`

### Complete configurations

- `GET /api/v1/music/configurations`
- `POST /api/v1/music/configurations`
- `GET /api/v1/music/configurations/{id}`
- `PUT /api/v1/music/configurations/{id}`
- `DELETE /api/v1/music/configurations/{id}`
- `POST /api/v1/music/configurations/{id}/make-boot-default`

All mutating routes use existing paired-session/bearer authentication, origin checks,
bounded JSON, strict schemas, and revision/ETag conflict protection.

## Persistence store

The controller service owns a versioned store under its writable state directory.
The exact representation may be individual JSON documents plus an atomic index or a
small SQLite database. The required behavior is more important than the first
storage engine:

- Atomic create/update/delete.
- Schema version on every entity.
- Stable UUIDs and timestamps.
- Bounded names and document sizes.
- Migration before making an entity active.
- Backup/export path.
- Crash-safe boot-default pointer.
- No direct browser-supplied filesystem paths.
- No writes by the dropped matrix child.

SQLite is preferable once libraries, references, and migrations become substantial;
atomic JSON is acceptable for the initial limited store if updates use write, fsync,
and rename in a controller-owned directory.

## Template reference semantics

Saved templates are copied into a configuration when used; configurations do not
silently change because a library template was edited later.

- An analyzer favorite creates a new analyzer cell with a new analyzer UUID.
- A layer template creates a new layer UUID and asks which compatible analyzer to
  connect.
- Metadata may retain `based_on_favorite_id` or `based_on_template_id` for display,
  but it is not a live inheritance relationship.

This makes saved configurations reproducible and prevents surprising cross-project
changes.

## Default/current migration

Provision the library with:

- Protected analyzer favorite: `Default - Current`.
- Layer templates matching current bass and mid defaults.
- One complete `Current Music Baseline` configuration.

Importing current runtime state is best-effort. Existing partial state has no stable
IDs or arbitrary response curves, so migration must create new IDs and log exactly
which fields were preserved or defaulted.

## Apply feedback

The Apply response contains:

- Resulting revision and effective configuration.
- Shared/deduplicated analyzer relationships.
- Estimated and measured compute summary.
- Warnings.
- Structured errors keyed by analyzer/layer ID and field path.
- Apply staging and swap durations.

The browser retains the draft on error. On success it replaces both applied and
draft state with the normalized response and clears dirty paths.

## Accessibility and fallback controls

- Every graph operation is possible through numeric fields and buttons.
- Layer order is exposed as a semantic list.
- Color is never the only indicator of dirty/error/connected state.
- Labels announce units and valid ranges.
- Graph points have stable accessible names such as `Point 2 frequency` and
  `Point 2 gain`.
- Reduced-motion preference disables decorative animation, not the matrix output.

