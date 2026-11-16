import HilbertDrafts.Hilbert12Lambda

/-!
Machine-checked consistency checks for the Λ-geometric form of Hilbert 12.
They show that the definitions behave as intended. They are not progress on
the question itself.
-/

namespace HilbertDrafts.Hilbert12LambdaChecks

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda NumberField AlgebraicGeometry
  CategoryTheory
open scoped NumberField

noncomputable section

variable {K : Type} [Field K] [NumberField K]

omit [NumberField K] in
/-- The empty list of primes gives the unit ideal. -/
theorem idealOfList_nil : idealOfList ([] : List (Prime K)) = ⊤ := by
  simp [idealOfList, Ideal.one_eq_top]

omit [NumberField K] in
/-- Concatenating lists multiplies ideals. -/
theorem idealOfList_append (L₁ L₂ : List (Prime K)) :
    idealOfList (L₁ ++ L₂) = idealOfList L₁ * idealOfList L₂ := by
  simp [idealOfList, List.map_append, List.prod_append]

/-- Concatenating lists composes the operators. -/
theorem psiList_append {M : IntegralScheme K} (Λ : LambdaStructure M)
    (L₁ L₂ : List (Prime K)) :
    Λ.psiList (L₁ ++ L₂) = Λ.psiList L₁ ≫ Λ.psiList L₂ := by
  induction L₁ with
  | nil => simp [LambdaStructure.psiList]
  | cons p L ih => simp [LambdaStructure.psiList, ih, Category.assoc]

/-- The operator of an ideal does not depend on the order of its prime factors. -/
theorem psiList_perm {M : IntegralScheme K} (Λ : LambdaStructure M)
    {L₁ L₂ : List (Prime K)} (h : L₁.Perm L₂) : Λ.psiList L₁ = Λ.psiList L₂ := by
  induction h with
  | nil => rfl
  | cons p _ ih => simp only [LambdaStructure.psiList, ih]
  | swap p q L =>
    simp only [LambdaStructure.psiList]
    rw [← Category.assoc, ← Category.assoc, Λ.commute]
  | trans _ _ ih₁ ih₂ => exact ih₁.trans ih₂

-- `Cycle` also names Mathlib's cyclic lists at the root; make the bare name mean the
-- cycle of `Hilbert12Lambda` inside this namespace.
export HilbertDrafts.Hilbert12Lambda (Cycle)

omit [NumberField K] in
/-- Every ideal is equivalent to itself modulo every cycle. -/
theorem equivalent_refl (f : Cycle K) (a : Ideal (𝓞 K)) : f.Equivalent a a := by
  refine ⟨1, ?_, ?_, ?_⟩
  · intro σ _
    simp
  · intro α
    simp
  · intro b' _
    exact ⟨0, f.finite.zero_mem, by simp⟩

/-- On an affine scheme a Frobenius lift satisfies the ring-level congruence
`ψ(s) ≡ s ^ N(p)` modulo `p` on global functions. The scheme-level condition is
therefore at least as strong as the algebraic definition of Borger and de Smit. -/
theorem frobeniusLift_global {M : IntegralScheme K} {p : Prime K} {ψ : M.X ⟶ M.X}
    (hX : IsAffineOpen (⊤ : M.X.Opens)) (h : M.IsFrobeniusLift p ψ) (s : Γ(M.X, ⊤)) :
    (ψ.appLE ⊤ ⊤ le_top).hom s - s ^ Ideal.absNorm p.asIdeal ∈
      Ideal.map (M.scalar ⊤) p.asIdeal := by
  have key := h.2.2 ⊤ ⊤ le_rfl le_top hX s
  have hid : (M.X.presheaf.map (homOfLE (le_refl (⊤ : M.X.Opens))).op).hom s = s := by
    have : (homOfLE (le_refl (⊤ : M.X.Opens))) = 𝟙 _ := rfl
    rw [this, op_id, CategoryTheory.Functor.map_id]
    rfl
  rw [hid] at key
  exact key

/-- The class of schemes is not empty: the base itself is flat, separated and
of finite type over itself. -/
def baseScheme (K : Type) [Field K] [NumberField K] : IntegralScheme K where
  X := Base K
  toBase := 𝟙 _
  flat := inferInstance
  separated := inferInstance
  locallyOfFiniteType := inferInstance
  quasiCompact := inferInstance

/-- The identity morphism acts on functions by restriction. -/
theorem id_appLE (X : Scheme) (U V : X.Opens) (hU : V ≤ U) (hψ : V ≤ (𝟙 X) ⁻¹ᵁ U)
    (s : Γ(X, U)) :
    (Scheme.Hom.appLE (𝟙 X) U V hψ).hom s = (X.presheaf.map (homOfLE hU).op).hom s :=
  congrArg (fun f : Γ(X, U) ⟶ Γ(X, V) => f.hom s)
    (Category.id_comp (X.presheaf.map (homOfLE hU).op))

