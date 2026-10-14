import Mathlib

set_option autoImplicit false
set_option relaxedAutoImplicit false

namespace IndependentCollatz

open Filter Topology

/-- Shortcut clock: an odd multiplication and its following halving are one step. -/
def shortcutNat (n : ℕ) : ℕ :=
  if n % 2 = 0 then n / 2 else (3 * n + 1) / 2

/-- First passage to a value at most sqrt(n); comparisons are exact integer squares. -/
def FirstSqrtPassage (n k : ℕ) : Prop :=
  (shortcutNat^[k] n)^2 ≤ n ∧
  ∀ j : ℕ, j < k → n < (shortcutNat^[j] n)^2

/-- Natural logarithms. The variance coefficient is (log 3)^2/(log(4/3))^3. -/
noncomputable def sqrtPassageBound (z : ℝ) (n : ℕ) : ℝ :=
  Real.log (n : ℝ) / Real.log (4 / 3 : ℝ) +
    z * Real.sqrt ((Real.log (3 : ℝ))^2 /
      (Real.log (4 / 3 : ℝ))^3 * Real.log (n : ℝ))

/-- Standard normal cumulative distribution function. -/
noncomputable def normalCDF (z : ℝ) : ℝ :=
  (∫ x in Set.Iic z, Real.exp (-(x^2) / 2)) / Real.sqrt (2 * Real.pi)

open Classical in
/-- All starts 1,...,N are sampled; a start without a finite passage is not counted. -/
noncomputable def sqrtPassageCount (N : ℕ) (z : ℝ) : ℕ :=
  ((Finset.Icc 1 N).filter fun n => ∃ k : ℕ,
    FirstSqrtPassage n k ∧ (k : ℝ) ≤ sqrtPassageBound z n).card

/-- C13: a proposed Gaussian fluctuation law for actual integer trajectories. -/
def SqrtPassageGaussian : Prop :=
  ∀ z : ℝ, Tendsto (fun N : ℕ => (sqrtPassageCount N z : ℝ) / (N : ℝ))
    atTop (𝓝 (normalCDF z))

end IndependentCollatz
