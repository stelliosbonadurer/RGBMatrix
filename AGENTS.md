# Project Agent Instructions

These instructions apply to the entire repository. Use them with the current user
request and the more detailed records under `docs_agents/`. Runtime code and current
evidence take precedence when older documentation has drifted.

## Begin every task safely

1. Read the user's request and all records it names completely.
2. Read the relevant guides in `docs_agents/`, beginning with
   `docs_agents/README.md` when the area is unfamiliar.
3. Inspect `git status --short`, recent history, the relevant implementation, and its
   tests before proposing a change.
4. Treat pre-existing modified and untracked files as user work. Do not overwrite,
   discard, reformat, or fold them into the task without explicit approval.
5. Establish the current behavior and source identity before relying on a handoff,
   branch name, deployment pointer, or documentation claim.
6. Keep the requested scope distinct from adjacent cleanup and future roadmap work.

## Session records

For every development-mode task, and for any other substantial implementation,
architecture, performance, deployment, or investigation session, create a folder:

```text
docs_agents/session summaries/YYYY-MM-DD-N-<short-topic>/
```

`N` is the one-based chronological sequence for that date.

Create and maintain these individual Markdown records during the work, not only at
the end:

- `README.md` — objective, success criteria, scope, baseline, current status, and a
  short index of the other records.
- `decisions.md` — numbered decisions, options and tradeoffs, recommendation, user
  answer when required, rationale, and superseded decisions.
- `changes.md` — behavioral changes, files touched, important implementation details,
  and relevant data, copy, allocation, or control-flow paths.
- `tests.md` — exact commands, results, environment, skipped checks, failures, and
  limits on what the results prove.
- `architecture.md` — preserved invariants, before/after structure, interface or
  ownership changes, and rejected alternatives. State explicitly when there was no
  architecture change.
- `handoff.md` — final state, remaining risks, working-tree state, operational state,
  and the smallest useful next step.

Put raw benchmark or measurement output in an `artifacts/` subdirectory and link it
from `tests.md`; do not paste large raw results into the narrative records.

For a small task, keep each file brief. The separate files are an evidence trail, not
an invitation to invent detail or duplicate the same narrative six times.

### Reproducible source identity

When results depend on an exact implementation, record:

- the base commit and relevant committed candidate commits;
- `git status --short` and whether the measured tree was dirty;
- hashes of relevant dirty or deployed files;
- any temporary qualification-only changes or merges; and
- the actual source version used at each comparison point.

A machine checkout pointer, documentation commit, or deployment label is not proof
of the measured source when the implementation differs or the tree is dirty.

## Development mode

When the user says **development mode**, follow this workflow until the requested
cycle is complete or the user disables it.

### 1. Orient and bound the work

- Begin with a read-only audit of the controlling records, current code, tests,
  configuration, and relevant operational integration.
- State the goal, acceptance criteria, protected behavior, out-of-scope work, known
  uncertainty, and the exact baseline.
- Create the session-record folder at the beginning and update it continuously.
- Distinguish an accepted production baseline, an experimental candidate, and a
  promoted production version. Never silently treat one as another.

### 2. Make decisions with the user

Before each meaningful design choice, ask one focused question and wait for the
answer. Include:

- 1 to 4 concise bullets explaining the real tradeoff;
- a clear recommendation and the evidence behind it; and
- one decision at a time where practical.
- mentions of alternatives and the tradeoffs.

A meaningful choice changes architecture, externally visible behavior, compatibility,
risk, data ownership, resource use, measurement validity, deployment state, or the
accepted baseline. Routine edits that directly implement an approved choice do not
need another pause. If inspection removes the choice because only one option is safe
or consistent with the requirements, explain that finding and proceed.

Record approved decisions immediately in `decisions.md`. If new evidence invalidates
one, stop, show the evidence, and ask whether to revise it.

### 3. Implement the smallest useful increment

- Prefer a narrow, reversible, independently testable change.
- Preserve the accepted path as the default when comparing a candidate unless the
  user explicitly approves replacement.
- Keep experiments opt-in and qualification instrumentation bounded and outside the
  permanent product architecture where practical.
- Avoid unrelated refactors, speculative abstractions, generic frameworks, and work
  from later roadmap steps.
- Preserve hot-path invariants and stable object/buffer identities where performance
  matters. Identify every unavoidable new allocation, copy, wait, or lock boundary.
- Do not weaken a working baseline merely to make two candidates share code.

### 4. Validate claims proportionately

- Run focused tests first, then the broader relevant suite, syntax/compile checks,
  and `git diff --check` where applicable.
- Test protected behavior and failure behavior, not only the new success path.
- For performance choices, measure the complete end-to-end workload under equivalent
  conditions. A component microbenchmark may diagnose a cost but cannot select the
  winning architecture by itself.
- Define metrics, service/quality gates, run order, duration, and the decision rule
  before collecting comparison data. Alternate order or repeat close comparisons.
- Preserve and document negative results. If a candidate does not materially win or
  justify its complexity, retain the simpler accepted path.
- State exactly what local or simulated tests cannot prove about audio, native
  bindings, timing, GPIO, or physical panel behavior.

### 5. Review and hand off

- Review the final diff for scope, correctness, failure bounds, allocations/copies,
  concurrency, and stale documentation.
- Reconcile every acceptance criterion with evidence in `tests.md`.
- Ensure the session records identify what changed, what was merely evaluated, what
  remains uncommitted, and what is currently deployed.
- End the cycle at its stated boundary. Do not automatically begin the next roadmap
  step.

### Delegation in development mode

Use sub-agents only for bounded, non-conflicting work such as independent audits,
test inspection, documentation checks, or a final diff/hot-path review. The primary
agent must personally read all controlling records, own design recommendations,
review every edit, and verify the integrated result. Sub-agent conclusions are
evidence to check, not decisions to accept automatically.

## Hardware, remote-system, and deployment safety

Do not deploy, restart services, run hardware benchmarks, perform LAN tests, replace
remote files, or change the production baseline without explicit approval.

Before requesting approval for a hardware or remote measurement, state:

- exact baseline and candidate source identities;
- exact files and qualification-only changes to deploy;
- configuration, input, and environmental conditions;
- run order and duration;
- metrics, quality/service gates, and the decision the run will inform; and
- backup, rollback, and post-test restoration plans.

Before an approved deployment, inspect remote status and hashes again, preserve
intentional modified or untracked artifacts, create a targeted timestamped recovery
archive, transfer only approved files, and verify local/remote hashes. Prefer
temporary runtime overrides. Restore the previously approved production state after
the experiment unless the user separately approves promotion.

Approval to test a candidate is not approval to promote it.

## Workflow quality rules

- Use decision IDs such as `D-001` so code, tests, measurements, and later handoffs
  can point to the choice they support.
- Write acceptance criteria as observable outcomes before implementation begins.
- Separate facts, measurements, inferences, recommendations, and user decisions.
- Record failed commands and rejected alternatives when they affect confidence or
  prevent future repetition; omit inconsequential trial-and-error noise.
- Prefer an evidence-backed stop/go gate after each narrow increment over a long,
  speculative implementation sequence.
- Keep documentation concise enough that the next agent can read all controlling
  records before acting.
