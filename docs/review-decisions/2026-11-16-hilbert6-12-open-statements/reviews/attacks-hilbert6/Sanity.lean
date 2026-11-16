import HilbertDrafts.Hilbert6MeanField

open HilbertDrafts.Hilbert6 HilbertDrafts.Hilbert6MeanField MeasureTheory Filter
open scoped Topology ENNReal

namespace Rev

-- volume on Phase is the product measure; on configurations the finite product
example : (volume : Measure Phase) = (volume : Measure Space).prod volume := rfl
example (N : ℕ) : (volume : Measure (Configuration (N + 1))) =
    Measure.pi (fun _ => (volume : Measure Phase)) := rfl
-- volume on Space is Lebesgue
example : MeasurePreserving (@WithLp.ofLp 2 (Fin 3 → ℝ)) (volume : Measure Space) volume :=
  PiLp.volume_preserving_ofLp (Fin 3)
example (z : Phase) : ‖z‖ = max ‖z.1‖ ‖z.2‖ := Prod.norm_def z
example (n : ℕ) : ((n : ℕ) : WithTop ℕ∞) = ((n : ℕ∞) : WithTop ℕ∞) := rfl
example (f : Phase → ℝ) : (∀ n : ℕ, ContDiff ℝ n f) ↔ ContDiff ℝ (⊤ : ℕ∞) f :=
  contDiff_infty.symm

/-- `SmoothDensity` is satisfiable. -/
theorem smoothDensity_exists : ∃ f₀ : Phase → ℝ, SmoothDensity f₀ := by
  let b : ContDiffBump (0 : Phase) := ⟨1, 2, by norm_num, by norm_num⟩
  exact ⟨b.normed volume, fun n => b.contDiff_normed, b.hasCompactSupport_normed,
    b.nonneg_normed, b.integrable_normed, b.integral_normed⟩

/-- `productLaw` is a probability measure for every admissible density. -/
theorem productLaw_univ (N : ℕ) {f₀ : Phase → ℝ} (h : SmoothDensity f₀) :
    productLaw N f₀ Set.univ = 1 := by
  obtain ⟨-, -, hnn, hint, hone⟩ := h
  unfold productLaw
  rw [withDensity_apply _ MeasurableSet.univ, Measure.restrict_univ]
  have hI : Integrable (fun z : Configuration (N + 1) => ∏ i, f₀ (z i)) :=
    Integrable.fin_nat_prod (μ := fun _ => (volume : Measure Phase)) (fun _ => hint)
  rw [← ofReal_integral_eq_lintegral_ofReal hI
    (Eventually.of_forall fun z => Finset.prod_nonneg fun i _ => hnn (z i))]
  have := integral_fin_nat_prod_volume_eq_prod (E := fun _ : Fin (N + 1) => Phase)
    (fun _ => f₀)
  rw [this]
  simp [hone]

end Rev
