import HilbertDrafts.Hilbert6MeanField

open HilbertDrafts.Hilbert6 HilbertDrafts.Hilbert6MeanField MeasureTheory Filter
open scoped Topology ENNReal

namespace Rev

instance phaseHaar : Measure.IsAddHaarMeasure (volume : Measure Phase) :=
  inferInstanceAs (Measure.IsAddHaarMeasure ((volume : Measure Space).prod volume))

instance configHaar (N : ℕ) : Measure.IsAddHaarMeasure (volume : Measure (Configuration N)) :=
  @Measure.pi.isAddHaarMeasure (Fin N) (fun _ => Phase) _ _ (fun _ => volume)
    (fun _ => inferInstance) (fun _ => inferInstance) _ (fun _ => phaseHaar) (fun _ => inferInstance)

/-- The coincidence set of two particles is Lebesgue-null in configuration space. -/
theorem coincide_null (N : ℕ) (i j : Fin (N + 1)) (hij : i ≠ j) :
    (volume : Measure (Configuration (N + 1))) {z | (z i).1 = (z j).1} = 0 := by
  let L : Configuration (N + 1) →ₗ[ℝ] Space :=
    (LinearMap.fst ℝ Space Space).comp (LinearMap.proj i) -
      (LinearMap.fst ℝ Space Space).comp (LinearMap.proj j)
  have hset : {z : Configuration (N + 1) | (z i).1 = (z j).1} = (LinearMap.ker L : Set _) := by
    ext z
    simp [L, sub_eq_zero]
  rw [hset]
  apply Measure.addHaar_submodule
  intro htop
  have hx : (Pi.single i ((EuclideanSpace.single 0 (1 : ℝ) : Space), (0 : Space)) :
      Configuration (N + 1)) ∈ LinearMap.ker L := by
    rw [htop]; trivial
  simp [L, Pi.single_eq_of_ne hij.symm] at hx

/-- Hence the set of configurations with a coincident pair is null for the product law. -/
theorem productLaw_coincide_null (N : ℕ) (f₀ : Phase → ℝ) :
    productLaw N f₀ {z | ∃ i j, i ≠ j ∧ (z i).1 = (z j).1} = 0 := by
  have hv : (volume : Measure (Configuration (N + 1)))
      {z | ∃ i j, i ≠ j ∧ (z i).1 = (z j).1} = 0 := by
    have : {z : Configuration (N + 1) | ∃ i j, i ≠ j ∧ (z i).1 = (z j).1} =
        ⋃ i, ⋃ j, ⋃ (_ : i ≠ j), {z | (z i).1 = (z j).1} := by
      ext z; simp
    rw [this]
    refine (measure_iUnion_null_iff.mpr fun i => measure_iUnion_null_iff.mpr fun j =>
      measure_iUnion_null_iff.mpr fun hij => coincide_null N i j hij)
  exact withDensity_absolutelyContinuous _ _ hv

/-- The directional derivative in the PDE clause is the transport operator
`∂ₜF + v·∇ₓF + E·∇ᵥF` whenever `F` is differentiable at the point. -/
theorem fderiv_split (F : ℝ × Phase → ℝ) (t : ℝ) (x v E : Space)
    (hF : DifferentiableAt ℝ F (t, (x, v))) :
    fderiv ℝ F (t, (x, v)) (1, (v, E)) =
      deriv (fun s => F (s, (x, v))) t
      + fderiv ℝ (fun y => F (t, (y, v))) x v
      + fderiv ℝ (fun w => F (t, (x, w))) v E := by
  have hd := hF.hasFDerivAt
  have h1 : HasDerivAt (fun s => F (s, (x, v))) (fderiv ℝ F (t, (x, v)) (1, 0)) t := by
    have := hd.comp_hasDerivAt t ((hasDerivAt_id t).prodMk (hasDerivAt_const t (x, v)))
    exact this
  have h2 : HasFDerivAt (fun y => F (t, (y, v)))
      ((fderiv ℝ F (t, (x, v))).comp
        ((ContinuousLinearMap.inr ℝ ℝ Phase).comp (ContinuousLinearMap.inl ℝ Space Space))) x := by
    have hin : HasFDerivAt (fun y : Space => ((t, (y, v)) : ℝ × Phase))
        ((ContinuousLinearMap.inr ℝ ℝ Phase).comp (ContinuousLinearMap.inl ℝ Space Space)) x := by
      have a : HasFDerivAt (fun y : Space => ((y, v) : Phase))
          (ContinuousLinearMap.inl ℝ Space Space) x := hasFDerivAt_prodMk_left x v
      exact (hasFDerivAt_prodMk_right t (x, v)).comp x a
    exact hd.comp x hin
  have h3 : HasFDerivAt (fun w => F (t, (x, w)))
      ((fderiv ℝ F (t, (x, v))).comp
        ((ContinuousLinearMap.inr ℝ ℝ Phase).comp (ContinuousLinearMap.inr ℝ Space Space))) v := by
    have hin : HasFDerivAt (fun w : Space => ((t, (x, w)) : ℝ × Phase))
        ((ContinuousLinearMap.inr ℝ ℝ Phase).comp (ContinuousLinearMap.inr ℝ Space Space)) v := by
      have a : HasFDerivAt (fun w : Space => ((x, w) : Phase))
          (ContinuousLinearMap.inr ℝ Space Space) v := hasFDerivAt_prodMk_right x v
      exact (hasFDerivAt_prodMk_right t (x, v)).comp v a
    exact hd.comp v hin
  rw [h1.deriv, h2.fderiv, h3.fderiv]
  simp only [ContinuousLinearMap.comp_apply, ContinuousLinearMap.inr_apply,
    ContinuousLinearMap.inl_apply]
  rw [← map_add, ← map_add]
  simp

end Rev
