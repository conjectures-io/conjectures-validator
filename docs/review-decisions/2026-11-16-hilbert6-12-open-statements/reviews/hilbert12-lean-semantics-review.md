# Hilbert 12 Λ-geometric statements: adversarial Lean-semantics review

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T02:48:34.375Z)

```text
You are an adversarial reviewer for a platform that pays cash bounties for Lean 4 proofs OR refutations of formal statements of open problems. If a published statement can be proved or refuted cheaply because of a formalization defect (vacuous or unsatisfiable hypothesis, junk value, wrong quantifier, degenerate case, a definition that does not mean what its docstring says), the platform loses money and credibility. Find such a defect in the candidate statement below before publication, or report that you could not. Web access is available but the task is mainly about the Lean text.

First make your own working copy and work ONLY inside it (other processes are editing the original):
`cp -r <scratch>/h612 <scratch>/h612-review-l12`
Then read, in the copy:
- `src/HilbertDrafts/Hilbert12Lambda.lean` — the candidate. The reward target is the proposition `HilbertDrafts.Hilbert12Lambda.Hilbert12LambdaGeometric`.
- `src/HilbertDrafts/Hilbert12.lean` — earlier reviewed definitions it reuses (`Ambient`, `IsFiniteAbelian`, `IsAbelianElement`, `FixedValueCoverage`).
Ignore the `*Checks.lean` files (work in progress by someone else, may contain `sorry`). In the copy, `./compile.sh <path relative to src>` compiles a Lean file under `src/` inside Docker (Lean 4.35.0-rc2, pinned Mathlib, offline, warnings are errors, 30–120 s). Mathlib sources are inside the image at /opt/fc-verifier/vendor/formal-conjectures/.lake/packages/mathlib/Mathlib (read with `docker run --rm --network none --entrypoint bash sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90 -c '...'`). Write experiments as new files in your copy.

Intended informal meaning (from Borger and de Smit, "Explicit class field theory and the algebraic geometry of Λ-rings", arXiv:1809.02295; a text extraction is at <scratch>/bds/paper.txt — read §1, §6.1, §7.1, §8, §12.1 if useful): for a number field K with ring of integers O_K, a Λ-scheme is a flat separated O_K-scheme X of finite type with commuting O_K-endomorphisms ψ_p, one for every maximal ideal p, each restricting to the N(p)-power Frobenius on the fibre X ⊗ k(p). For an ideal a = p_1⋯p_n put ψ_a = ψ_{p_1}∘⋯∘ψ_{p_n}. For a cycle f (nonzero ideal plus a set of real places), ideals a, b are f-equivalent iff a = x·b for some x ∈ 1 + f_fin·b⁻¹ positive at the real places of f (their Proposition 4.3). A geometric point x ∈ X(K̄) is f-periodic if ψ_a(x) = ψ_b(x) whenever a ∼_f b. K(X) is the field generated over K by the coordinates of the f-periodic points for all f. The target says: for EVERY number field K there is one such X with K^ab ⊆ K(X) (and, redundantly, all those coordinates abelian over K). It is known to be true for K = Q (X = G_m with ψ_p = p-th power; the periodic points for the cycle (n)·∞ are the n-th roots of unity) and for imaginary quadratic K (a Lattès structure on P^1 over the Hilbert class field ring, viewed over O_K), and open for every other number field. Both a proof and a refutation are meant to be hard.

Attack it. Concretely:
1. Unfold every definition (`Base`, `IntegralScheme`, `scalar`, `OverPrime`, `IsFrobeniusLift`, `LambdaStructure`, `psiList`, `idealOfList`, `Cycle`, `Cycle.Equivalent`, `GeometricPoint`, `IsPeriodic`, `value`, `periodicValues`, `LambdaGeometric`, `FixedValueCoverage`) down to Mathlib notions (`AlgebraicGeometry.Scheme`, `Spec`, `Scheme.Hom.appLE`, `Scheme.ΓSpecIso`, `Scheme.basicOpen`, `IsAffineOpen`, `Flat`, `IsSeparated`, `LocallyOfFiniteType`, `QuasiCompact`, `Ideal.absNorm`, `IsDedekindDomain.HeightOneSpectrum`) and check each means what the docstring says. Check universe levels and implicit arguments with `#print`/`pp.explicit`.
2. CHEAP PROOF of the target? Look for anything that makes `LambdaGeometric K` easy for all K: is the Frobenius condition accidentally vacuous or weak (e.g. does `OverPrime` pick out the right fibre; is the congruence clause quantified so that it says nothing; does `Ideal.map (M.scalar V) p.asIdeal` equal the whole ring more often than intended; does `scalar` really send an integer to the corresponding function; could `ψ = 𝟙` qualify on a positive-dimensional scheme)? Can a non-finite-type object slip through (`LocallyOfFiniteType` + `QuasiCompact`; infinite disjoint unions; non-noetherian tricks)? Can `periodicValues` be made all of K^ab by a degenerate choice (empty fibres over some or all primes, a scheme living only over the generic point or over O_K[1/n], a non-reduced or non-separated trick, a cycle chosen so that `Cycle.Equivalent` is trivial/empty so every point is periodic, lists of primes with repetitions, the unit ideal)? Is `Cycle.Equivalent` really the Proposition 4.3 relation (check on K = Q by hand: with the real place, (k) ∼ (l) for positive integers should mean k ≡ l mod n; without it, k ≡ ±l)? Does `value` really evaluate a function at the point?
3. CHEAP REFUTATION of the target? The universal statement is false as soon as it fails for one number field, in particular it would be cheaply refutable if the formal conditions are impossible to satisfy even for K = Q where the informal statement is a theorem. Check whether G_m over Z with ψ_p(t) = t^p plausibly satisfies every formal clause: the points clause (ψ fixes each point of the fibre over p), the congruence clause for ALL opens U and ALL affine opens V ⊆ U ∩ ψ⁻¹U (including V not meeting the fibre, V not contained in a distinguished open, U not affine), `GeometricPoint` compatibility, periodic points = roots of unity, `FixedValueCoverage` (all coordinate values abelian; every finite abelian extension of Q contained in the field they generate — Kronecker–Weber). Also consider whether for imaginary quadratic K the known construction (X = P^1 over O_H regarded as an O_K-scheme, non-affine, geometrically disconnected over K when h_K > 1) fits the formal class. Any clause that is stronger than the paper's definition is a refutation risk; any clause weaker is a cheap-proof risk.
4. Does the formal statement differ from the informal one in a way that changes the mathematical problem? Say precisely how.
5. Anything else a hostile bounty hunter would try.

