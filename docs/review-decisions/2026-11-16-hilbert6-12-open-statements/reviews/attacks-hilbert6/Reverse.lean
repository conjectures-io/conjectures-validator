import HilbertDrafts.Hilbert6MeanField

open HilbertDrafts.Hilbert6 HilbertDrafts.Hilbert6MeanField MeasureTheory Filter
open scoped Topology ENNReal

namespace Rev

/-- Time reversal maps trajectories to trajectories, so requiring trajectories on
all of `ℝ` is the same as requiring forward trajectories from the reversed data. -/
theorem reverse_isCoulombTrajectory {N : ℕ} {z : ℝ → Configuration (N + 1)}
    (h : IsCoulombTrajectory z) :
    IsCoulombTrajectory (fun t i => ((z (-t) i).1, -(z (-t) i).2)) := by
  intro t
  refine ⟨fun i j hij => (h (-t)).1 i j hij, fun i => ⟨?_, ?_⟩⟩
  · have h1 := ((h (-t)).2 i).1
    have := h1.scomp t (hasDerivAt_neg t)
    rw [neg_one_smul] at this
    exact this
  · have h2 := ((h (-t)).2 i).2
    have := (h2.scomp t (hasDerivAt_neg t)).neg
    rw [neg_one_smul, neg_neg] at this
    exact this

/-- Newton's third law: total momentum of two particles is conserved
(a sign check on the kernel and on the `erase` sum). -/
theorem pair_momentum (q : Fin 2 → Space) :
    acceleration (N := 1) q 0 + acceleration (N := 1) q 1 = 0 := by
  have h0 : Finset.univ.erase (0 : Fin 2) = {1} := by decide
  have h1 : Finset.univ.erase (1 : Fin 2) = {0} := by decide
  unfold acceleration
  rw [h0, h1, Finset.sum_singleton, Finset.sum_singleton, ← smul_add,
    ← neg_sub (q 0) (q 1), coulomb_neg]
  simp

/-- Repulsion: the force on particle 0 points away from particle 1. -/
theorem pair_repulsive (q : Fin 2 → Space) (hq : q 0 ≠ q 1) :
    0 < inner ℝ (acceleration (N := 1) q 0) (q 0 - q 1) := by
  rw [acceleration_pair, coulomb, inner_smul_left, inner_smul_left, real_inner_self_eq_norm_sq]
  have : 0 < ‖q 0 - q 1‖ := norm_pos_iff.mpr (sub_ne_zero.mpr hq)
  simp only [RCLike.conj_to_real]
  positivity

end Rev
