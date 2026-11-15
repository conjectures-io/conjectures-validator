# Non-vacuity witness proofs

Reviewer model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-15T21:54:56.736Z)

```text
You are completing three Lean 4 / Mathlib proofs in an existing file. This is a pure proof-engineering task; no web access is needed.

## Files
Working directory (absolute): <scratch>/h16

- `src/HilbertDrafts/Hilbert16.lean` — reviewed definitions. DO NOT EDIT.
- `src/HilbertDrafts/Hilbert16Open.lean` — statement module. DO NOT EDIT.
- `src/HilbertDrafts/Hilbert16OpenChecks.lean` — the file you edit. It contains three theorems ending in `sorry`:
  1. `fermatQuintic_admissible : Admissible 3 5 true fermatQuintic`
  2. `unitCircle_isLimitCycle : IsLimitCycle cubicField unitCircle`
  3. `not_cycleBound_three_zero : ¬ CycleBound 3 0`
Read all three files first so you know the exact definitions (`Admissible`, `ComplexNonsingular`, `IsPeriodicOrbit`, `IsLimitCycle`, `PolynomialVectorField.eval`, `DegreeAtMost`, `CycleBound`).

## How to compile
`./compile.sh HilbertDrafts/Hilbert16OpenChecks.lean` (run from that working directory; it runs Lean 4.35.0-rc2 with a pinned Mathlib inside Docker, offline, with warnings treated as errors; a compile takes roughly 30-90 seconds). Exit code 0 and no output means success. The two dependency modules are already compiled (`.olean` files exist next to them); if you ever see an "object file ... does not exist" error for them, recompile them in order with `./compile.sh HilbertDrafts/Hilbert16.lean` then `./compile.sh HilbertDrafts/Hilbert16Open.lean`.
Because warnings are errors, unused variables, unused simp arguments, deprecated lemma names and similar linter warnings all fail the build — fix them rather than silencing them (do not add `set_option` lines that disable linters unless there is truly no alternative, and tell me if you did).
To look up Mathlib lemma names you can grep the Mathlib sources inside the image, e.g.
`docker run --rm --network none --entrypoint bash sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90 -c 'cd /opt/fc-verifier && grep -rn "theorem pderiv_X" .lake/packages/mathlib/Mathlib | head'`
(first locate the Mathlib directory with a quick `find /opt/fc-verifier -maxdepth 4 -name Mathlib -type d` in the image if that path is wrong). You can also write scratch `.lean` files under `src/` (e.g. `src/Scratch.lean`) and compile them the same way to test with `#check`, `exact?`, etc.; delete scratch files when finished.

## Hard requirements
- The three theorem STATEMENTS and the three definitions (`fermatQuintic`, `cubicField`, `unitCircle`) must remain exactly as written. You may add helper lemmas/defs (inside the same namespace, above their use). 
- No `sorry`, no `admit`, no new `axiom`, no `native_decide`, no `implemented_by`/`unsafe`. At the end add nothing else to the file, but verify the axioms by compiling a scratch file containing `import HilbertDrafts.Hilbert16OpenChecks` and `#print axioms` for the three theorems; the only permitted axioms are `propext`, `Classical.choice`, `Quot.sound`. (`#print axioms` output is informational, not a warning.) Report that output verbatim.
- Keep the docstrings on the three theorems.