Report format: VERDICT — one of DEFECT FOUND (give the concrete cheap proof/refutation, ideally machine-checked in your copy), FIDELITY ISSUE (statement differs from the informal meaning; say how and whether it matters), or NO DEFECT FOUND. Then one or two lines per attack: what you tried, why it fails or succeeds, and whether you verified it by compiling or on paper. Then residual risks you could not rule out. Do not soften findings.
```

## Brief 2 (2026-11-16T03:17:42.156Z)

```text
The coordinator sent a message while you were working:
Follow-up delta review of the final Hilbert 12 Λ file, which changed in two ways since you reviewed it: docstrings were reworded, and one new proposition was added at the end. No existing definition body was changed.
Please (1) refresh ONLY the candidate in your own copy: `cp <scratch>/h612/src/HilbertDrafts/Hilbert12Lambda.lean <scratch>/h612-review-l12/src/HilbertDrafts/Hilbert12Lambda.lean`, recompile it and then recompile every one of your `src/Review/*.lean` attack files against it, and tell me whether they all still build (list any that fail and why). Confirm with a diff against your earlier copy that no definition body changed apart from comments/docstrings and the appended definition.
(2) Attack the new proposition, which will be a second reward target (proof and refutation both offered):
```
def BorgerDeSmitQuestion4 : Prop :=
  ∃ (K : Type) (_ : Field K) (_ : NumberField K),
    Module.finrank ℚ K ≠ 1 ∧ ¬ (Module.finrank ℚ K = 2 ∧ IsEmpty (K →+* ℝ)) ∧
      LambdaGeometric K
```
Intended meaning: Question 4 of Borger–de Smit — there is a number field other than Q and the imaginary quadratic fields whose maximal abelian extension is Λ-geometric. Check: do the two side conditions exclude exactly Q and the imaginary quadratic fields (which `Module ℚ K` instance does `Module.finrank ℚ K` use — is it forced to be the canonical one given `Field K` and `NumberField K`, or could a cheater choose the instances `(_ : Field K) (_ : NumberField K)` so that the excluded cases slip through, e.g. a field isomorphic to Q or to an imaginary quadratic field presented with a different ℚ-module structure, a degree-2 field with a ring hom to ℝ that is really imaginary, or a non-standard `Field` structure on a type)? Are the instance binders in the existential actually used by `LambdaGeometric K`, `Module.finrank ℚ K` and `K →+* ℝ` (check with `pp.explicit`)? Is there any cheap proof (a degenerate K) or cheap refutation? Is the statement the affirmative answer to the paper's Question 4 and nothing more?
Report a verdict for the new proposition (DEFECT FOUND / FIDELITY ISSUE / NO DEFECT FOUND) with what you compiled versus argued on paper, plus the build status of your earlier attack files against the final candidate. Work only in your own copy.

