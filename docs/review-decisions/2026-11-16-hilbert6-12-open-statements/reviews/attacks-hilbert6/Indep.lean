import HilbertDrafts.Hilbert6MeanField

open HilbertDrafts.Hilbert6 HilbertDrafts.Hilbert6MeanField MeasureTheory Filter
open scoped Topology ENNReal

namespace Rev

/-- `productLaw` is literally the law of `N + 1` independent samples of the
one-particle law with density `f₀`. -/
theorem productLaw_eq_pi (N : ℕ) {f₀ : Phase → ℝ} (hnn : ∀ z, 0 ≤ f₀ z) (hint : Integrable f₀) :
    productLaw N f₀ = Measure.pi (fun _ : Fin (N + 1) =>
      (volume : Measure Phase).withDensity (fun z => ENNReal.ofReal (f₀ z))) := by
  have : IsFiniteMeasure ((volume : Measure Phase).withDensity (fun z => ENNReal.ofReal (f₀ z))) :=
    isFiniteMeasure_withDensity_ofReal hint.2
  symm
  apply Measure.pi_eq
  intro s hs
  unfold productLaw
  rw [withDensity_apply _ (MeasurableSet.univ_pi hs), ← lintegral_indicator (MeasurableSet.univ_pi hs)]
  have h1 : ∀ z : Configuration (N + 1),
      (Set.pi Set.univ s).indicator (fun z => ENNReal.ofReal (∏ i, f₀ (z i))) z =
        ENNReal.ofReal (∏ i, (s i).indicator f₀ (z i)) := by
    intro z
    by_cases hz : z ∈ Set.pi Set.univ s
    · rw [Set.indicator_of_mem hz]
      congr 1
      refine Finset.prod_congr rfl fun i _ => ?_
      rw [Set.indicator_of_mem (hz i (Set.mem_univ i))]
    · rw [Set.indicator_of_notMem hz]
      simp only [Set.mem_pi, Set.mem_univ, forall_const, not_forall] at hz
      obtain ⟨i, hi⟩ := hz
      rw [Finset.prod_eq_zero (Finset.mem_univ i) (Set.indicator_of_notMem hi _)]
      simp
  simp_rw [h1]
  have hI : Integrable (fun z : Configuration (N + 1) => ∏ i, (s i).indicator f₀ (z i)) :=
    Integrable.fin_nat_prod (μ := fun _ => (volume : Measure Phase))
      (f := fun i => (s i).indicator f₀) (fun i => hint.indicator (hs i))
  rw [← ofReal_integral_eq_lintegral_ofReal hI
    (Eventually.of_forall fun z => Finset.prod_nonneg fun i _ =>
      Set.indicator_nonneg (fun z _ => hnn z) _)]
  rw [integral_fin_nat_prod_volume_eq_prod (E := fun _ : Fin (N + 1) => Phase)
    (fun i => (s i).indicator f₀)]
  rw [ENNReal.ofReal_prod_of_nonneg (fun i _ => integral_nonneg fun z =>
    Set.indicator_nonneg (fun z _ => hnn z) _)]
  refine Finset.prod_congr rfl fun i _ => ?_
  rw [withDensity_apply _ (hs i), integral_indicator (hs i),
    ofReal_integral_eq_lintegral_ofReal hint.integrableOn (Eventually.of_forall hnn)]

end Rev
