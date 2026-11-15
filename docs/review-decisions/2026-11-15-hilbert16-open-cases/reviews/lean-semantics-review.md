# Adversarial Lean-semantics review

Reviewer model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-15T21:55:44.855Z)

```text
You are an adversarial reviewer for a platform that pays cash bounties for Lean 4 proofs OR refutations of formal statements of open problems. If a published statement can be proved or refuted cheaply because of a formalization defect (vacuous hypothesis, junk value, wrong quantifier, degenerate case, unintended reading, a definition that does not mean what the prose says), the platform loses money and credibility. Your job is to find such a defect in two candidate statements before publication, or to report that you could not.

Read these files (do not modify them; do not edit anything in that directory):
- <scratch>/h16/src/HilbertDrafts/Hilbert16.lean  (definitions; previously reviewed for a different purpose)
- <scratch>/h16/src/HilbertDrafts/Hilbert16Open.lean  (the two candidate targets)

The two candidate reward targets are the propositions
  A. `HilbertDrafts.Hilbert16Open.QuadraticFourCycles`  (:= `CycleBound 2 4`)
  B. `HilbertDrafts.Hilbert16Open.QuinticSurfaceComponents`  (:= `SurfaceComponentBound 5 23`)
Each would be published twice: one task asks for a proof of the proposition, the other for a proof of its negation. The intended informal meanings:
  A. Every real planar polynomial vector field x' = P(x,y), y' = Q(x,y) with deg P, deg Q ≤ 2 has at most 4 limit cycles (limit cycle = non-constant periodic orbit, as a point set, isolated among periodic orbits).
  B. Every nonsingular real projective surface of degree 5 in RP^3 (zero set in RP^3 of a real homogeneous quintic in 4 variables whose complex projective zero set is smooth) has at most 23 connected components.

For EACH target, fully unfold every definition it depends on (`PolynomialVectorField`, `eval`, `DegreeAtMost`, `IsPeriodicOrbit`, `IsLimitCycle`, `Plane`; `Vector`, `Projective`, `projectiveTopology`, `zeroLocus`, `ComplexNonsingular`, `Admissible`, `ExactComponents`, and the Mathlib notions they use: `MvPolynomial.totalDegree`, `MvPolynomial.IsHomogeneous`, `MvPolynomial.pderiv`, `MvPolynomial.eval₂`, `Projectivization`, `projectivizationSetoid`, `connectedComponentIn`, `HasDerivAt` for functions ℝ → (Fin 2 → ℝ), `Function.Periodic`) and then attack it. Concretely try to answer:

1. Is there a cheap proof of the proposition? (e.g. is the hypothesis class empty or the predicate `IsLimitCycle` unsatisfiable or nearly so; does `ExactComponents` hold trivially; does some junk value or totalized function make the bound automatic; does the topology on `Projective 3` as declared — look hard at the `projectiveTopology` instance and what topology `Projectivization ℝ (Fin 4 → ℝ)` actually gets, including whether it could be something degenerate like the indiscrete or discrete topology, and whether there is any competing instance — make component counts trivial?)
2. Is there a cheap proof of the negation? (e.g. a degenerate vector field — zero field, linear field, a field with a line or curve of singular points, a center, a field of degree ≤ 2 where `IsLimitCycle` as formalized is satisfied by infinitely many or ≥ 5 sets for a reason that is NOT a genuine limit cycle in the standard sense; a "quintic" that satisfies `Admissible 3 5 true` but is degenerate; a polynomial whose real zero locus has infinitely many "components" under the formal definition; the empty locus; `connectedComponentIn` conventions when x ∉ S; a nonsingularity condition that is weaker or stronger than smoothness of the complex projective hypersurface; `MvPolynomial.IsHomogeneous` admitting the zero polynomial; etc.)
   For A, think specifically about: (i) periodic orbits vs. their images as sets, re-parametrizations, and whether two different limit cycles can have the same image or one limit cycle can be counted twice; (ii) whether "isolated" as formalized (an open U ⊇ C containing no other entire periodic orbit as a subset) agrees with the standard notion, including for periodic orbits accumulating on C, period annuli/centers, and nested cycles; (iii) whether the solution curve γ is required to be defined for all time and whether that matters; (iv) whether degree ≤ 2 allows anything a standard "quadratic system" would exclude, and whether that could create ≥ 5 limit cycles (it should not: lower degree systems are standardly included).
   For B, think specifically about: (i) whether `zeroLocus` really is the real projective zero set; (ii) whether `ComplexNonsingular` is exactly smoothness of the complex projective hypersurface (note it quantifies over all nonzero complex vectors and uses the 4 partial derivatives); (iii) whether `ExactComponents (zeroLocus 3 p) m` is exactly "the zero locus has exactly m connected components" in the subspace topology of the standard topology of RP^3; (iv) the degree: `Admissible 3 5 true p` forces `p.IsHomogeneous 5` and p ≠ 0 — is the degree exactly 5?
3. Does either statement differ from its informal meaning in any way that changes the mathematical problem (stronger, weaker, or different)? State precisely what the Lean statement says if it differs.
4. Anything else a hostile bounty hunter would try.

You may use the pinned Lean toolchain to test hypotheses: from the directory <scratch>/h16 , the script `./compile.sh <path relative to src>` compiles a Lean file under `src/` inside Docker (offline; ~30–90 s; warnings are errors). If you want to experiment, create your OWN scratch directory by copying: `cp -r <scratch>/h16 <scratch>/h16-review-a` and work only inside that copy (another process is editing files in the original). In the copy, `./compile.sh` works the same way. Useful experiments: `#synth TopologicalSpace (HilbertDrafts.Hilbert16Classification.Projective 3)`, `#print` of instances, checking what instance is found, small lemmas testing degenerate cases. Mathlib sources are inside the image under /opt/fc-verifier (find the Mathlib directory with `find /opt/fc-verifier -maxdepth 4 -name Mathlib -type d`), readable via `docker run --rm --network none --entrypoint bash sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90 -c '...'`.

