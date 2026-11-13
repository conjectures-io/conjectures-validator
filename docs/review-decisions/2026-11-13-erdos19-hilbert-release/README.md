# Erdős and Hilbert publication

The requested publication combines the nineteen remaining Erdős candidates with
the reviewed Hilbert formalizations. The candidate reward pool contains 327
targets and 654 proof/refutation bundles. Its production baseline has 306 targets.

The Erdős additions are 151, 159, 295, 311, 322, 345, 388, 389, 393, 422, 423,
430, 461, 462, 467, 472, 478, 483 and 545. The November 13 catalog refresh
agrees with the earlier bounded literature review. Erdős 173 has a matching
accepted solution and its two versions are retired. Its statement and attribution
remain available in the public retirement record.

The Hilbert publication uses the exact interpretations accepted by Fable 5.2
after four review rounds. The complete packet is published in this validator
repository and in `staged/hilbert-formulations-20261113` in the task repository.

| Material | Publication and submission scope |
| --- | --- |
| Hilbert 6 | Reviewed selected-model specification, including probability, mechanics and classical limits |
| Hilbert 12 | Reviewed arithmetic output, benchmarks and general recipe contract, with the separately named rank-one abelian Stark conjecture offered for submissions |
| Algebraic Hilbert 13 | The full reviewed complex resolvent-degree assertion RD(7) = 3 offered for submissions |
| Hilbert 16 | Reviewed classification output and cycle-bound statement, with the existing general uniform-bound proof claim recorded for assessment |

The general Hilbert 12 contract leaves explicitness to mathematical assessment.
Hilbert 6 covers the physical models specified in the review. These scope limits
are recorded in the accepted package and accompany its publication.

The two new submission targets are
`HilbertCatalog.Hilbert13.conjecture` and
`HilbertCatalog.RankOneAbelianStark.conjecture`. Their source wrappers refer
directly to the reviewed proposition definitions. The four mathematical modules
are copied byte for byte from the accepted packet. The source patch and extracted
catalog bind the definitions and statement types to the release.

The publication check found a full general uniform-bound claim for Hilbert 16
in OpenAI's September 24 manuscript. Its published Lean scope covers quintic
Liénard systems. The release records the general claim as awaiting assessment.
The review covers the manuscript's abstract and main theorem scope. It does
not establish the validity of its 160-page proof.
[Author manuscript](https://github.com/openai/math/tree/main/preprints/uniform-bounds-for-planar-polynomial-limit-cycles-September-24-2026)
[Published Lean scope](https://github.com/openai/math/blob/main/lean/docs/143.md)

The algebraic Hilbert 13 status check includes Farb–Wolfson and
Edens–Reichstein. The positive-characteristic results concern a different base
field from the complex target used here. The rank-one Stark check includes
the original Tate formulation and the Berkeley–Stanford seminar account.
The bounded search found no full resolution of either exact submission target.
[Farb–Wolfson](https://www.math.uchicago.edu/~farb/papers/RD.pdf)
[Edens–Reichstein](https://doi.org/10.4171/DM/984)
[Rank-one Stark account](https://math.berkeley.edu/~hhao/notes/Rank-one%20abelian%20Stark%20conjecture.pdf)

## Validation and deployment

The four modules and two submission wrappers compile. The full Formal
Conjectures build passes 10,629 jobs, and the extracted catalog has 6,064
declarations. Every type hash of the 6,062 retained declarations matches the
previous catalog. Bundle validation, sandbox controls, release commits and
deployment results are recorded in the accompanying release evidence.

The local self-contained suite passes 1,586 tests, with 909 environment-dependent
tests skipped. All 58 pool/admission checks, 15 production-sandbox controls and
71 tracing checks pass. The CI dependency gate identified
GHSA-mf5v-w3w3-f6cg in OpenTelemetry API 1.45.1. The release updates the aligned
OpenTelemetry packages to 1.45.2/0.66b2, and the pinned OSV scan passes.
A direct regression check preserves valid trace contexts and rejects the
oversized malformed carrier described by the advisory.
[Upstream advisory](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-mf5v-w3w3-f6cg)
[Patch release](https://github.com/open-telemetry/opentelemetry-python/releases/tag/v1.45.2)

The user authorized publication, merge and deployment on November 13. The
release follows the repository's pull-request, CI and deployment workflow.
