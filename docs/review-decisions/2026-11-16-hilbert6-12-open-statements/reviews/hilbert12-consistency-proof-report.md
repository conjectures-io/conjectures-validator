# Hilbert 12 Λ-geometric consistency proofs

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T02:47:39.126Z)

```text
You are completing Lean 4 / Mathlib proofs in one existing file. Pure proof engineering; no web access is needed.

## Files
Working directory (absolute): <scratch>/h612
- `src/HilbertDrafts/Hilbert12.lean` and `src/HilbertDrafts/Hilbert12Lambda.lean` — definitions. DO NOT EDIT either file (other processes are reading them).
- `src/HilbertDrafts/Hilbert12LambdaChecks.lean` — the ONLY file you edit. It contains six theorems ending in `sorry` and a definition `baseScheme` with four `sorry` fields.
Read `Hilbert12Lambda.lean` in full first: it defines `IntegralScheme` (a flat, separated, finite-type scheme over `Spec (𝓞 K)` using Mathlib's `AlgebraicGeometry.Scheme`), `IntegralScheme.scalar`, `OverPrime`, `IsFrobeniusLift`, `LambdaStructure`, `psiList`, `idealOfList`, `Cycle`, `Cycle.Equivalent`, `GeometricPoint`, `IsPeriodic`, `value`, `periodicValues`, `LambdaGeometric`.

## How to compile
From the working directory: `./compile.sh HilbertDrafts/Hilbert12LambdaChecks.lean` (Lean 4.35.0-rc2 with a pinned Mathlib inside Docker, offline, warnings are errors, 30–120 s per run). Exit code 0 and no output means success. Dependency `.olean` files already exist; if you see "object file ... does not exist", recompile `HilbertDrafts/Hilbert12.lean` then `HilbertDrafts/Hilbert12Lambda.lean` in that order with the same script. Warnings are errors, so fix unused variables, unused simp arguments and deprecated names rather than silencing linters; do not add `set_option` lines unless truly unavoidable, and tell me if you did.
Mathlib sources are inside the image at `/opt/fc-verifier/vendor/formal-conjectures/.lake/packages/mathlib/Mathlib` — search them with e.g. `docker run --rm --network none --entrypoint bash sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90 -c 'cd /opt/fc-verifier/vendor/formal-conjectures/.lake/packages/mathlib/Mathlib && grep -rn "theorem appLE_" AlgebraicGeometry | head'`. The relevant API is in `AlgebraicGeometry/Scheme.lean`, `AffineScheme.lean`, `Morphisms/{Flat,Separated,FiniteType,QuasiCompact}.lean`, `Spec.lean`. You may create scratch `.lean` files under `src/` with names starting `ScratchA` (e.g. `src/ScratchA1.lean`) to test with `#check`, `exact?`, `infer_instance`; delete them when finished. Do not touch any other file in that directory.

## Required work (must finish)
1. `idealOfList_nil`, `idealOfList_append`, `psiList_append`, `psiList_perm` (use `LambdaStructure.commute`; induct on `List.Perm`), `equivalent_refl` (take `x = 1`; the finite part of the cycle contains 0).
2. `frobeniusLift_global`: from `h.2.2` applied with `U = V = ⊤` (the restriction map along `homOfLE (le_refl ⊤)` is the identity; relate `(M.X.presheaf.map (homOfLE _).op).hom s` to `s`, e.g. via `CategoryTheory.Functor.map_id` after showing the morphism is `𝟙`, or by `Subsingleton`/`rfl` reasoning on morphisms in the opens preorder category).
3. `baseScheme`: fill the four fields — the identity morphism of `Spec (CommRingCat.of (𝓞 K))` is flat, separated, locally of finite type and quasi-compact (`inferInstance` should work for identities/isomorphisms; otherwise find the instances).
The statements of these six theorems and the definition header of `baseScheme` must remain exactly as written (you may only replace `sorry`). Helper lemmas may be added above their use, inside the namespace.

