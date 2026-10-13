import Mathlib

set_option autoImplicit false
set_option relaxedAutoImplicit false

namespace IndependentCollatz

open Filter Topology

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

/-- L1 distance to the uniform lift of the coarse mass, not to global uniformity. -/
def syracuseOscillation (m n : ℕ) : ℚ :=
  ∑ r ∈ Finset.range (3^n),
    |syracuseMass n r - syracuseMass m (r % (3^m)) / (3 : ℚ)^(n-m)|

/-- C14: exponential strengthening of Tao's fine-scale mixing estimate. -/
def ExponentialSyracuseMixing : Prop :=
  ∃ C c : ℝ, 0 < C ∧ 0 < c ∧ ∀ m n : ℕ, 1 ≤ m → m ≤ n →
    (syracuseOscillation m n : ℝ) ≤ C * Real.exp (-c * (m : ℝ))

end IndependentCollatz
