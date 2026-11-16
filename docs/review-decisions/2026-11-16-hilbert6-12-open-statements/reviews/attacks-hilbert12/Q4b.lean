import Review.Q4

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda NumberField
open scoped NumberField

namespace Review

/-- The cyclotomic field is a cyclotomic extension for the canonical rational algebra
structure, the one the target uses. -/
theorem cyc_inst (n : ℕ) :
    @IsCyclotomicExtension {n} ℚ (CyclotomicField n ℚ) _ _ DivisionRing.toRatAlgebra := by
  have hinst : @IsCyclotomicExtension {n} ℚ (CyclotomicField n ℚ) _ _
      (CyclotomicField.algebra n ℚ) := inferInstance
  have halg : (DivisionRing.toRatAlgebra : Algebra ℚ (CyclotomicField n ℚ)) =
      CyclotomicField.algebra n ℚ := Subsingleton.elim _ _
  rw [halg]
  exact hinst

/-- Degree of a cyclotomic field, with `Module.finrank ℚ K` elaborated as in the target. -/
theorem cyc_degree (K : Type) [Field K] [NumberField K] (n : ℕ) [NeZero n]
    [IsCyclotomicExtension {n} ℚ K] : Module.finrank ℚ K = n.totient :=
  IsCyclotomicExtension.Rat.finrank n K

/-- The two side conditions are satisfiable: the fifth cyclotomic field has degree four. -/
theorem side_conditions_satisfiable :
    ∃ (K : Type) (_ : Field K) (_ : NumberField K),
      Module.finrank ℚ K ≠ 1 ∧ ¬ (Module.finrank ℚ K = 2 ∧ IsEmpty (K →+* ℝ)) := by
  have := cyc_inst 5
  have h4 := cyc_degree (CyclotomicField 5 ℚ) 5
  rw [Nat.totient_prime (by norm_num : Nat.Prime 5)] at h4
  refine ⟨CyclotomicField 5 ℚ, inferInstance, inferInstance, ?_, ?_⟩
  · intro h
    exact absurd (h4.symm.trans h) (by norm_num)
  · rintro ⟨h, _⟩
    exact absurd (h4.symm.trans h) (by norm_num)

/-- The universal target implies the Question 4 target, by a few lines. -/
theorem h12_imp_q4 : Hilbert12LambdaGeometric → BorgerDeSmitQuestion4 := by
  intro h
  obtain ⟨K, i, j, h1, h2⟩ := side_conditions_satisfiable
  exact ⟨K, i, j, h1, h2, @h K i j⟩

/-- A refutation of the Question 4 target refutes the universal target. -/
theorem not_q4_imp_not_h12 : ¬ BorgerDeSmitQuestion4 → ¬ Hilbert12LambdaGeometric :=
  mt h12_imp_q4

/-- The Gaussian field is excluded by the second side condition. -/
theorem gaussian_excluded :
    Module.finrank ℚ (CyclotomicField 4 ℚ) = 2 ∧ IsEmpty (CyclotomicField 4 ℚ →+* ℝ) := by
  have := cyc_inst 4
  refine ⟨?_, ?_⟩
  · have h := cyc_degree (CyclotomicField 4 ℚ) 4
    have ht : Nat.totient 4 = 2 := by decide
    exact h.trans ht
  · rw [isEmpty_real_iff]
    have : IsTotallyComplex (CyclotomicField 4 ℚ) :=
      IsCyclotomicExtension.Rat.isTotallyComplex (n := 4) (CyclotomicField 4 ℚ) (by norm_num)
    intro w
    rw [InfinitePlace.not_isReal_iff_isComplex]
    exact this.isComplex w

/-- So the Gaussian field cannot be a witness of the Question 4 target, in this presentation
or (by `iso_imagQuad_excluded`) in any other. -/
theorem gaussian_not_witness :
    ¬ (Module.finrank ℚ (CyclotomicField 4 ℚ) ≠ 1 ∧
      ¬ (Module.finrank ℚ (CyclotomicField 4 ℚ) = 2 ∧ IsEmpty (CyclotomicField 4 ℚ →+* ℝ)) ∧
      LambdaGeometric (CyclotomicField 4 ℚ)) :=
  fun h => h.2.1 gaussian_excluded

#print axioms side_conditions_satisfiable
#print axioms h12_imp_q4
#print axioms not_q4_imp_not_h12
#print axioms gaussian_excluded
#print axioms q4_iff
#print axioms finrank_eq_one_iff
#print axioms isEmpty_real_iff
#print axioms finrank_any_module
#print axioms iso_rat_excluded
#print axioms iso_imagQuad_excluded

end Review
