import HilbertDrafts.Hilbert16Open

open HilbertDrafts.Hilbert16 HilbertDrafts.Hilbert16Classification HilbertDrafts.Hilbert16Open

#synth TopologicalSpace (Projective 3)
#synth TopologicalSpace (Projectivization ℝ (Fin 4 → ℝ))

example (n : ℕ) : projectiveTopology n =
    TopologicalSpace.coinduced (Projectivization.mk' ℝ)
      (instTopologicalSpaceSubtype : TopologicalSpace {v : Fin (n + 1) → ℝ // v ≠ 0}) := rfl

example : (instTopologicalSpaceSubtype : TopologicalSpace {v : Fin (3 + 1) → ℝ // v ≠ 0}) =
    TopologicalSpace.induced Subtype.val Pi.topologicalSpace := rfl

example : (inferInstance : TopologicalSpace (Projectivization ℝ (Fin 4 → ℝ))) =
    projectiveTopology 3 := rfl

set_option pp.explicit true in
#print HilbertDrafts.Hilbert16Open.CycleBound