Address this before completing your current task.
```

## Report 1 (2026-11-16T03:16:09.658Z)

**VERDICT: NO DEFECT FOUND**

I found no cheap proof and no cheap refutation. Every definition unfolds to what its docstring says, and the formal statement matches the stated informal meaning. The main residual exposure is the "for every K" quantifier, which makes a refutation need only one field (see residual risks).

All experiments are in `<scratch>/h612-review-l12/src/Review/`. Everything marked "compiled" below builds with warnings-as-errors and uses only `propext`, `Classical.choice`, `Quot.sound`.

**1. Unfolding and universes**
- **Universes and implicits** (`#print`, `pp.universes`/`pp.explicit` in `Print.lean`): everything is in universe 0, `IntegralScheme K : Type 1`, no stray implicit arguments. Compiled.
- **`OverPrime`**: `overPrime_iff` shows it holds exactly when the image of `x` in the base has ideal `p.asIdeal`, so it picks the right fibre. Compiled.
- **`scalar`**: `scalar_res` shows it commutes with restriction; `scalar_top_eq` shows it is the pullback of the integer. Compiled.
- **`value`**: `value_affine` shows that on `Spec R` it is the ring map applied to the function. Compiled.
- **`Cycle.Equivalent`**: reflexive and monotone in the cycle. Compiled.
- **`Cycle.Equivalent` over ℚ** (modulus `(n)`): `k ≡ l mod n` gives `(k) ∼ (l)`; with the real place, `(k) ∼ (l)` forces `k ≡ l mod n`; without it, `k ≡ −l` also gives equivalence. Compiled (`RatCycle.lean`).
- **Proposition 4.3**: I re-derived on paper that the formal relation matches it, including the non-coprime cases.

**2. Cheap proof attempts — all fail**
- **Frobenius clause vacuous or weak?** No. The ideal generated by `p` in `Γ(V)` is proper whenever `V` contains a fibre point, and is the unit ideal for affine `V` missing the fibre. Compiled (`map_ne_top_of_overPrime`, `map_eq_top_of_forall_not_overPrime`).
- **Formal clause weaker than the paper's?** No. The points clause forces `ψ(x) = x`, so every fibre point has an affine `V ⊆ U ∩ ψ⁻¹U`, and the congruence then gives `ψ ≡ Frobenius` on stalks mod `p`. On paper.
- **`ψ = 𝟙` on a positive-dimensional scheme?** No. It would force `s^q = s` on `Γ(V)/p`, so zero-dimensional fibres; a flat finite-type scheme with a positive-dimensional component has positive-dimensional fibres over almost all primes. On paper.
- **Scheme over the generic point only, or over `O_K[1/N]`?** No finite-type flat scheme has image `{η}` (the image is open). Over `O_K[1/N]` only finitely many `ψ_p` are unconstrained, which the paper also allows, and Theorem 8.4 still applies with the remaining primes. On paper.
- **Non-finite type, infinite unions, zero-dimensional tricks?** Quasi-compact plus locally finite type is finite type. A quasi-finite `X` has finitely many geometric points, so `K(X)` is a finite extension. On paper.
- **Empty scheme, non-reduced, non-separated?** Empty gives no points. Passing to `X_red` preserves flatness, the `ψ_p` and the values. Separatedness is required. On paper.
- **Degenerate cycle making everything periodic?** No. `finite ≠ ⊥` and every cycle has a finite quotient monoid; for example `f ∼ x·f` for all admissible `x`. Repeated primes and the empty list (unit ideal, `ψ = 𝟙`) behave correctly. On paper; the list plumbing is compiled in `linePoint_psiList`.
- **Known constructions for general K?** Norm-twisted `G_m` and tori split by abelian extensions give only `K·ℚ^ab·(finite)`; the CM method gives Shimura's proper subextension. On paper.