## Mathematical guidance
1. Fermat quintic: `Admissible n d smooth p` unfolds to `p ≠ 0 ∧ p.IsHomogeneous d ∧ (smooth = true → ComplexNonsingular n p)`. Nonzero: evaluate at a point (e.g. the constant-1 vector gives 4) or use a coefficient. Homogeneous: sum of `X i ^ 5`, each homogeneous of degree 5 (`MvPolynomial.isHomogeneous_X`, `.pow`, `MvPolynomial.IsHomogeneous.sum`). Nonsingular: `pderiv i` of the sum is `5 * X i ^ 4` (C-multiple); if all complex evaluations of the partials vanish at `z` then every `z i ^ 4 = 0`, so `z = 0`, contradicting `z ≠ 0`. So for `z ≠ 0` pick `i` with `z i ≠ 0` and show the i-th partial evaluates to `5 * z i ^ 4 ≠ 0`.
2. Unit circle is a limit cycle of the cubic field. Periodic orbit: take `γ t = ![Real.cos t, Real.sin t]`, period `2 * Real.pi`; on the circle the field evaluates to `![-sin t, cos t]`. For the isolating neighbourhood take `U = {z | 0 < z 0 ^ 2 + z 1 ^ 2}` (open, contains the circle). Suppose `C'` is a periodic orbit inside `U`, with solution `η` of period `T`. Let `f t = (η t 0)^2 + (η t 1)^2`. Then `f' = 2 f (1 - f)`, `f` is continuous and periodic, so it attains a global maximum and a global minimum (compactness of `[0, T]` plus periodicity; Mathlib has `Function.Periodic` lemmas such as `Function.Periodic.exists_mem_Ico₀` / `Function.Periodic.image_Icc` or similar, or argue via `IsCompact.exists_isMaxOn` on `Set.Icc 0 T` and reduce any `t` modulo `T`). At an extremum the derivative vanishes (`IsLocalMax.hasDerivAt_eq_zero` / `IsLocalMin.hasDerivAt_eq_zero`), and `f > 0`, so the extreme values are both 1, hence `f ≡ 1` and `C' ⊆ unitCircle`. On the circle the equation reduces to `x' = -y, y' = x`; the functions `u t = x t * cos t + y t * sin t` and `v t = -x t * sin t + y t * cos t` have zero derivative, hence are constant (`is_const_of_deriv_eq_zero`), which gives `η t` = rotation by angle `t` of `η 0`. Finally every point of the unit circle is a rotation of `η 0` (work in `ℂ`: unit complex numbers are `Complex.exp (θ * I)`; or use `Real.cos`/`Real.sin` angle-existence lemmas), so `Set.range η = unitCircle`, which gives `C' = unitCircle`.
   You are free to choose any other correct route (e.g. a different neighbourhood, or working with complex numbers throughout) as long as the statement is unchanged.
3. `¬ CycleBound 3 0`: `cubicField.DegreeAtMost 3` (total degrees are at most 3 — prove via `MvPolynomial.totalDegree_add`, `totalDegree_mul`, `totalDegree_X`, etc., or by bounding with `le_trans`), then apply the bound to the one-element family `fun _ : Fin 1 => unitCircle` to get `1 ≤ 0`.

## Report back
- Whether all three theorems now compile with exit code 0 under `./compile.sh HilbertDrafts/Hilbert16OpenChecks.lean` (state the final command and its result).
- The verbatim `#print axioms` output for the three theorems.
- Any deviation from the requirements, any `set_option` added, and anything you could not finish (if a theorem remains unproved, leave it as `sorry` and say so plainly — do not weaken or alter its statement to make it pass).
```

## Report 1 (2026-11-15T22:01:08.541Z)

All three theorems are proved, and the file compiles cleanly with warnings as errors.

**Final command and result**

`./compile.sh HilbertDrafts/Hilbert16OpenChecks.lean`, run from `<scratch>/h16`, gave exit code 0 and no output.

**`#print axioms` output (verbatim)**

```
'HilbertDrafts.Hilbert16OpenChecks.fermatQuintic_admissible' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert16OpenChecks.unitCircle_isLimitCycle' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert16OpenChecks.not_cycleBound_three_zero' depends on axioms: [propext, Classical.choice, Quot.sound]
```

The same scratch file also confirmed by `#check` with type ascription that the three theorems still have their exact original statements.

**Requirements check**

- **Statements and definitions:** the three theorem statements, their docstrings, and `fermatQuintic`, `cubicField` and `unitCircle` are unchanged.
- **Forbidden constructs:** the file contains no `sorry`, `admit`, `axiom`, `native_decide`, `implemented_by`, `unsafe` or `set_option`.
- **Untouched files:** `Hilbert16.lean` and `Hilbert16Open.lean` were not edited.
- **Scratch files:** `src/Scratch.lean` and its `.olean` are deleted.
- **Deviations:** none, and nothing is left unfinished.

**Helper lemmas added**

All are in the same namespace, above their use, each with a docstring:

- `pderiv_fermatQuintic`: the i-th partial is `C 5 * X i ^ 4`.
- `cubicField_eval`: closed form of the field at a point.
- `cubicField_degreeAtMost`: `cubicField.DegreeAtMost 3`.
- `exists_angle`: `a ^ 2 + b ^ 2 = 1` gives `θ` with `cos θ = a` and `sin θ = b`.
- `unitCircle_isPeriodicOrbit`: the orbit `t ↦ ![cos t, sin t]` with period `2 * π`.
- `periodic_logistic_eq_one`: a positive periodic solution of `f' = 2 f (1 - f)` is identically 1.
- `rotation_solution`: solutions of `x' = -y`, `y' = x` are rotations of their initial value.
- `periodicOrbit_eq_unitCircle`: a periodic orbit of the cubic field avoiding the origin is the unit circle.

The proofs follow your suggested route, including the neighbourhood `{z | 0 < z 0 ^ 2 + z 1 ^ 2}`.

The edited file is `<scratch>/h16/src/HilbertDrafts/Hilbert16OpenChecks.lean`.
