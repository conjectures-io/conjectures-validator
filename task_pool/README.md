# Task pool

The checked-in task pool is a deny-by-default set of exact Formal Conjectures
formalizations for the production verifier and subnet submission protocol. Tasks
are organized into explicit tiers. The allowlist commits the tier of every source
and bundle, so moving a task between tiers is a reviewed policy change.

## Layout

- `allowlist.json` is the pool-wide, machine-readable admission set.
- `tiers/<tier>/` contains that tier's selection audit and policy inputs.
- `../tasks/pool/<tier>/` contains the immutable task bundles.

The current release has one tier, `tier-1`. Additional tiers can be added without
renaming the pool or weakening deny-by-default validation.

The current wide candidate count and the policy changes required to reach 500
tasks are documented in [`CANDIDATE_AUDIT.md`](CANDIDATE_AUDIT.md).

## Admission policy

Each admitted theorem target must:

- come from the pinned Formal Conjectures commit;
- be a compiled `research open`, `DIRECT_PROP` theorem;
- use `formalized` mode, whose target is definitionally equal to the complete
  source theorem type;
- have identical canonical source and target type hashes;
- contain no `sorryAx` term or answer annotation in its type;
- have no formal-proof metadata or exact collision with a cataloged proved
  theorem;
- satisfy the selection and freshness rules declared by its tier;
- be the only admitted task for its canonical type;
- compile and pass the independent `TaskInspector` target check.

Each current task contains one exact canonical theorem whose proof closes the
whole source problem. Partial results, numbered parts, variants, candidate bounds,
and multi-target bundles are excluded from `tier-1`.

The `tier-1` selection has a separate solver-oriented audit. Every source:

- is an Erdős problem;
- is still marked `research open` on the reviewed upstream `main`;
- belongs to a parent problem marked `open`, `verifiable`, `falsifiable`, or
  `decidable` in the pinned Erdős Problems database;
- has no open upstream pull request resolving or correcting the selected theorem
  at audit time; unrelated source-file changes are recorded;
- has no formal-proof metadata or cataloged proved-type collision;
- has at least one recorded feasibility signal: a compact target, discrete
  domain, finite or finitary structure, partial results in the same source, or a
  standard Mathlib surface.

No Formal Conjectures source family is excluded at the pool level. In particular,
Written on the Wall II conjectures may be included in a future tier after the same
open-status, pull-request, collision, and compiled-target checks. The current
`tier-1` selection happens to contain only Erdős problems.

The pool does not create a positive/negative pair, extract a proposition from an
answer wrapper, or substitute a new answer. If a future admitted source theorem
is itself a negation, that exact negation may be used.

## Scope

`tier-1` contains 29 reward tasks covering 29 audited Erdős problems from 29
source files. Every task has exactly one theorem target and one source file. A
valid proof therefore closes the whole selected problem; no grouped or partial
task can earn a reward in this tier.

The GitHub review covered all 281 open pull requests visible at audit time and
excluded selected theorems with an active resolution or correction. A separate
pinned check against the Erdős Problems database excludes parent problems
recorded as solved.

“Plausibly attackable” is a comparative solver-target screen, not a promise.
These checks establish that a task is well-formed, remained upstream-open at the
audit boundary, avoided a known active proof or correction PR, and has a
manageable formal surface. They do not establish that the conjecture is easy,
guarantee that it can be solved, prove fidelity to its informal source, or
determine a reward.

`allowlist.json` commits to every task bundle, target type, and tier. The subnet
miner accepts only those exact commitments. Within each tier:

- `selection-audit.json` records upstream status and feasibility review;
- `whole-problem-targets.json` records the one-problem/one-target selection;
- `task-groups.json` records any grouped tasks (`tier-1` has none);
- `retired-source-theorems.json` prevents retired sources and canonical types
  from being selected again.

Source citations extracted from pinned Formal Conjectures docstrings are stored
in each task's metadata for site rendering.

## Rebuilding

`scripts/rebuild_task_pool.py` loads the checked-in selection audit and
whole-problem target policy, builds every target in the selected tier, and
inspects the result. It refuses to overwrite an existing task directory or
allowlist. Generate into fresh staging paths, review the selection and hashes,
and only then replace the published tier and pool-wide allowlist.

Production operates one active pinned pool. For a release, pause admissions and
wait until no submission is queued, running, retryable, awaiting review, or
awaiting reward processing. Then update the Formal Conjectures, Lean, Mathlib,
and verifier dependency pins together, regenerate and audit the tiers and
commitments, run the full test suite, and atomically activate the rebuilt pool
and verifier image before reopening admissions. A failed update leaves the
existing pins active. Historical pin values, task digests, tier assignments, and
reports remain in the audit database even though only one verifier version is
active.