Report format:
- VERDICT for A and for B separately: one of DEFECT FOUND (with the concrete cheap proof/refutation sketch, ideally machine-checked in your scratch copy), FIDELITY ISSUE (statement differs from the informal meaning; say how and whether it matters), or NO DEFECT FOUND.
- For each attack you tried, one or two lines: what you tried and why it fails or succeeds. Be specific about definitions unfolded and Mathlib facts relied on; mark anything you verified by compiling versus reasoned on paper.
- Residual risks you could not rule out.
Do not soften findings; an honest "I could not rule out X" is more useful than reassurance.
```

## Report 1 (2026-11-15T22:08:04.373Z)

**VERDICT A (`QuadraticFourCycles`): NO DEFECT FOUND.**
**VERDICT B (`QuinticSurfaceComponents`): NO DEFECT FOUND.**

I found no fidelity issue in either: each Lean statement says what its informal meaning says. The identification rests partly on compiled lemmas and partly on textbook arguments I did not formalize; the split is marked below.

All experiments were done in my own copy; the original `h16` directory was not touched. The three scratch files compile with exit 0 under warnings-as-errors:
- `<scratch>/h16-review-a/src/HilbertDrafts/ReviewA1.lean` (instances and topology)
- `<scratch>/h16-review-a/src/HilbertDrafts/ReviewA2.lean` (fidelity lemmas)
- `<scratch>/h16-review-a/src/HilbertDrafts/ReviewA3.lean` (non-vacuity for B)

## Target A: attacks tried

- **Derivative is non-standard (compiled).** This Mathlib defines `HasDerivAt` for topological vector spaces via `HasDerivAtFilter`. For `Plane` it still reduces to componentwise derivatives (`hasDerivAt_pi`) and the usual slope limit (`hasDerivAt_iff_tendsto_slope`). Fails.
- **Evaluation or index mix-up (compiled).** For the field `⟨-X 1, X 0⟩`, the curve `t ↦ (r cos t, r sin t)` satisfies `IsPeriodicOrbit`, so `eval` and the variable indexing mean what they should. Fails.
- **A cleverly shaped `U` makes a non-isolated orbit "isolated" (compiled).** `periodicOrbit_isCompact` shows every periodic orbit is compact. `isLimitCycle_iff_tube` then shows `IsLimitCycle V C` is equivalent to "periodic orbit, and some ε-tube around C contains no other entire periodic orbit". The shape of `U` gives no freedom. Fails.
- **Centers or period annuli give infinitely many limit cycles (compiled for one case).** `center_circle_not_limitCycle` shows the unit circle of the linear center is a periodic orbit but not a limit cycle. The general case follows from the tube lemma. Fails.
- **Tube isolation is weaker than standard isolation (paper).** In the plane, a periodic orbit meeting a small neighbourhood of C crosses a transversal once, so it is a fixed point of the return map and lies wholly in the tube. The two notions agree. Fails.
- **Double counting or fake orbits (paper).** Cycles are counted as sets through an injective family. Polynomial fields have unique solutions, so a non-constant periodic solution is a genuine closed orbit avoiding singular points; the zero field has none (already in the file). Requiring γ on all of ℝ costs nothing. Fails.
- **Degree ≤ 2 admits degenerate fields with many cycles (paper).** Constant and linear fields have no isolated periodic orbits. If P and Q share a linear factor, the orbits are those of a linear field avoiding a line: either none, or an open family of ellipses, never isolated. Fails.
- **Quantifiers and hidden arguments (compiled).** `#print` with `pp.explicit` shows `CycleBound : Nat → Nat → Prop` with no auto-bound implicits. Infinite families are refuted through the "every finite injective family" form. Only the standard three axioms appear.
- **Triviality.** By the literature as I recall it, quadratic systems with 4 limit cycles exist (Shi; Chen–Wang), so the bound 3 is false. No system with 5 is known, and each quadratic system has finitely many (Bamon). Proof is H(2) ≤ 4; refutation is H(2) ≥ 5.

