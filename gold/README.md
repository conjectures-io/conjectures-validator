# Gold task pool

The checked-in gold pool is a deny-by-default set of exact Formal Conjectures
formalizations for the production verifier and subnet submission protocol.

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
- use a source theorem and canonical type not present in either previous gold
  pool;
- be the only admitted task for its canonical type;
- compile and pass the independent `TaskInspector` target check.

Each reward task contains one exact canonical theorem whose proof closes the whole source problem.
Partial results, numbered parts, variants, candidate bounds, and multi-target bundles are excluded.
The checked-in `whole-problem-targets.json` records the audited closure theorem for every admitted
problem. `task-groups.json` is intentionally empty and remains committed to make the absence of
grouped tasks machine-checkable.

The v3 selection adds a separate solver-oriented audit. Every admitted source:

- is an Erdős problem;
- is still marked `research open` on the latest reviewed upstream `main`;
- belongs to a parent problem that is `open`, `verifiable`, `falsifiable`, or
  `decidable` in the pinned Erdős Problems database;
- has no open upstream pull request resolving or correcting the selected
  theorem at audit time; unrelated source-file changes are recorded;
- has no formal-proof metadata or cataloged proved-type collision;
- has at least one recorded feasibility signal: a compact target, discrete
  domain, finite or finitary structure, partial results in the same source, or
  a standard Mathlib surface;
- is outside `FormalConjectures/WrittenOnTheWallII/`, which is a hard-excluded
  source prefix.

The pool does not create a positive/negative pair, extract a proposition from
an answer wrapper, or substitute a new answer. If a future admitted source
theorem is itself a negation, that exact negation may be used.

## Scope

The current pool contains 29 reward tasks covering 29 audited Erdős problems from 29 source files.
Every task has exactly one theorem target and one source file. A valid proof therefore closes the
whole selected problem; no grouped or partial task can earn a reward. The pool contains no Written
on the Wall II task. The GitHub review covered all 281 open
pull requests visible at audit time and excluded selected theorems with an
active resolution or correction. A separate pinned check against the Erdős
Problems database excludes parent problems currently recorded as solved.

“Plausibly attackable” is a comparative solver-target screen, not a promise.
These checks establish that a task is well-formed, remained upstream-open at
the audit boundary, avoided a known active proof or correction PR, and has a
more manageable formal surface than the deliberately excluded frontier cases.
They do not establish that the conjecture is easy, guarantee that it can be
solved, prove fidelity to its informal source, or determine a reward.

`allowlist.json` commits to every task bundle and every target type. The subnet miner
accepts only those exact commitments. `selection-audit.json` records the
upstream and feasibility review and is itself committed by digest in the
allowlist. `whole-problem-targets.json` records the audited one-problem/one-target selection and is
committed by digest. `task-groups.json` records that there are no grouped tasks and is also committed
by digest. `retired-source-theorems.json` prevents either previous pool from being selected again.
Source citations extracted from the pinned Formal Conjectures docstrings are stored in each task's
metadata for site rendering.

## Rebuilding

`scripts/rebuild_gold_pool.py` loads the exact checked-in selection audit and
whole-problem target policy, builds every target, and inspects the resulting pool. It refuses to
overwrite an existing task directory or allowlist. Generate into fresh staging paths, review the
selection and hashes, and only then replace the published pool.
