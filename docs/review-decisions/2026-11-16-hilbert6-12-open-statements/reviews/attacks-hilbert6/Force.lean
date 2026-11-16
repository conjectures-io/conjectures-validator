import HilbertDrafts.Hilbert6MeanField

open HilbertDrafts.Hilbert6 HilbertDrafts.Hilbert6MeanField MeasureTheory Filter
open scoped Topology ENNReal

namespace Rev

theorem measurable_coulomb : Measurable coulomb := by
  unfold coulomb
  exact ((measurable_norm.pow_const 3).inv).smul measurable_id

/-- The kernel is locally integrable on three-space. -/
theorem locallyIntegrable_coulomb : LocallyIntegrable coulomb (volume : Measure Space) := by
  have hdim : Module.finrank ℝ Space = 3 := by simp
  refine locallyIntegrable_of_norm_le_rpow (C := 1) (α := 2) (by rw [hdim]; norm_num)
    (by rw [hdim]; norm_num) (Eventually.of_forall fun x => ?_)
    measurable_coulomb.aestronglyMeasurable
  by_cases hx : x = 0
  · subst hx
    simp [coulomb]
  · rw [norm_coulomb hx, one_mul, Real.rpow_neg (norm_nonneg x)]
    norm_num

/-- Clause 4 of `IsVlasovPoissonSolution` holds for every continuous compactly
supported profile, at every field point: the six-dimensional force integrand
is genuinely integrable. -/
theorem integrable_force (g : Phase → ℝ) (hg : Continuous g) (hc : HasCompactSupport g)
    (x : Space) : Integrable (fun z : Phase => g z • coulomb (x - z.1)) := by
  obtain ⟨C, hC⟩ := hc.exists_bound_of_continuous hg
  obtain ⟨R, hR⟩ := hc.isCompact.isBounded.subset_closedBall (0 : Phase)
  have hC0 : 0 ≤ C := (norm_nonneg _).trans (hC 0)
  set K : Set Space := Metric.closedBall 0 (R + ‖x‖)
  have hK : Integrable (K.indicator coulomb) (volume : Measure Space) :=
    (integrable_indicator_iff Metric.isClosed_closedBall.measurableSet).mpr
      (locallyIntegrable_coulomb.integrableOn_isCompact (isCompact_closedBall 0 _))
  have hF : Integrable (fun y : Space => ‖K.indicator coulomb (x - y)‖) :=
    (hK.comp_sub_left x).norm
  have hG : Integrable (fun w : Space => C * (Metric.closedBall (0 : Space) R).indicator 1 w) :=
    (((integrable_indicator_iff Metric.isClosed_closedBall.measurableSet).mpr
      (integrableOn_const (C := (1 : ℝ)) (by
        exact (isCompact_closedBall (0 : Space) R).measure_lt_top.ne))).const_mul C)
  have hprod : Integrable (fun z : Phase =>
      ‖K.indicator coulomb (x - z.1)‖ * (C * (Metric.closedBall (0 : Space) R).indicator 1 z.2)) :=
    hF.mul_prod hG
  refine hprod.mono' ((hg.measurable.smul
    (measurable_coulomb.comp (measurable_const.sub measurable_fst))).aestronglyMeasurable)
    (Eventually.of_forall fun z => ?_)
  by_cases hz : g z = 0
  · rw [hz, zero_smul, norm_zero]
    exact mul_nonneg (norm_nonneg _) (mul_nonneg hC0 (Set.indicator_nonneg (fun _ _ => zero_le_one) _))
  · have hzR : ‖z‖ ≤ R := by
      have := hR (subset_tsupport g (Function.mem_support.mpr hz))
      simpa using this
    have h1 : ‖z.1‖ ≤ R := (norm_fst_le z).trans hzR
    have h2 : ‖z.2‖ ≤ R := (norm_snd_le z).trans hzR
    have hmem : x - z.1 ∈ K := by
      simp only [K, Metric.mem_closedBall, dist_zero_right]
      calc ‖x - z.1‖ ≤ ‖x‖ + ‖z.1‖ := norm_sub_le _ _
        _ ≤ R + ‖x‖ := by linarith
    have hmem2 : z.2 ∈ Metric.closedBall (0 : Space) R := by simpa using h2
    rw [Set.indicator_of_mem hmem, Set.indicator_of_mem hmem2, norm_smul]
    simp only [Pi.one_apply, mul_one]
    rw [mul_comm]
    exact mul_le_mul_of_nonneg_left (hC z) (norm_nonneg _)

end Rev