/-- The identity morphism acts as the identity on global functions. -/
theorem id_appLE_top (X : Scheme) (s : Γ(X, ⊤)) :
    (Scheme.Hom.appLE (𝟙 X) ⊤ ⊤ le_top).hom s = s := by
  simp [Scheme.Hom.appLE]

/-- The affine line over the ring of integers. -/
def affineLine (K : Type) [Field K] [NumberField K] : IntegralScheme K where
  X := Spec (CommRingCat.of (Polynomial (𝓞 K)))
  toBase := Spec.map (CommRingCat.ofHom (Polynomial.C : 𝓞 K →+* Polynomial (𝓞 K)))
  flat := by
    rw [Flat.SpecMap_iff, CommRingCat.hom_ofHom, ← Polynomial.algebraMap_eq,
      RingHom.flat_algebraMap_iff]
    infer_instance
  separated := inferInstance
  locallyOfFiniteType := by
    rw [HasRingHomProperty.Spec_iff (P := @LocallyOfFiniteType), CommRingCat.hom_ofHom,
      ← Polynomial.algebraMap_eq, RingHom.finiteType_algebraMap]
    infer_instance
  quasiCompact := inferInstance

/-- Global functions on the affine line are polynomials, and the integers among
them are the constant polynomials. -/
theorem affineLine_coordinates :
    ∃ φ : Γ((affineLine K).X, ⊤) →+* Polynomial (𝓞 K),
      Function.Surjective φ ∧ φ.comp ((affineLine K).scalar ⊤) = Polynomial.C := by
  refine ⟨(Scheme.ΓSpecIso (CommRingCat.of (Polynomial (𝓞 K)))).hom.hom, ?_, ?_⟩
  · intro f
    exact ⟨(Scheme.ΓSpecIso (CommRingCat.of (Polynomial (𝓞 K)))).inv.hom f,
      Iso.inv_hom_id_apply (Scheme.ΓSpecIso (CommRingCat.of (Polynomial (𝓞 K)))) f⟩
  · have h := Scheme.ΓSpecIso_naturality
      (CommRingCat.ofHom (Polynomial.C : 𝓞 K →+* Polynomial (𝓞 K)))
    have h2 : (Scheme.ΓSpecIso (CommRingCat.of (𝓞 K))).inv ≫
        (Spec.map (CommRingCat.ofHom (Polynomial.C : 𝓞 K →+* Polynomial (𝓞 K)))).appTop ≫
          (Scheme.ΓSpecIso (CommRingCat.of (Polynomial (𝓞 K)))).hom =
        CommRingCat.ofHom (Polynomial.C : 𝓞 K →+* Polynomial (𝓞 K)) := by
      rw [h, Iso.inv_hom_id_assoc]
    exact congrArg CommRingCat.Hom.hom h2

/-- The identity is not a Frobenius lift on the affine line: the congruence
`x ≡ x ^ N(p)` fails. The Frobenius condition is therefore not vacuous. -/
theorem id_not_frobeniusLift_affineLine (p : Prime K) :
    ¬ (affineLine K).IsFrobeniusLift p (𝟙 _) := by
  intro h
  obtain ⟨φ, hφ, hC⟩ := affineLine_coordinates (K := K)
  obtain ⟨x, hx⟩ := hφ Polynomial.X
  have hX : IsAffineOpen (⊤ : (affineLine K).X.Opens) :=
    isAffineOpen_top (Spec (CommRingCat.of (Polynomial (𝓞 K))))
  have key := frobeniusLift_global hX h x
  rw [id_appLE_top] at key
  have key2 := Ideal.mem_map_of_mem φ key
  rw [Ideal.map_map, hC, map_sub, map_pow, hx, Ideal.mem_map_C_iff] at key2
  have hN : Ideal.absNorm p.asIdeal ≠ 1 := by
    rw [Ne, Ideal.absNorm_eq_one_iff]
    exact p.isPrime.ne_top
  have h1 : (1 : 𝓞 K) ∈ p.asIdeal := by
    simpa [Polynomial.coeff_X_pow, Ne.symm hN] using key2 1
  exact p.isPrime.ne_top ((Ideal.eq_top_iff_one _).mpr h1)

/-- If `r ≡ r ^ N` modulo `I` for every `r`, the same congruence holds for every
fraction `r / u`, modulo the ideal generated by `I`. -/
theorem sub_pow_mem_map_of_fraction {R S : Type*} [CommRing R] [CommRing S] (f : R →+* S)
    (I : Ideal R) (N : ℕ) (hN : ∀ r : R, r - r ^ N ∈ I) (z : S)
    (hz : ∃ r u : R, IsUnit (f u) ∧ z * f u = f r) : z - z ^ N ∈ Ideal.map f I := by
  obtain ⟨r, u, hu, h⟩ := hz
  have hmem : (r - r ^ N) * u - r * (u - u ^ N) ∈ I :=
    I.sub_mem (I.mul_mem_right _ (hN r)) (I.mul_mem_left _ (hN u))
  have key : (z - z ^ N) * f u ^ (N + 1) = f ((r - r ^ N) * u - r * (u - u ^ N)) := by
    simp only [map_sub, map_mul, map_pow, ← h]
    ring
  have hmem' := Ideal.mem_map_of_mem f hmem
  rw [← key] at hmem'
  exact (Ideal.mul_unit_mem_iff_mem _ (hu.pow (N + 1))).mp hmem'

