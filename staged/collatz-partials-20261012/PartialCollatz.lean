import Mathlib

set_option autoImplicit false
set_option relaxedAutoImplicit false

/- Three questions, not asserted theorems. Each target has its own definition and
   can be extracted with only its dependencies. No convergence hypothesis or
   previously proposed Collatz target is imported. -/
namespace IndependentCollatz

open Filter Topology

-- BEGIN ordinaryNat
/-- Ordinary clock, matching Yolcu–Aaronson–Heule's C. -/
def ordinaryNat (n : ℕ) : ℕ :=
  if n % 2 = 0 then n / 2 else 3 * n + 1
-- END ordinaryNat

-- BEGIN ModEightEscape
/-- C12: every positive orbit reaches 1 or one of the two specified odd residues. -/
def ModEightEscape : Prop :=
  ∀ n : ℕ, 0 < n → ∃ k : ℕ,
    ordinaryNat^[k] n = 1 ∨
    ordinaryNat^[k] n % 8 = 5 ∨ ordinaryNat^[k] n % 8 = 7
-- END ModEightEscape

-- BEGIN shortcutNat
/-- Shortcut clock: an odd multiplication and its following halving are one step. -/
def shortcutNat (n : ℕ) : ℕ :=
  if n % 2 = 0 then n / 2 else (3 * n + 1) / 2
-- END shortcutNat

-- BEGIN FirstSqrtPassage
/-- First passage to a value at most sqrt(n); comparisons are exact integer squares. -/
def FirstSqrtPassage (n k : ℕ) : Prop :=
  (shortcutNat^[k] n)^2 ≤ n ∧
  ∀ j : ℕ, j < k → n < (shortcutNat^[j] n)^2
-- END FirstSqrtPassage

-- BEGIN sqrtPassageBound
/-- Natural logarithms. The variance coefficient is (log 3)^2/(log(4/3))^3. -/
noncomputable def sqrtPassageBound (z : ℝ) (n : ℕ) : ℝ :=
  Real.log (n : ℝ) / Real.log (4 / 3 : ℝ) +
    z * Real.sqrt ((Real.log (3 : ℝ))^2 /
      (Real.log (4 / 3 : ℝ))^3 * Real.log (n : ℝ))
-- END sqrtPassageBound

-- BEGIN normalCDF
/-- Standard normal cumulative distribution function. -/
noncomputable def normalCDF (z : ℝ) : ℝ :=
  (∫ x in Set.Iic z, Real.exp (-(x^2) / 2)) / Real.sqrt (2 * Real.pi)
-- END normalCDF

-- BEGIN sqrtPassageCount
open Classical in
/-- All starts 1,...,N are sampled; a start without a finite passage is not counted. -/
noncomputable def sqrtPassageCount (N : ℕ) (z : ℝ) : ℕ :=
  ((Finset.Icc 1 N).filter fun n => ∃ k : ℕ,
    FirstSqrtPassage n k ∧ (k : ℝ) ≤ sqrtPassageBound z n).card
-- END sqrtPassageCount

-- BEGIN SqrtPassageGaussian
/-- C13: a proposed Gaussian fluctuation law for actual integer trajectories. -/
def SqrtPassageGaussian : Prop :=
  ∀ z : ℝ, Tendsto (fun N : ℕ => (sqrtPassageCount N z : ℝ) / (N : ℝ))
    atTop (𝓝 (normalCDF z))
-- END SqrtPassageGaussian

-- BEGIN syracuseMass
/-- Exact rational mass of Tao's n-Syracuse offset modulo 3^n.
The geometric exponent law is summed by residue classes with period 2*3^n.
The base case is the sole residue modulo 1. -/
def syracuseMass : ℕ → ℕ → ℚ
  | 0, r => if r = 0 then 1 else 0
  | n + 1, r =>
      let q := 3^(n+1)
      let L := 2 * 3^n
      let u := (q+1)/2
      ∑ y ∈ Finset.range (3^n), ∑ a ∈ Finset.Icc 1 L,
        if ((3*y+1)*u^a) % q = r then
          syracuseMass n y * ((2 : ℚ)^(L-a) / ((2 : ℚ)^L-1))
        else 0
-- END syracuseMass

-- BEGIN syracuseOscillation
/-- L1 distance to the uniform lift of the coarse mass, not to global uniformity. -/
def syracuseOscillation (m n : ℕ) : ℚ :=
  ∑ r ∈ Finset.range (3^n),
    |syracuseMass n r - syracuseMass m (r % (3^m)) / (3 : ℚ)^(n-m)|
-- END syracuseOscillation

-- BEGIN ExponentialSyracuseMixing
/-- C14: exponential strengthening of Tao's fine-scale mixing estimate. -/
def ExponentialSyracuseMixing : Prop :=
  ∃ C c : ℝ, 0 < C ∧ 0 < c ∧ ∀ m n : ℕ, 1 ≤ m → m ≤ n →
    (syracuseOscillation m n : ℝ) ≤ C * Real.exp (-c * (m : ℝ))
-- END ExponentialSyracuseMixing

end IndependentCollatz
