# Release validation

This file records how the November 15 admission was built and checked. The
decision and its reasons are in `README.md`. Coordinates are in
`release-coordinates.json`.

## Source and catalog

The source commit `bc92995b0ab14ac035d3cd535cbca4a598097a8e` adds four files to
the November 13 source: the statement module, its non-vacuity checks and two
wrappers. The full source build passes 10,636 jobs. The extracted catalog has
6,066 declarations. All 6,064 retained rows are identical to the previous
catalog, including every type hash.

## Task publication

The pool has 328 targets and 656 planned bundles. All 654 retained bundles are
reused, and no retained dependency changed. Two bundles were built, for proof
and refutation of `HilbertCatalog.Hilbert16QuinticSurfaces.conjecture`. The
quadratic declaration is in the catalog and has no bundle.

## Controls

Eight controls ran under the production sandbox profile as UID 10001 with no
network and a read-only root. The positive control verifies. For both new
bundles a wrong statement is rejected as `STATEMENT_MISMATCH`, and `sorry`, a
forged axiom and the source placeholder are rejected as
`SUBMISSION_POLICY_VIOLATION`. These controls used the admission image with
this branch's verifier package. The production image is checked again before
the worker is installed.

The 98 pool and admission tests pass against the merged tasks commit. Ruff and
the self-contained unit suite pass.