**3. Cheap refutation attempts — all fail**
- **Formal conditions unsatisfiable for standard examples?** No. `specScheme_isFrobeniusLift` shows that for any flat finite-type `O_K`-algebra `R` and endomorphism `φ` with `φ(f) ≡ f^{N(p)} mod pR`, `Spec φ` satisfies every clause of `IsFrobeniusLift`, for all `U` and all affine `V ⊆ U ∩ ψ⁻¹U`. Compiled.
- **Positive-dimensional Λ-structure exists for every K?** Yes: `toricLine K`, the affine line over `𝓞 K` with `t ↦ t^{N(p)}`, is a `LambdaStructure`. Compiled.
- **Periodic points of the toric line**: `ψ_L` sends the point `z` to `z^{N(idealOfList L)}`, the coordinate value is `z`, and `IsPeriodic` holds iff `z^{N(a)} = z^{N(b)}` whenever `a ∼_f b`. Compiled (`Points.lean`).
- **K = ℚ**: combining the above, the periodic points are 0 and the roots of unity, so the statement holds for ℚ. The last steps (`N((k)) = k`, every `GeometricPoint` is of this form, values lie in ℚ(z), Kronecker–Weber) are on paper; Kronecker–Weber is not in Mathlib.
- **Imaginary quadratic K**: `P¹` over `O_H` viewed over `O_K` fits the formal class. It is flat, separated, finite type and non-affine; `ψ_p` is `O_K`-linear only; each prime above `p` is fixed because `H/K` is abelian and unramified. On paper; I did not re-check the paper's Lattès theorem.
- **Uninhabited structure?** No: the empty scheme, `(Base, 𝟙)` and the toric line all carry Λ-structures.

**4. Differences from the informal statement**
- The target is "for all K", whereas the paper's Question 4 asks whether any field other than ℚ and imaginary quadratic ones has the property. Your brief already states the "for all" form, so this is not a mismatch, but it matters for risk.
- The first clause of `FixedValueCoverage` (all values abelian) is redundant by Theorem 8.4 and is never the failing clause for a refuter.
- Otherwise the formal statement matches the paper: lists of primes for ideals, all maximal ideals, cycles as nonzero ideal plus set of real embeddings, coordinates as values of all local functions.

**5. Other**
- A web search found no resolution of Question 4 for any number field. A 2022 ANU thesis (Cheng) proves the function-field analogue via Drinfeld modules; `NumberField K` excludes it.

**Residual risks**
1. **One-field refutation.** The negation of the target is "some number field is not Λ-geometric", which is strictly weaker than a negative answer to Question 4. A hunter may pick the most convenient field, for example a real quadratic one. I could not find a cheap obstruction (counting, local conditions and Leopoldt-type rank arguments give no contradiction), and it would seem to need a classification of finite-type schemes with commuting Frobenius lifts, which is open even over ℤ. But if the statement is false, the bounty is effectively for exactly this, and a specialist argument for a single field cannot be excluded.
2. **Not compiled:**
   - the converse direction of the Frobenius equivalence;
   - any non-affine example;
   - the full `LambdaGeometric ℚ`;
   - the Lattès case.
3. **Primes with empty fibre** leave `ψ_p` unconstrained apart from commuting. This is the paper's own convention and I see no way to exploit it.

## Report 2 (2026-11-16T03:31:21.013Z)

**VERDICT for `BorgerDeSmitQuestion4`: NO DEFECT FOUND.** It is the affirmative answer to the paper's Question 4 and nothing more. All earlier attack files still build against the final candidate.

## (1) Delta and build status

- **Candidate refreshed.** The final file in my copy has sha256 `d53136df…f6a9`, identical to the original at the time of the check. The earlier reviewed version (`8fc34021…8cea`) is kept as `Hilbert12Lambda.reviewed-v1.lean` in my copy.
- **Diff.** With all comments and docstrings stripped, the only difference is the four appended lines of `BorgerDeSmitQuestion4`. No existing definition body changed. The other changes are the module header and the docstrings of `LambdaGeometric` and `Hilbert12LambdaGeometric`. `Hilbert12.lean` in my copy is byte-identical to the original.
- **Builds.** The candidate compiles (exit 0, no output). Every earlier attack file compiles against it with exit 0: `Sanity1`, `Affine`, `Affine2`, `Line`, `Points`, `RatCycle`, `All`, `Print`. None fail. A deliberately false file gives exit 1, so the exit codes are meaningful.
- **Docstring spot-check.** The quoted abstract sentence, the Question 4 wording, and the references to Theorem 8.4 and Proposition 7.3 match the paper text.

