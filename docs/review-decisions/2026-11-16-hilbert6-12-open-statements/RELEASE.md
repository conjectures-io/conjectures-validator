# Release validation

This file records how the November 16 admission was built and checked. The
decision and its reasons are in `README.md`. Coordinates are in
`release-coordinates.json`.

## Source and catalog

The source commit `6adcbe13652f4e7c69ad02a783465ae2a99d5cb3` adds seven files to
the November 15 source: two statement modules, two consistency modules and
three wrappers. The full source build passes 10,643 jobs. The extracted catalog
has 6,069 declarations. All 6,066 retained rows are identical to the previous
catalog, including every type hash.

## Task publication

The pool has 330 targets and 660 planned bundles. All 654 retained bundles are
reused, and no retained dependency changed. Six bundles were built, for proof
and refutation of the three new statements.

## Controls

Twenty-two controls ran under the production sandbox profile as UID 10001 with
no network and a read-only root. The positive control verifies. For all six new
bundles a wrong statement is rejected as `STATEMENT_MISMATCH`, and `sorry`, a
forged axiom and the source placeholder are rejected as
`SUBMISSION_POLICY_VIOLATION`. These controls used the admission image with
this branch's verifier package. The production image is checked again before
the worker is installed.

The pool, admission and API task-pool tests pass against the merged tasks
commit. Ruff and the self-contained unit suite pass.
