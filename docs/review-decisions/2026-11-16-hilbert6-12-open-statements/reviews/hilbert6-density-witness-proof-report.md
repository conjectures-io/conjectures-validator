# Hilbert 6 admissible-density witness: proof report

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T02:47:56.451Z)

```text
You are completing one Lean 4 / Mathlib proof. Pure proof engineering; no web access is needed.

## Files
Working directory (absolute): <scratch>/h612
- `src/HilbertDrafts/Hilbert6.lean` and `src/HilbertDrafts/Hilbert6MeanField.lean` — definitions. DO NOT EDIT them (other processes are reading them).
- `src/HilbertDrafts/Hilbert6MeanFieldChecks.lean` — the ONLY file you edit. It contains one theorem ending in `sorry`:
  `theorem exists_smoothDensity : ∃ f₀ : Phase → ℝ, SmoothDensity f₀`
Read the two definition files first. `Phase` is `EuclideanSpace ℝ (Fin 3) × EuclideanSpace ℝ (Fin 3)` and
`SmoothDensity f₀ := (∀ n : ℕ, ContDiff ℝ n f₀) ∧ HasCompactSupport f₀ ∧ (∀ z, 0 ≤ f₀ z) ∧ Integrable f₀ ∧ ∫ z, f₀ z = 1`.

## How to compile
From the working directory: `./compile.sh HilbertDrafts/Hilbert6MeanFieldChecks.lean` (Lean 4.35.0-rc2 with a pinned Mathlib inside Docker, offline, warnings are errors, 30–120 s per run). Exit code 0 and no output means success. Dependency `.olean` files already exist; if you see "object file ... does not exist", recompile `HilbertDrafts/Hilbert6.lean` then `HilbertDrafts/Hilbert6MeanField.lean` in that order. Warnings are errors, so fix unused variables/simp arguments and deprecated names rather than silencing linters; do not add `set_option` lines unless unavoidable, and tell me if you did.
Mathlib sources are inside the image at `/opt/fc-verifier/vendor/formal-conjectures/.lake/packages/mathlib/Mathlib` — search with e.g. `docker run --rm --network none --entrypoint bash sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90 -c 'cd /opt/fc-verifier/vendor/formal-conjectures/.lake/packages/mathlib/Mathlib && grep -rn "integral_normed" Analysis | head'`. You may create scratch files under `src/` with names starting `ScratchB` and must delete them when finished. Do not touch any other file in that directory (another agent is editing `Hilbert12LambdaChecks.lean` and using `ScratchA*` names).

## Guidance
Use a normalized smooth bump function: `ContDiffBump` (file `Analysis/Calculus/BumpFunction/Basic.lean` and `.../Normed.lean`) on `E = Phase`, centred at `0`. Mathlib provides `ContDiffBump.normed μ`, with lemmas along the lines of `ContDiffBump.contDiff_normed`, `ContDiffBump.hasCompactSupport_normed`, `ContDiffBump.nonneg_normed`, `ContDiffBump.integrable_normed`, `ContDiffBump.integral_normed`. `Phase` is a finite-dimensional real normed space, so `HasContDiffBump Phase` should be found by instance search (via `FiniteDimensional`); `Nonempty (ContDiffBump (0 : Phase))` is available. The measure is `volume` on the product space; check that the required instances (`MeasurableSpace`, `BorelSpace`, `IsLocallyFiniteMeasure`, `IsOpenPosMeasure`) are found for `Phase`. Smoothness of every finite order follows from `ContDiff ℝ ∞` (or whatever top order the lemma states) by `ContDiff.of_le` with the appropriate order comparison in this Mathlib version (orders live in `WithTop ℕ∞`).

## Hard requirements
- The theorem statement must remain exactly as written. Helper lemmas may be added above it inside the namespace.
- No `sorry`, `admit`, new `axiom`, `native_decide`, `unsafe`, `implemented_by`.
- When done, compile a scratch file importing `HilbertDrafts.Hilbert6MeanFieldChecks` containing `#print axioms HilbertDrafts.Hilbert6MeanFieldChecks.exists_smoothDensity`; only `propext`, `Classical.choice`, `Quot.sound` are permitted. Report the output verbatim.

## Report back
The final compile command and result, the verbatim `#print axioms` output, any deviation, and plainly whether the theorem is fully proved (if not, leave it as `sorry` and say so; do not alter the statement).
```

## Report 1 (2026-11-16T02:48:53.790Z)

`exists_smoothDensity` is fully proved, with the statement unchanged and no `sorry`.

**Final compile**

- Command, run from `<scratch>/h612`: `./compile.sh HilbertDrafts/Hilbert6MeanFieldChecks.lean`
- Result: exit code 0, no output.

**`#print axioms` output, verbatim**

```
'HilbertDrafts.Hilbert6MeanFieldChecks.exists_smoothDensity' depends on axioms: [propext, Classical.choice, Quot.sound]
```

**Proof**

The witness is the default bump function centred at `0 : Phase`, normalized against `volume`, as you suggested:

```lean
theorem exists_smoothDensity : ∃ f₀ : Phase → ℝ, SmoothDensity f₀ := by
  let φ : ContDiffBump (0 : Phase) := default
  refine ⟨φ.normed volume, fun n => ?_, φ.hasCompactSupport_normed, fun z => φ.nonneg_normed z,
    φ.integrable_normed, φ.integral_normed⟩
  exact_mod_cast φ.contDiff_normed (μ := volume) (n := (n : ℕ∞))
```

**Deviations**

- None from the hard requirements: no helper lemmas, no `set_option`, no new axioms, and only `src/HilbertDrafts/Hilbert6MeanFieldChecks.lean` was edited.
- One small difference from the guidance: `ContDiff.of_le` was not needed. In this Mathlib, `ContDiffBump.contDiff_normed` is stated for any `n : ℕ∞`, so it is instantiated at `(n : ℕ∞)` and the cast is closed by `exact_mod_cast`.
- All required instances on `Phase` were found by instance search.
- The scratch file `src/ScratchBAxioms.lean` and its `.olean` were deleted. The compile left `src/HilbertDrafts/Hilbert6MeanFieldChecks.olean` in place as the normal build output.