## (2) Attack on `BorgerDeSmitQuestion4`

Compiled, in `src/Review/Q4.lean`, `Q4b.lean`, `Q4Print.lean`, `Q4Print2.lean` (standard axioms only):

- **Parse.** The body is `finrank ≠ 1 ∧ (¬(finrank = 2 ∧ IsEmpty (K →+* ℝ)) ∧ LambdaGeometric K)`. The negation covers only the imaginary-quadratic clause.
- **Binders are used.** With `pp.explicit` and `pp.proofs`:
  - `NumberField K` is `@NumberField K i`.
  - `LambdaGeometric K` is `@LambdaGeometric K i j`.
  - `K →+* ℝ` uses the semiring from `i`.
  - Both occurrences of `Module.finrank ℚ K` use `Algebra.toModule (DivisionRing.toRatAlgebra K (Field.toDivisionRing K i) (NumberField.to_charZero K i j))`.
  - There is no free `Module ℚ K` or `Algebra ℚ K` binder to choose.
- **The degree cannot be manipulated.**
  - `finrank_any_module`: any `Module ℚ K` structure on the field's additive group gives the same degree.
  - `finrank_pos'`: the degree is positive, so `≠ 1` means degree at least 2 with no junk zero.
  - `finrank_congr`: the degree is invariant under ring isomorphism.
- **First condition excludes exactly ℚ.** `finrank_eq_one_iff`: degree 1 holds iff `Nonempty (K ≃+* ℚ)`. `iso_rat_excluded`: every presentation of a field isomorphic to ℚ fails the condition.
- **Second condition excludes exactly degree 2 with no real place.**
  - `isEmpty_real_iff`: `IsEmpty (K →+* ℝ)` holds iff no infinite place is real, so a "really imaginary" field with a hom to ℝ cannot exist.
  - `isEmpty_real_congr` and `iso_imagQuad_excluded`: the condition is isomorphism-invariant, so re-presenting an excluded field does not help.
  - `gaussian_excluded`: ℚ(i), as `CyclotomicField 4 ℚ`, has degree 2 and no real hom.
- **Target restated.** `q4_iff`: the target is equivalent to the same statement with "not isomorphic to ℚ" and "not (degree 2 and no real place)". This confirms the lemmas apply to the exact instances in the target.
- **Not vacuous.** `side_conditions_satisfiable`: `CyclotomicField 5 ℚ` (degree 4) meets both side conditions. A refuter therefore has real fields to rule out.

Argued on paper:

- **Cheap proof: none.** A witness must be a genuine number field of degree at least 2 that is not imaginary quadratic, and `LambdaGeometric` for any such field is the open problem. Base change from ℚ or an imaginary quadratic F gives only K·F^ab, which has infinite index in K^ab.
- **Cheap refutation: none.** It requires non-Λ-geometricity for every non-excluded field, which is the full negative answer to Question 4 and strictly harder than refuting the universal target.
- "Degree 2 with no real embedding" equals "imaginary quadratic" is the classical fact; I compiled only the link to Mathlib's real places and the Gaussian example.
- Isomorphism-invariance of `LambdaGeometric` itself is not compiled. It is not needed for the exclusions, which hold regardless.

## Things to know (not defects)

1. **The two rewards are coupled.** `h12_imp_q4` and `not_q4_imp_not_h12` compile in about 40 lines. A proof of the universal target collects the Question 4 proof reward almost for free, and a refutation of Question 4 collects the universal refutation reward the same way. In every consistent outcome exactly two of the four rewards are payable.
2. **The redundant "values are abelian" clause** rests on Theorem 8.4 of a preprint. If that theorem were wrong, the formal statement would be stronger than the literal Question 4, never weaker, so this is not a cheap-proof risk.
3. Everything from my earlier review of `LambdaGeometric` carries over unchanged, including the one-field refutation exposure of the universal target, which the new header now states.

Everything is in `<scratch>/h612-review-l12/`; build logs are `build_*.log` there.
