# Question 4 follows from the universal form: proof report

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T03:17:30.324Z)

```text
You are completing one Lean 4 / Mathlib proof. Pure proof engineering; no web access needed.

Working directory (absolute): <scratch>/h612
- `src/HilbertDrafts/Hilbert12.lean` and `src/HilbertDrafts/Hilbert12Lambda.lean` — definitions. DO NOT EDIT.
- `src/HilbertDrafts/Hilbert12LambdaChecks.lean` — the ONLY file you edit. It compiles except for its last theorem, which ends in `sorry`:
```
theorem question4_of_universal (h : Hilbert12LambdaGeometric) : BorgerDeSmitQuestion4 := by
  sorry
```
where (see `Hilbert12Lambda.lean`)
```
def Hilbert12LambdaGeometric : Prop := ∀ (K : Type) [Field K] [NumberField K], LambdaGeometric K
def BorgerDeSmitQuestion4 : Prop :=
  ∃ (K : Type) (_ : Field K) (_ : NumberField K),
    Module.finrank ℚ K ≠ 1 ∧ ¬ (Module.finrank ℚ K = 2 ∧ IsEmpty (K →+* ℝ)) ∧ LambdaGeometric K
```
So you must exhibit one concrete number field `K : Type` whose degree over ℚ is neither 1 nor 2 (degree ≥ 3 suffices and avoids talking about real embeddings), and apply `h K`.

Suggested witness: `CyclotomicField 5 ℚ` (degree φ(5) = 4) or `CyclotomicField 7 ℚ` (degree 6). Mathlib has `Field` and `NumberField` instances for `CyclotomicField n ℚ`, `IsCyclotomicExtension {n} ℚ (CyclotomicField n ℚ)`, and a finrank formula: `IsCyclotomicExtension.finrank` (requires irreducibility of the cyclotomic polynomial over ℚ: `Polynomial.cyclotomic.irreducible_rat`) giving `Module.finrank ℚ L = Nat.totient n`. Check the exact names and whether `n` is a `ℕ` or `ℕ+` and whether a `NeZero` instance is needed in this Mathlib version by searching the sources inside the image:
`docker run --rm --network none --entrypoint bash sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90 -c 'cd /opt/fc-verifier/vendor/formal-conjectures/.lake/packages/mathlib/Mathlib && grep -rn "theorem finrank" NumberTheory/Cyclotomic | head'`
Alternative witnesses if that is awkward: `AdjoinRoot (X ^ 3 - 2 : ℚ[X])` with irreducibility via Eisenstein, or any other degree ≥ 3 number field already convenient in Mathlib. Make sure the `Module ℚ K` instance used by `Module.finrank ℚ K` in the goal is the one your finrank lemma talks about (the canonical ℚ-algebra structure of a characteristic-zero field; use `Subsingleton.elim` on `Algebra ℚ K` / `convert` if an instance mismatch appears).

How to compile: from the working directory, `./compile.sh HilbertDrafts/Hilbert12LambdaChecks.lean` (Lean 4.35.0-rc2, pinned Mathlib, Docker, offline, warnings are errors, ~60–120 s). Exit code 0 with no output is success. If you see "object file ... does not exist" for a dependency, recompile `HilbertDrafts/Hilbert12.lean` then `HilbertDrafts/Hilbert12Lambda.lean` first. You may create scratch files under `src/` named `ScratchC*.lean` and must delete them afterwards. Do not modify anything else in the file besides replacing that one `sorry` (you may add helper lemmas directly above the theorem, inside the namespace). Do not touch other files.

Hard requirements: the theorem statement stays exactly as written; no `sorry`, `admit`, new `axiom`, `native_decide`, `unsafe`; no `set_option` unless unavoidable (tell me). When done, compile a scratch file importing `HilbertDrafts.Hilbert12LambdaChecks` with `#print axioms HilbertDrafts.Hilbert12LambdaChecks.question4_of_universal` and report the output verbatim (only `propext`, `Classical.choice`, `Quot.sound` allowed).

Report: final compile command and result, the witness field you used, the verbatim axioms output, and plainly whether it is fully proved (if not, leave the `sorry` and say so; do not change the statement).
```

## Report 1 (2026-11-16T03:20:14.822Z)

`question4_of_universal` is fully proved: the `sorry` is gone, the statement is unchanged, and the file compiles cleanly.

**Final compile command and result**

From `<scratch>/h612`:

`./compile.sh HilbertDrafts/Hilbert12LambdaChecks.lean` gave exit code 0 with no output (warnings are errors).

**Witness field**

`CyclotomicField 5 ℚ`, of degree φ(5) = 4 over ℚ. Degree 4 rules out both degree 1 and degree 2, so real embeddings never come up, and the last conjunct is `h _`.

**Axioms output (verbatim)**

```
'HilbertDrafts.Hilbert12LambdaChecks.question4_of_universal' depends on axioms: [propext, Classical.choice, Quot.sound]
```

**What was changed**

Only `<scratch>/h612/src/HilbertDrafts/Hilbert12LambdaChecks.lean` was edited:

- **Helper lemma:** I added `finrank_cyclotomicField_five : Module.finrank ℚ (CyclotomicField 5 ℚ) = 4` directly above the theorem, inside the namespace.
- **Theorem body:** the `sorry` is replaced by a short proof using that lemma.

There is no `sorry`, `admit`, new `axiom`, `native_decide`, `unsafe` or `set_option` in the file. The scratch file `src/ScratchC1.lean` and its `.olean` are deleted.

**One thing to know about the instances**

Typeclass search does not find `IsCyclotomicExtension {5} ℚ (CyclotomicField 5 ℚ)` on its own in this Mathlib version. The `Algebra ℚ _` instance it picks is `DivisionRing.toRatAlgebra`, which does not unify with `CyclotomicField.algebra` at instance transparency, so `IsCyclotomicExtension.Rat.finrank` fails directly. The helper lemma therefore calls `IsCyclotomicExtension.finrank` with `CyclotomicField.algebra 5 ℚ` and `CyclotomicField.isCyclotomicExtension 5 ℚ` passed explicitly, plus `Polynomial.cyclotomic.irreducible_rat`. The result closes the canonical-instance goal by plain `exact`, so no `Subsingleton.elim` or `convert` was needed.
