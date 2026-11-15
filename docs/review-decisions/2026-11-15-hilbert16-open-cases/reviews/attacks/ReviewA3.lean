import HilbertDrafts.Hilbert16Open

open HilbertDrafts.Hilbert16 HilbertDrafts.Hilbert16Classification HilbertDrafts.Hilbert16Open

namespace ReviewA

noncomputable def fermat : MvPolynomial (Fin 4) ℝ := ∑ i : Fin 4, MvPolynomial.X i ^ 5

theorem fermat_admissible : Admissible 3 5 true fermat := by
  refine ⟨?_, ?_, fun _ => ?_⟩
  · intro h
    have h1 := congrArg (MvPolynomial.eval (fun _ => (1 : ℝ))) h
    simp [fermat] at h1
  · unfold fermat
    exact MvPolynomial.IsHomogeneous.sum _ _ _
      (fun i _ => MvPolynomial.isHomogeneous_X_pow i 5)
  · intro z hz _
    obtain ⟨i, hi⟩ : ∃ i, z i ≠ 0 := by
      by_contra hcon
      exact hz (funext fun j => by_contra fun hj => hcon ⟨j, hj⟩)
    refine ⟨i, ?_⟩
    have hd : MvPolynomial.pderiv i fermat = 5 * MvPolynomial.X i ^ 4 := by
      unfold fermat
      rw [map_sum, Finset.sum_eq_single i]
      · simp [MvPolynomial.pderiv_X]
      · intro j _ hji
        simp [MvPolynomial.pderiv_X, hji]
      · simp
    rw [hd]
    simp [hi]

/-- A non-reduced "quintic" (five-fold plane) is rejected. -/
example : ¬ Admissible 3 5 true ((MvPolynomial.X 0 : MvPolynomial (Fin 4) ℝ) ^ 5) := by
  rintro ⟨-, -, h⟩
  obtain ⟨i, hi⟩ := h rfl (Pi.single 1 1)
    (by intro h0; have := congrFun h0 1; simp at this) (by simp)
  apply hi
  simp only [MvPolynomial.pderiv_pow, MvPolynomial.pderiv_X, MvPolynomial.eval₂_mul,
    MvPolynomial.eval₂_pow, MvPolynomial.eval₂_X]
  have h01 : (Pi.single 1 1 : Fin (3 + 1) → ℂ) 0 = 0 := by simp
  rw [h01]
  simp

end ReviewA
