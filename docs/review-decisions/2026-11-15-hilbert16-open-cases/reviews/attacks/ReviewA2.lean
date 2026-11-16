import HilbertDrafts.Hilbert16Open

open HilbertDrafts.Hilbert16 HilbertDrafts.Hilbert16Classification HilbertDrafts.Hilbert16Open

namespace ReviewA

/-! ### B: `ExactComponents` is exactly "the subspace has exactly m connected components" -/

theorem exactComponents_iff_equiv {X : Type} [TopologicalSpace X] (S : Set X) (m : ℕ) :
    ExactComponents S m ↔ Nonempty (ConnectedComponents S ≃ Fin m) := by
  classical
  let Φ : ConnectedComponents S → Set X :=
    fun c => Subtype.val '' (ConnectedComponents.mk ⁻¹' {c})
  have hΦmk : ∀ x : S, Φ (ConnectedComponents.mk x) = connectedComponentIn S x := by
    intro x
    rw [connectedComponentIn_eq_image x.2]
    show Subtype.val '' (ConnectedComponents.mk ⁻¹' {ConnectedComponents.mk x}) = _
    rw [connectedComponents_preimage_singleton]
  have hΦinj : Function.Injective Φ := by
    intro c c' h
    have h' : ConnectedComponents.mk ⁻¹' {c} = ConnectedComponents.mk ⁻¹' {c'} :=
      Set.image_injective.mpr Subtype.val_injective h
    obtain ⟨x, rfl⟩ := ConnectedComponents.surjective_coe c
    have hx : x ∈ ConnectedComponents.mk ⁻¹' {ConnectedComponents.mk x} :=
      Set.mem_preimage.mpr (Set.mem_singleton _)
    rw [h'] at hx
    exact Set.mem_singleton_iff.mp (Set.mem_preimage.mp hx)
  have hrange : ∀ C : Set X, (∃ x ∈ S, connectedComponentIn S x = C) ↔ C ∈ Set.range Φ := by
    intro C
    constructor
    · rintro ⟨x, hx, rfl⟩
      exact ⟨ConnectedComponents.mk ⟨x, hx⟩, hΦmk ⟨x, hx⟩⟩
    · rintro ⟨c, rfl⟩
      obtain ⟨x, rfl⟩ := ConnectedComponents.surjective_coe c
      exact ⟨x.1, x.2, (hΦmk x).symm⟩
  constructor
  · rintro ⟨comps, hinj, hcov⟩
    have hr : Set.range comps = Set.range Φ := by
      ext C
      exact (hcov C).symm.trans (hrange C)
    exact ⟨(Equiv.ofInjective Φ hΦinj).trans
      ((Equiv.cast (congrArg (fun s : Set (Set X) => ↥s) hr.symm)).trans (Equiv.ofInjective comps hinj).symm)⟩
  · rintro ⟨e⟩
    refine ⟨Φ ∘ e.symm, hΦinj.comp e.symm.injective, fun C => ?_⟩
    rw [hrange C, EquivLike.range_comp]

/-- The quintic target, restated with Mathlib's own `ConnectedComponents`. -/
theorem quinticSurfaceComponents_iff :
    QuinticSurfaceComponents ↔
      ∀ p : MvPolynomial (Fin 4) ℝ, Admissible 3 5 true p →
        ∃ m ≤ 23, Nonempty (ConnectedComponents (zeroLocus 3 p) ≃ Fin m) := by
  unfold QuinticSurfaceComponents SurfaceComponentBound
  simp only [exactComponents_iff_equiv]

/-! ### B: the topology is the quotient topology -/

example : Topology.IsQuotientMap
    (Projectivization.mk' ℝ : {v : Fin (3 + 1) → ℝ // v ≠ 0} → Projective 3) :=
  isQuotientMap_quot_mk

/-! ### A: derivative is the usual one -/

example (γ : ℝ → Plane) (v : Plane) (t : ℝ) :
    HasDerivAt γ v t ↔ ∀ i, HasDerivAt (fun s => γ s i) (v i) t := hasDerivAt_pi

example (f : ℝ → ℝ) (v t : ℝ) :
    HasDerivAt f v t ↔ Filter.Tendsto (slope f t) (nhdsWithin t {t}ᶜ) (nhds v) :=
  hasDerivAt_iff_tendsto_slope

/-! ### A: isolation is tube isolation -/

theorem periodicOrbit_isCompact {V : PolynomialVectorField} {C : Set Plane}
    (h : IsPeriodicOrbit V C) : IsCompact C := by
  obtain ⟨γ, T, hT, hp, -, hd, rfl⟩ := h
  have hc : Continuous γ := continuous_iff_continuousAt.mpr fun t => (hd t).continuousAt
  rw [← hp.image_Icc hT 0]
  exact isCompact_Icc.image hc

theorem isLimitCycle_iff_tube (V : PolynomialVectorField) (C : Set Plane) :
    IsLimitCycle V C ↔ IsPeriodicOrbit V C ∧ ∃ ε > 0, ∀ C', IsPeriodicOrbit V C' →
      C' ⊆ Metric.thickening ε C → C' = C := by
  constructor
  · rintro ⟨hC, U, hU, hCU, hiso⟩
    obtain ⟨ε, hε, hsub⟩ := (periodicOrbit_isCompact hC).exists_thickening_subset_open hU hCU
    exact ⟨hC, ε, hε, fun C' hC' h => hiso C' hC' (h.trans hsub)⟩
  · rintro ⟨hC, ε, hε, hiso⟩
    exact ⟨hC, Metric.thickening ε C, Metric.isOpen_thickening,
      Metric.self_subset_thickening hε C, hiso⟩

/-! ### A: a linear centre has periodic orbits that are not limit cycles -/

noncomputable def center : PolynomialVectorField := ⟨-MvPolynomial.X 1, MvPolynomial.X 0⟩

noncomputable def circ (r : ℝ) : ℝ → Plane := fun t => ![r * Real.cos t, r * Real.sin t]

theorem circle_periodicOrbit (r : ℝ) (hr : 0 < r) :
    IsPeriodicOrbit center (Set.range (circ r)) := by
  refine ⟨circ r, 2 * Real.pi, by positivity, ?_, ⟨0, Real.pi, ?_⟩, ?_, rfl⟩
  · intro t
    simp [circ]
  · intro h
    have h0 := congrFun h 0
    simp [circ] at h0
    linarith
  · intro t
    rw [hasDerivAt_pi]
    intro i
    fin_cases i
    · simpa [circ, center, PolynomialVectorField.eval] using
        (Real.hasDerivAt_cos t).const_mul r
    · simpa [circ, center, PolynomialVectorField.eval] using
        (Real.hasDerivAt_sin t).const_mul r

theorem center_circle_not_limitCycle : ¬ IsLimitCycle center (Set.range (circ 1)) := by
  rw [isLimitCycle_iff_tube]
  rintro ⟨-, ε, hε, hiso⟩
  have hr : (0 : ℝ) < 1 + ε / 2 := by linarith
  have hsub : Set.range (circ (1 + ε / 2)) ⊆ Metric.thickening ε (Set.range (circ 1)) := by
    rintro _ ⟨t, rfl⟩
    rw [Metric.mem_thickening_iff]
    refine ⟨circ 1 t, ⟨t, rfl⟩, ?_⟩
    rw [dist_pi_lt_iff hε]
    intro i
    fin_cases i
    · have h1 := Real.abs_cos_le_one t
      have : (1 + ε / 2) * Real.cos t - 1 * Real.cos t = ε / 2 * Real.cos t := by ring
      simp only [circ, Real.dist_eq, Fin.zero_eta, Matrix.cons_val_zero, this, abs_mul]
      rw [abs_of_pos (by linarith : (0 : ℝ) < ε / 2)]
      nlinarith
    · have h1 := Real.abs_sin_le_one t
      have : (1 + ε / 2) * Real.sin t - 1 * Real.sin t = ε / 2 * Real.sin t := by ring
      simp only [circ, Real.dist_eq, Fin.mk_one, Matrix.cons_val_one, Matrix.cons_val_zero,
        this, abs_mul]
      rw [abs_of_pos (by linarith : (0 : ℝ) < ε / 2)]
      nlinarith
  have heq := hiso _ (circle_periodicOrbit _ hr) hsub
  have hmem : circ (1 + ε / 2) 0 ∈ Set.range (circ 1) := heq ▸ ⟨0, rfl⟩
  obtain ⟨t, ht⟩ := hmem
  have h0 := congrFun ht 0
  simp [circ] at h0
  have := Real.cos_le_one t
  linarith

end ReviewA
