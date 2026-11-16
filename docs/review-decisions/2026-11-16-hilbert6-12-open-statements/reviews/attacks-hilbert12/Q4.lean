import HilbertDrafts.Hilbert12Lambda

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda NumberField
open scoped NumberField

namespace Review

/-- The degree used in the statement is positive: no junk value `0`. -/
theorem finrank_pos' {K : Type} [Field K] [NumberField K] : 0 < Module.finrank ℚ K :=
  Module.finrank_pos

/-- The degree exactly as the target elaborates it (no `Module ℚ K` binder in scope). -/
noncomputable def degree (K : Type) [Field K] [NumberField K] : ℕ := Module.finrank ℚ K

/-- No other `ℚ`-module structure on the additive group of the field changes the degree.
(If a `Module ℚ K` binder were in scope, `Module.finrank ℚ K` would silently use it; the
target has no such binder, so the comparison is made against `degree K`.) -/
theorem finrank_any_module {K : Type} [Field K] [NumberField K] (m : Module ℚ K) :
    @Module.finrank ℚ K _ _ m = degree K := by
  have h : m = @Algebra.toModule ℚ K _ _ DivisionRing.toRatAlgebra := Subsingleton.elim _ _
  subst h
  rfl

/-- The degree is invariant under field isomorphism, whatever the presentation. -/
theorem finrank_congr {K K' : Type} [Field K] [NumberField K] [Field K'] [NumberField K']
    (e : K ≃+* K') : Module.finrank ℚ K = Module.finrank ℚ K' :=
  LinearEquiv.finrank_eq e.toRatAlgEquiv.toLinearEquiv

/-- Having no ring homomorphism to the reals is invariant under field isomorphism. -/
theorem isEmpty_real_congr {K K' : Type} [Field K] [Field K'] (e : K ≃+* K') :
    IsEmpty (K →+* ℝ) ↔ IsEmpty (K' →+* ℝ) :=
  ⟨fun h => ⟨fun σ => h.false (σ.comp e.toRingHom)⟩,
    fun h => ⟨fun σ => h.false (σ.comp e.symm.toRingHom)⟩⟩

/-- Degree one means exactly: isomorphic to the rational field. -/
theorem finrank_eq_one_iff {K : Type} [Field K] [NumberField K] :
    Module.finrank ℚ K = 1 ↔ Nonempty (K ≃+* ℚ) := by
  constructor
  · intro h
    have h1 : (⊥ : Subalgebra ℚ K) = ⊤ := Subalgebra.bot_eq_top_iff_finrank_eq_one.mpr h
    have h2 : Function.Bijective (algebraMap ℚ K) :=
      Algebra.bijective_algebraMap_iff.mpr h1.symm
    exact ⟨(RingEquiv.ofBijective (algebraMap ℚ K) h2).symm⟩
  · rintro ⟨e⟩
    rw [finrank_congr e]
    exact Module.finrank_self ℚ

/-- No ring homomorphism to the reals means exactly: no real place (totally complex). -/
theorem isEmpty_real_iff {K : Type} [Field K] [NumberField K] :
    IsEmpty (K →+* ℝ) ↔ ∀ w : InfinitePlace K, ¬ w.IsReal := by
  constructor
  · intro h w hw
    exact h.false (InfinitePlace.embedding_of_isReal hw)
  · intro h
    refine ⟨fun σ => ?_⟩
    apply h (InfinitePlace.mk (Complex.ofRealHom.comp σ))
    refine ⟨Complex.ofRealHom.comp σ, ?_, rfl⟩
    rw [ComplexEmbedding.isReal_iff]
    ext x
    simp [ComplexEmbedding.conjugate_coe_eq]

/-- The target with the first side condition replaced by "not isomorphic to ℚ". This
checks that the lemmas above apply to the exact instances occurring in the target. -/
theorem q4_iff :
    BorgerDeSmitQuestion4 ↔
      ∃ (K : Type) (_ : Field K) (_ : NumberField K),
        ¬ Nonempty (K ≃+* ℚ) ∧
        ¬ (Module.finrank ℚ K = 2 ∧ ∀ w : InfinitePlace K, ¬ w.IsReal) ∧
        LambdaGeometric K := by
  unfold BorgerDeSmitQuestion4
  constructor
  · rintro ⟨K, i, j, h1, h2, h3⟩
    exact ⟨K, i, j, fun h => h1 (finrank_eq_one_iff.mpr h),
      fun h => h2 ⟨h.1, isEmpty_real_iff.mpr h.2⟩, h3⟩
  · rintro ⟨K, i, j, h1, h2, h3⟩
    exact ⟨K, i, j, fun h => h1 (finrank_eq_one_iff.mp h),
      fun h => h2 ⟨h.1, isEmpty_real_iff.mp h.2⟩, h3⟩

/-- The rational field with its standard instances fails the first side condition. -/
theorem rat_excluded : ¬ (Module.finrank ℚ ℚ ≠ 1) :=
  fun h => h (finrank_eq_one_iff.mpr ⟨RingEquiv.refl ℚ⟩)

/-- Every presentation of a field isomorphic to ℚ fails the first side condition. -/
theorem iso_rat_excluded (K : Type) (i : Field K) (j : NumberField K) (e : K ≃+* ℚ) :
    ¬ (Module.finrank ℚ K ≠ 1) :=
  fun h => h (finrank_eq_one_iff.mpr ⟨e⟩)

/-- Every presentation of a field isomorphic to one that fails the second side
condition also fails it. -/
theorem iso_imagQuad_excluded (K K' : Type) (i : Field K) (j : NumberField K)
    (i' : Field K') (j' : NumberField K') (e : K ≃+* K')
    (h : Module.finrank ℚ K' = 2 ∧ IsEmpty (K' →+* ℝ)) :
    ¬ ¬ (Module.finrank ℚ K = 2 ∧ IsEmpty (K →+* ℝ)) :=
  fun hn => hn ⟨(finrank_congr e).trans h.1, (isEmpty_real_congr e).mpr h.2⟩

end Review
