# Reward readiness of Hilbert 6, 12 and 16, and two open cases of Hilbert 16

Reviewed November 15, 2026. The owner asked for the published Hilbert problems
to be made ready and offered with rewards once they meet the admission bar.
This record states what was admitted, what was prepared and held, and why two
problems still carry no general reward target.

| Problem | Reward status after this review |
| --- | --- |
| 6 | No reward target. Research page unchanged. |
| 12 | Reward through the existing rank-one abelian Stark target. No general target. |
| 13 | Existing algebraic target unchanged. |
| 16 | New target: components of real quintic surfaces. Quadratic limit cycles prepared and held. General uniform bound still held. |

**A statement that compiles is not thereby faithful, open or proved.** The Lean
checks below establish compilation, permitted axioms and non-vacuity. Fidelity
rests on the two reviews. Open status rests on a bounded literature search.

## Admitted: components of real quintic surfaces

`HilbertCatalog.Hilbert16QuinticSurfaces.conjecture` states
`HilbertDrafts.Hilbert16Open.QuinticSurfaceComponents`: every nonzero real
homogeneous quintic in four variables with nonsingular complex projective zero
set has a real zero locus in RP³ with at most 23 connected components.

Hilbert asks about the number, form and position of the sheets of an algebraic
surface and names the unknown maximum for degree four. That maximum is ten
(Kharlamov, 1972). Degree five is the first degree in which the maximum is
unknown. It is 23, 24 or 25: the upper bound follows from the Smith and
Comessatti inequalities, and Orevkov constructed 23 components in 2001. The
target asserts only the bound 23, so it may be true or false. A proof gives the
maximum 23. A refutation needs a quintic with 24 or 25 components and leaves
those two values to be separated.

The search found the question stated as open in 2018, 2019, 2025 and March
2026, and no later construction or bound. Sources and their verification depth
are in `literature/quintic-surfaces-status.md`.

## Prepared and held: quadratic limit cycles

`HilbertCatalog.Hilbert16QuadraticLimitCycles.conjecture` states
`HilbertDrafts.Hilbert16Open.QuadraticFourCycles`: every real planar polynomial
vector field of degree at most two has at most four limit cycles, counted as
point sets in the whole affine plane. This is the upper bound in H(2) = 4.

Both reviews accept the statement. It is not admitted, because a live
unrefereed preprint claims exactly this result (Pedregal, arXiv:2103.07193v4,
November 3, 2026). The assessment in `literature/quadratic-limit-cycles-status.md`
finds the claim neither established nor refuted. Its quadratic corollary is a
direct specialization of an unverified general theorem. The preprint has no
citations, an earlier version was withdrawn, and two checkable errors occur
elsewhere in it without touching the quadratic case. Current expert sources,
including Qin and Sun in October 2026, still describe H(2) as unknown.

The pool's precedent excludes or holds a target when a full-resolution claim
is identified and unreviewed (Cantoni, Erdős 326, Erdős 1004 and the Hilbert 16
uniform bound). The declaration is therefore published in the source and
recorded with decision `hold_resolution_claim`. Admission needs either an
owner decision or a review that disposes of the claim.

## Still held: the general uniform bound

`HilbertDrafts.Hilbert16.UniformBound` remains outside the pool. OpenAI's
September 24 manuscript claims it, and no independent verification or
refutation was located. The manuscript asserts only that some bound exists in
each degree and gives no values, so it decides neither case above.

## Hilbert 12: no general reward target

The general problem needs a criterion for an explicit analytic construction.
Three candidate criteria were examined, and none gives a statement that can
responsibly carry a reward.

1. **Recipes chosen per base field.** The reviewed contract is satisfied by
   constant functions whose values enumerate the abelian elements. The reviewed
   identity-reencoding lemma shows the same weakness. No class field theory is
   needed, so a proof would be a formalization defect.
2. **One analytic family for all fields, evaluated at all base-field points.**
   Analyticity does not constrain values on a countable set. Interpolation of
   Stäckel type produces an entire function taking prescribed dense-set values
   at every algebraic point, which appears to satisfy such a contract with no
   arithmetic content. This is the author's analysis and is not formalized.
3. **A restricted function class.** Any fixed class, such as differentially
   algebraic or automorphic functions, replaces Hilbert's question with a
   different one whose answer may be trivially positive through algebraic
   functions or negative for reasons unrelated to class field theory.

The recognised precise conjecture in this programme is the rank-one abelian
Stark conjecture, which is already an active target. The arithmetic and
benchmark statements in the package are known theorems and are not eligible
for an open-problem reward under review policy v4.

## Hilbert 6: no reward target

The reviewed statements are selected physical models. Their kinetic and fluid
limits follow results with published proof claims by Deng, Hani and Ma, in
ensemble and initial-data variants that the cited papers do not prove
literally. A reward would therefore pay either for formalizing claimed
theorems, which policy v4 excludes, or for variants whose literal truth this
review cannot certify. No closed open statement inside Hilbert's programme was
identified that avoids both problems.

## Reviews

Two fresh-context reviewers examined the statements. Their briefs and reports
are reproduced verbatim under `reviews/`.

- The Lean-semantics adversary found no defect in either statement. It
  machine-checked that the topology on RP³ is the standard quotient topology,
  that the component count is a bijection with the connected components, and
  that isolation is equivalent to the tube formulation. Its files are in
  `reviews/attacks/` and compile in `verify.sh`.
- The mathematical fidelity reviewer accepted both statements with labelling
  changes, all applied: neutral titles, the affine-plane and multiplicity
  conventions, the known lower bounds stated as outside the targets, and
  disclosure of the unrefereed H(2) = 4 claims.

`Hilbert16OpenChecks.lean` proves that the Fermat quintic satisfies the surface
hypothesis and that the unit circle is a limit cycle of an explicit cubic
field. Neither statement can hold for an empty reason.

## Verification

Run `bash verify.sh` from this directory. It uses the pinned image, Lean
4.35.0-rc2 and Mathlib `065356127b1dc0016f66b7283ce0ce2c4055aa55`, with
networking disabled and warnings as errors. `Hilbert16.lean` is byte-identical
to the module accepted on November 13. All 36 audited declarations depend only
on `propext`, `Classical.choice` and `Quot.sound`. Release evidence is in
`evidence/`.

## Limits

- Both reviewers and both literature searches ran on the same model family as
  the author, in separate contexts. The cross-model reviewer used for the
  November 13 packet was unavailable. No human mathematician reviewed this
  packet.
- The literature searches were bounded. They do not certify that no solution
  exists.
- Three steps in the semantics review are textbook arguments on paper: tube
  isolation implies standard isolation, degenerate fields of degree at most two
  have no limit cycles, and an admissible quintic has finitely many components.