## Target B: attacks tried

- **Degenerate or competing topology on `Projective 3` (compiled).**
  - Mathlib at this pin has no `TopologicalSpace` instance on `Projectivization`.
  - `#synth` returns `projectiveTopology 3` for `Projective 3`, `Projectivization ℝ (Fin 4 → ℝ)` and the `Fin (3+1)` spelling.
  - `pp.explicit` shows that instance baked into `SurfaceComponentBound`.
  - By `rfl`, `projectiveTopology n` is the topology coinduced by `Projectivization.mk' ℝ` from the subtype topology on nonzero vectors, which by `rfl` is induced from the product topology. `mk'` is a quotient map.
  - This is the standard RP³, neither discrete nor indiscrete. Fails.
- **`ExactComponents` trivial or miscounting (compiled).** `exactComponents_iff_equiv` proves `ExactComponents S m ↔ Nonempty (ConnectedComponents ↥S ≃ Fin m)` for any space. `quinticSurfaceComponents_iff` restates the target in that form. The `x ∉ S` junk branch of `connectedComponentIn` is guarded by `x ∈ S`; the empty set gives m = 0 (in the file). Fails.
- **Empty hypothesis class (compiled).** `fermat_admissible` shows `∑ i, X i ^ 5` satisfies `Admissible 3 5 true`. This proof can fill the `fermatQuintic_admissible` sorry in the checks file; the definition is identical. Fails.
- **Degenerate "quintics" slip in (compiled for one case).** `X 0 ^ 5` is rejected. The zero polynomial is rejected by `p ≠ 0`, and `IsHomogeneous 5` with `p ≠ 0` forces degree exactly 5 (in the file).
- **`ComplexNonsingular` too weak or too strong (paper).** "Gradient nonzero at every nonzero complex zero of p" is exactly smoothness of the complex projective hypersurface. It rejects non-reduced and singular forms, and by Euler's identity matches the "no critical points in ℂ⁴∖0" convention. Fails.
- **`zeroLocus` is not the real projective zero set.** `mem_zeroLocus_mk_iff` (in the file) shows membership is evaluation at any representative. Fails.
- **Infinitely many components refutes it cheaply (paper).** Real points of a complex-nonsingular form are smooth points, so the locus is a compact smooth surface with finitely many components. The negation needs a quintic with at least 24 components.
- **Triviality.** By the literature as I recall it, Orevkov's quintic has 23 components, so the bound 22 is false. Smith–Thom plus Comessatti give at most 25, which I re-derived by hand. The target is exactly "the maximum is 23".

## Residual risks

1. **Paper-only steps.** Not machine-checked: tube isolation implies standard isolation; degenerate degree ≤ 2 fields have no limit cycles; the real locus of an admissible quintic has finitely many components. If the first were wrong, the formal count could only exceed the standard one, which would make A's negation easier.
2. **Literature status.** I did not independently check whether H(2) ≤ 4, H(2) ≥ 5, or the quintic maximum has been settled recently; the status above is from memory. The one downloaded file that states it (the 2023 Zegeling slides in the scratchpad) agrees for A (H(2) ≥ 4); nothing I read bears on B. A settled result would make one side a formalization exercise.
3. **Constants at the frontier.** The neighbouring statements `SurfaceComponentBound 5 25` (true) and `CycleBound 2 3`, `SurfaceComponentBound 5 22` (false) are known results; make sure none of these is published by mistake.
4. **Instance stability.** The topology is a local instance. Today it is the only one; if a later Mathlib adds its own instance on `Projectivization`, the published statement keeps `projectiveTopology 3`, but downstream lemmas could hit a diamond.
5. **Not examined.** I did not review the checker pipeline (axiom auditing, redefinition tricks in submissions).