## Stretch goals (attempt in this order after the required work compiles; each is optional, add it only if it is fully proved)
S1. Add
```
/-- The affine line over the ring of integers. -/
def affineLine (K : Type) [Field K] [NumberField K] : IntegralScheme K
```
with `X := Spec (CommRingCat.of (Polynomial (𝓞 K)))` and `toBase := Spec.map (CommRingCat.ofHom (Polynomial.C : 𝓞 K →+* Polynomial (𝓞 K)))` (or `algebraMap`), proving the four properties, and then
```
/-- The identity is not a Frobenius lift on the affine line: the congruence `x ≡ x ^ N(p)` fails. The Frobenius condition is therefore not vacuous. -/
theorem id_not_frobeniusLift_affineLine (p : Prime K) : ¬ (affineLine K).IsFrobeniusLift p (𝟙 _)
```
Route: use `frobeniusLift_global` (the top open of an affine scheme is affine: `isAffineOpen_top`), transport along `Scheme.ΓSpecIso` to get `X - X ^ N ∈ p·(𝓞 K)[X]` and derive a contradiction from the coefficient of `X` (it is 1, which is not in `p`, using `N = Ideal.absNorm p.asIdeal ≥ 2`). This needs care relating `(affineLine K).scalar ⊤` to `Polynomial.C` through `ΓSpecIso` (naturality lemmas such as `Scheme.ΓSpecIso_naturality`, `Scheme.ΓSpecIso_inv_naturality`, `Spec.map_app`/`appLE` lemmas).
S2. Add
```
/-- The identity is a Frobenius lift on the base itself at every prime, so Λ-structures exist. -/
theorem id_isFrobeniusLift_base (p : Prime K) : (baseScheme K).IsFrobeniusLift p (𝟙 _)
```
The mathematical content: for an affine open `V` of `Spec (𝓞 K)`, `Γ(V)/pΓ(V)` is either zero or the residue field with `N(p)` elements, where `s ^ N(p) = s`. This may be hard to do for arbitrary affine opens; if you cannot finish it, leave it out entirely.

## Hard requirements
- No `sorry`, `admit`, new `axiom`, `native_decide`, `unsafe`, `implemented_by` in the final file. If a REQUIRED item cannot be proved, leave that single item as `sorry` and say so plainly; never weaken or alter a statement to make it pass. Stretch items that are not fully proved must be removed, not left as `sorry`.
- When done, compile a scratch file importing `HilbertDrafts.Hilbert12LambdaChecks` with `#print axioms` for every theorem in the file (and for `baseScheme`, and `affineLine` if added); only `propext`, `Classical.choice`, `Quot.sound` are permitted. Report the output verbatim.

## Report back
- Final compile command and result for `Hilbert12LambdaChecks.lean`.
- Which required items are proved, which stretch items were added (with their exact final statements), and anything left unfinished.
- The verbatim `#print axioms` output.
- Any observation, made while proving, that a definition in `Hilbert12Lambda.lean` does not mean what its docstring says (for example if `scalar`, `OverPrime` or `IsFrobeniusLift` turned out vacuous, ill-typed in spirit, or impossible to satisfy). That kind of finding is more valuable than the proofs.
```

## Report 1 (2026-11-16T03:03:53.542Z)

All six required theorems and `baseScheme` are proved, and both stretch goals (S1 and S2) are added and fully proved. The file compiles clean with no `sorry` and no `set_option`.

**File:** `<scratch>/h612/src/HilbertDrafts/Hilbert12LambdaChecks.lean`

**Final compile:** `./compile.sh HilbertDrafts/Hilbert12LambdaChecks.lean` gives exit 0 and no output. Scratch files are deleted; the script leaves `Hilbert12LambdaChecks.olean` beside the source.

## Two things I had to add around the given statements

The statement text of all six theorems and the `baseScheme` header is unchanged, but the file would not compile as given without these:

1. **`Cycle` is ambiguous as written.** `equivalent_refl (f : Cycle K)` fails with "Ambiguous term `Cycle`", because Mathlib has a root-level `Cycle` (cyclic lists) and the file only `open`s `Hilbert12Lambda`. I added `export HilbertDrafts.Hilbert12Lambda (Cycle)` above the theorem; `#check` confirms it elaborates to `Hilbert12Lambda.Cycle K`. The side effect is a persistent alias `HilbertDrafts.Hilbert12LambdaChecks.Cycle`.
2. **`omit [NumberField K] in`** precedes `idealOfList_nil`, `idealOfList_append` and `equivalent_refl`. The unused-section-variable linter is an error here, since `Prime`, `idealOfList`, `Cycle` and `Cycle.Equivalent` only need `[Field K]`. The elaborated signatures therefore drop the `[NumberField K]` argument, which makes them strictly more general.

