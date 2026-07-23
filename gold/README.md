# Gold task pool

The checked-in gold pool is a deny-by-default set of exact Formal Conjectures
formalizations for the production verifier and subnet submission protocol.

## Admission policy

Each task must:

- come from the pinned Formal Conjectures commit;
- be a compiled `research open`, `DIRECT_PROP` theorem;
- use `formalized` mode, whose target is definitionally equal to the complete
  source theorem type;
- have identical canonical source and target type hashes;
- contain no `sorryAx` term or answer annotation in its type;
- have no formal-proof metadata or exact collision with a cataloged proved
  theorem;
- use a source theorem, source file, and canonical type not present in the
  retired v1 pool;
- be the only admitted task from its source file and for its canonical type;
- compile and pass the independent `TaskInspector` target check.

The pool does not create a positive/negative pair, extract a proposition from
an answer wrapper, or substitute a new answer. If a future admitted source
theorem is itself a negation, that exact negation may be used.

## Scope

The current pool contains 64 tasks balanced across 13 repository areas. These
checks establish that a task is well-formed and that the verifier checks the
formalization published by Formal Conjectures. They do not establish that an
open problem is solvable, faithfully states its informal source, or deserves a
particular reward.

`allowlist.json` commits to every task bundle and target type. The subnet miner
accepts only those exact commitments. `retired-source-theorems.json` prevents
the previous pool from being selected again.

## Rebuilding

`scripts/rebuild_gold_pool.py` selects, builds, and inspects a fresh pool. It
refuses to overwrite an existing task directory or allowlist. Generate into
fresh staging paths, review the selection and hashes, and only then replace the
published pool.
