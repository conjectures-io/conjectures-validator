import Review.Affine2

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda NumberField AlgebraicGeometry CategoryTheory
open scoped NumberField
open Opposite Polynomial

namespace Review

variable {K : Type} [Field K]

theorem scalar_top_eq (M : IntegralScheme K) (r : 𝓞 K) :
    M.scalar ⊤ r = M.toBase.appTop.hom ((Scheme.ΓSpecIso (CommRingCat.of (𝓞 K))).inv.hom r) := by
  simp only [IntegralScheme.scalar, RingHom.comp_apply]
  change (M.toBase.appLE ⊤ (M.toBase ⁻¹ᵁ ⊤) le_rfl).hom _ = _
  rw [Scheme.Hom.appLE_eq_app]
  rfl

/-- The spectrum of a flat algebra of finite type, as an `IntegralScheme`. -/
noncomputable def specScheme (K : Type) [Field K] (R : Type) [CommRing R] [Algebra (𝓞 K) R]
    [Module.Flat (𝓞 K) R] [Algebra.FiniteType (𝓞 K) R] : IntegralScheme K where
  X := Spec (CommRingCat.of R)
  toBase := Spec.map (CommRingCat.ofHom (algebraMap (𝓞 K) R))
  flat := by
    rw [Flat.SpecMap_iff]
    exact RingHom.flat_algebraMap_iff.mpr inferInstance
  separated := inferInstance
  locallyOfFiniteType := by
    rw [HasRingHomProperty.Spec_iff (P := @LocallyOfFiniteType)]
    exact RingHom.finiteType_algebraMap.mpr inferInstance
  quasiCompact := inferInstance

instance (R : Type) [CommRing R] [Algebra (𝓞 K) R]
    [Module.Flat (𝓞 K) R] [Algebra.FiniteType (𝓞 K) R] : IsAffine (specScheme K R).X :=
  inferInstanceAs (IsAffine (Spec (CommRingCat.of R)))

/-- A ring endomorphism over the integers that is the `N(p)`-th power modulo `p`
gives a Frobenius lift in the formal sense, with every clause of `IsFrobeniusLift`. -/
theorem specScheme_isFrobeniusLift [NumberField K] (R : Type) [CommRing R] [Algebra (𝓞 K) R]
    [Module.Flat (𝓞 K) R] [Algebra.FiniteType (𝓞 K) R] (p : Prime K) (φ : R →+* R)
    (hφ : ∀ r : 𝓞 K, φ (algebraMap _ R r) = algebraMap _ R r)
    (hfrob : ∀ f : R, φ f - f ^ Ideal.absNorm p.asIdeal ∈
      Ideal.map (algebraMap (𝓞 K) R) p.asIdeal) :
    (specScheme K R).IsFrobeniusLift p (Spec.map (CommRingCat.ofHom φ)) := by
  have hglob : ∀ a : Γ((specScheme K R).X, ⊤),
      (Scheme.Hom.appTop (X := (specScheme K R).X) (Y := (specScheme K R).X)
        (Spec.map (CommRingCat.ofHom φ))).hom a - a ^ Ideal.absNorm p.asIdeal ∈
        Ideal.map ((specScheme K R).scalar ⊤) p.asIdeal := by
    intro a
    obtain ⟨f, rfl⟩ : ∃ f : R, (Scheme.ΓSpecIso (CommRingCat.of R)).inv.hom f = a :=
      ⟨(Scheme.ΓSpecIso (CommRingCat.of R)).hom.hom a,
        (Scheme.ΓSpecIso (CommRingCat.of R)).hom_inv_id_apply a⟩
    have h1 : (Spec.map (CommRingCat.ofHom φ)).appTop.hom
        ((Scheme.ΓSpecIso (CommRingCat.of R)).inv.hom f) =
        (Scheme.ΓSpecIso (CommRingCat.of R)).inv.hom (φ f) := by
      have := Scheme.ΓSpecIso_inv_naturality (CommRingCat.ofHom φ)
      exact (congrArg (fun g => g.hom f) this).symm
    have h2 : ∀ r : 𝓞 K, (specScheme K R).scalar ⊤ r =
        (Scheme.ΓSpecIso (CommRingCat.of R)).inv.hom (algebraMap _ R r) := by
      intro r
      rw [scalar_top_eq]
      have := Scheme.ΓSpecIso_inv_naturality (CommRingCat.ofHom (algebraMap (𝓞 K) R))
      exact (congrArg (fun g => g.hom r) this).symm
    have h3 : Ideal.map ((specScheme K R).scalar ⊤) p.asIdeal =
        Ideal.map (Scheme.ΓSpecIso (CommRingCat.of R)).inv.hom
          (Ideal.map (algebraMap (𝓞 K) R) p.asIdeal) := by
      rw [Ideal.map_map]
      congr 1
      ext r
      exact h2 r
    rw [h3]
    have h4 := Ideal.mem_map_of_mem (Scheme.ΓSpecIso (CommRingCat.of R)).inv.hom (hfrob f)
    rw [map_sub, map_pow] at h4
    rw [← h1] at h4
    exact h4
  refine ⟨?_, fixes_fibre_of_affine _ p _ hglob, congruence_of_affine _ p _ hglob⟩
  change Spec.map (CommRingCat.ofHom φ) ≫ Spec.map (CommRingCat.ofHom (algebraMap (𝓞 K) R)) =
    Spec.map (CommRingCat.ofHom (algebraMap (𝓞 K) R))
  rw [← Spec.map_comp]
  congr 1
  ext r
  exact hφ r