## Stretch items added (exact statements)

```lean
def affineLine (K : Type) [Field K] [NumberField K] : IntegralScheme K
-- X := Spec (CommRingCat.of (Polynomial (𝓞 K)))
-- toBase := Spec.map (CommRingCat.ofHom (Polynomial.C : 𝓞 K →+* Polynomial (𝓞 K)))

theorem id_not_frobeniusLift_affineLine (p : Prime K) :
    ¬ (affineLine K).IsFrobeniusLift p (𝟙 _)

theorem id_isFrobeniusLift_base (p : Prime K) : (baseScheme K).IsFrobeniusLift p (𝟙 _)
```

S2 holds for arbitrary affine opens, as stated. Helper lemmas added inside the namespace: `id_appLE`, `id_appLE_top`, `affineLine_coordinates`, `sub_pow_mem_map_of_fraction`, `sub_pow_absNorm_mem`, `stalk_fraction`, `spec_sub_pow_mem`.

## `#print axioms` output (verbatim)

```
'HilbertDrafts.Hilbert12LambdaChecks.idealOfList_nil' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.idealOfList_append' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.psiList_append' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.psiList_perm' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.equivalent_refl' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.frobeniusLift_global' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.baseScheme' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.id_appLE' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.id_appLE_top' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.affineLine' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.affineLine_coordinates' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.id_not_frobeniusLift_affineLine' depends on axioms: [propext,
 Classical.choice,
 Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.sub_pow_mem_map_of_fraction' depends on axioms: [propext,
 Classical.choice,
 Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.sub_pow_absNorm_mem' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.stalk_fraction' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.spec_sub_pow_mem' depends on axioms: [propext, Classical.choice, Quot.sound]
'HilbertDrafts.Hilbert12LambdaChecks.id_isFrobeniusLift_base' depends on axioms: [propext, Classical.choice, Quot.sound]
```

## Observations on the definitions in `Hilbert12Lambda.lean`

No definition turned out vacuous, ill-typed in spirit or unsatisfiable.

Machine-checked:
- **`scalar` is the structure map.** On the affine line, under `Γ(A¹, ⊤) ≅ (𝓞 K)[X]`, `scalar ⊤` is `Polynomial.C` (`affineLine_coordinates`).
- **`IsFrobeniusLift` is not vacuous.** The identity fails it on the affine line at every prime, via the coefficient of `X` in `X - X^N`.
- **`IsFrobeniusLift` is satisfiable.** The identity on the base satisfies it at every prime, for all `U` and all affine `V`.
- **The name `Cycle` clashes with Mathlib's root `Cycle`.** Any downstream file that opens the namespace hits the ambiguity above; renaming or qualifying it in `Hilbert12Lambda.lean` would be cleaner than my `export`.

By hand only, not machine-checked:
- **`OverPrime` was not exercised by any proof.** In S2 the fixed-point clause closes by `rfl` for the identity. On paper it reads correctly as the fibre: on the base it reduces to `p ⊆ x.asIdeal`.
- **`Cycle.Equivalent` is not symmetric at the zero ideal.** With `f.real = ∅` and `b ≤ f.finite`, `Equivalent ⊥ b` holds via `x = 0`, but `Equivalent b ⊥` fails for `b ≠ ⊥`. This is unreachable through `IsPeriodic`, since `idealOfList` is never `⊥`, but the relation is only an equivalence on nonzero ideals.
- **Empty fibres leave `ψ_p` unconstrained.** If the fibre over `p` is empty (e.g. a scheme over `𝓞 K[1/n]` with `p ∣ n`), every base endomorphism is a Frobenius lift at `p`, constrained only by `commute`. This matches the docstring but is a consequence worth knowing.