/-- Fermat's congruence in the ring of integers: `r ≡ r ^ N(p)` modulo `p`. -/
theorem sub_pow_absNorm_mem (p : Prime K) (r : 𝓞 K) :
    r - r ^ Ideal.absNorm p.asIdeal ∈ p.asIdeal := by
  have : p.asIdeal.IsMaximal := p.isMaximal
  let : Field (𝓞 K ⧸ p.asIdeal) := Ideal.Quotient.field p.asIdeal
  have : Finite (𝓞 K ⧸ p.asIdeal) := Ideal.finiteQuotientOfFreeOfNeBot p.asIdeal p.ne_bot
  let := Fintype.ofFinite (𝓞 K ⧸ p.asIdeal)
  rw [← Ideal.Quotient.eq_zero_iff_mem, map_sub, map_pow, Ideal.absNorm_apply,
    Submodule.cardQuot_apply, Nat.card_eq_fintype_card, FiniteField.pow_card, sub_self]

/-- Every germ of a regular function on the spectrum of a ring is a fraction of
ring elements. -/
theorem stalk_fraction (R : Type) [CommRing R] (x : PrimeSpectrum R)
    (z : (structurePresheafInCommRingCat R).stalk x) :
    ∃ r u : R, IsUnit ((StructureSheaf.toStalk R x).hom u) ∧
      z * (StructureSheaf.toStalk R x).hom u = (StructureSheaf.toStalk R x).hom r := by
  obtain ⟨⟨r, u⟩, h⟩ := IsLocalization.surj x.asIdeal.primeCompl z
  exact ⟨r, u, IsLocalization.map_units _ u, h⟩

/-- On every affine open of the spectrum of a ring, a congruence `r ≡ r ^ N`
modulo `I` valid for all ring elements is valid for all regular functions. -/
theorem spec_sub_pow_mem (R : Type) [CommRing R] (I : Ideal R) (N : ℕ)
    (hN : ∀ r : R, r - r ^ N ∈ I) (V : (Spec (CommRingCat.of R)).Opens) (hV : IsAffineOpen V)
    (t : Γ(Spec (CommRingCat.of R), V)) :
    t - t ^ N ∈ Ideal.map
      (((Scheme.Hom.appLE (𝟙 (Spec (CommRingCat.of R))) ⊤ V le_top).hom).comp
        (Scheme.ΓSpecIso (CommRingCat.of R)).inv.hom) I := by
  refine hV.mem_ideal_iff.mpr fun x hx => ?_
  have hcomp : ((Spec (CommRingCat.of R)).presheaf.germ V x hx).hom.comp
      (((Scheme.Hom.appLE (𝟙 (Spec (CommRingCat.of R))) ⊤ V le_top).hom).comp
        (Scheme.ΓSpecIso (CommRingCat.of R)).inv.hom) =
      (StructureSheaf.toStalk R x).hom := by
    rw [← StructureSheaf.algebraMap_germ V x hx]
    rfl
  rw [Ideal.map_map, hcomp, map_sub, map_pow]
  exact sub_pow_mem_map_of_fraction _ I N hN _ (stalk_fraction R x _)

/-- The identity is a Frobenius lift on the base itself at every prime, so
Λ-structures exist. -/
theorem id_isFrobeniusLift_base (p : Prime K) : (baseScheme K).IsFrobeniusLift p (𝟙 _) := by
  refine ⟨Category.comp_id _, fun x _ => rfl, ?_⟩
  intro U V hU hψ hV s
  rw [id_appLE _ U V hU hψ s]
  exact spec_sub_pow_mem (𝓞 K) p.asIdeal _ (sub_pow_absNorm_mem p) V hV _

/-- The fifth cyclotomic field has degree four over the rational field. -/
theorem finrank_cyclotomicField_five : Module.finrank ℚ (CyclotomicField 5 ℚ) = 4 := by
  have h := @IsCyclotomicExtension.finrank 5 _ ℚ (CyclotomicField 5 ℚ) _ _ _
    (CyclotomicField.algebra 5 ℚ) (CyclotomicField.isCyclotomicExtension 5 ℚ)
    (Polynomial.cyclotomic.irreducible_rat (by norm_num))
  rw [show Nat.totient 5 = 4 by decide] at h
  exact h

/-- The universal form implies Question 4, because number fields exist that are
neither the rational field nor imaginary quadratic. -/
theorem question4_of_universal (h : Hilbert12LambdaGeometric) : BorgerDeSmitQuestion4 := by
  refine ⟨CyclotomicField 5 ℚ, inferInstance, inferInstance, ?_, ?_, h _⟩
  · rw [finrank_cyclotomicField_five]
    decide
  · rw [finrank_cyclotomicField_five]
    rintro ⟨h2, -⟩
    exact absurd h2 (by decide)

end

end HilbertDrafts.Hilbert12LambdaChecks