/-- The Frobenius congruence for the power map on the affine line. -/
theorem expand_sub_pow_mem [NumberField K] (p : Prime K) (f : (𝓞 K)[X]) :
    expand (𝓞 K) (Ideal.absNorm p.asIdeal) f - f ^ Ideal.absNorm p.asIdeal ∈
      Ideal.map (algebraMap (𝓞 K) (𝓞 K)[X]) p.asIdeal := by
  have hmax : p.asIdeal.IsMaximal := p.isMaximal
  let _ : Field (𝓞 K ⧸ p.asIdeal) := Ideal.Quotient.field p.asIdeal
  have hfin : Finite (𝓞 K ⧸ p.asIdeal) := (Ideal.absNorm_ne_zero_iff _).mp (by
    rw [Ne, Ideal.absNorm_eq_zero_iff]; exact p.ne_bot)
  let _ : Fintype (𝓞 K ⧸ p.asIdeal) := Fintype.ofFinite _
  have hcard : Fintype.card (𝓞 K ⧸ p.asIdeal) = Ideal.absNorm p.asIdeal := by
    rw [Ideal.absNorm_apply, Submodule.cardQuot_apply, Nat.card_eq_fintype_card]
  have hker := Polynomial.ker_mapRingHom (Ideal.Quotient.mk p.asIdeal)
  rw [Ideal.mk_ker] at hker
  have hmem : expand (𝓞 K) (Ideal.absNorm p.asIdeal) f - f ^ Ideal.absNorm p.asIdeal ∈
      RingHom.ker (Polynomial.mapRingHom (Ideal.Quotient.mk p.asIdeal)) := by
    rw [RingHom.mem_ker, map_sub, map_pow, Polynomial.coe_mapRingHom, Polynomial.map_expand,
      ← hcard, FiniteField.Polynomial.expand_card, sub_self]
  rw [hker] at hmem
  have hC : algebraMap (𝓞 K) (𝓞 K)[X] = C := RingHom.ext fun r => (C_eq_algebraMap r).symm
  rw [hC]
  exact hmem

/-- The toric line: the affine line over the integers of ANY number field, with
the `N(p)`-th power maps, is a Λ-structure in the formal sense. So the formal
class contains a positive-dimensional object for every base field. -/
noncomputable def toricLine (K : Type) [Field K] [NumberField K] :
    LambdaStructure (specScheme K (𝓞 K)[X]) where
  psi p := Spec.map (CommRingCat.ofHom
    (expand (𝓞 K) (Ideal.absNorm p.asIdeal) : (𝓞 K)[X] →+* (𝓞 K)[X]))
  frobenius p := by
    apply specScheme_isFrobeniusLift
    · intro r
      rw [← C_eq_algebraMap]
      exact expand_C _ r
    · intro f
      exact expand_sub_pow_mem p f
  commute p q := by
    change Spec.map _ ≫ Spec.map _ = Spec.map _ ≫ Spec.map _
    rw [← Spec.map_comp, ← Spec.map_comp]
    congr 1
    refine CommRingCat.hom_ext (RingHom.ext fun f => ?_)
    change expand (𝓞 K) _ (expand (𝓞 K) _ f) = expand (𝓞 K) _ (expand (𝓞 K) _ f)
    rw [expand_expand, expand_expand, mul_comm]

#print axioms toricLine

end Review
