import HilbertDrafts.Hilbert6MeanField

/-!
Machine-checked non-vacuity witness for the mean-field statement. It shows the
class of admissible initial densities is not empty. It is not progress on the
conjecture.
-/

namespace HilbertDrafts.Hilbert6MeanFieldChecks

open HilbertDrafts.Hilbert6 HilbertDrafts.Hilbert6MeanField MeasureTheory

noncomputable section

/-- A smooth compactly supported probability density exists, so the statement
does not quantify over an empty class of initial data. -/
theorem exists_smoothDensity : ∃ f₀ : Phase → ℝ, SmoothDensity f₀ := by
  let φ : ContDiffBump (0 : Phase) := default
  refine ⟨φ.normed volume, fun n => ?_, φ.hasCompactSupport_normed, fun z => φ.nonneg_normed z,
    φ.integrable_normed, φ.integral_normed⟩
  exact_mod_cast φ.contDiff_normed (μ := volume) (n := (n : ℕ∞))

end

end HilbertDrafts.Hilbert6MeanFieldChecks
