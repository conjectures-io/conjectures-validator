import Mathlib

set_option autoImplicit false
set_option relaxedAutoImplicit false

namespace IndependentCollatz

open Filter Topology

/-- Ordinary clock, matching Yolcu–Aaronson–Heule's C. -/
def ordinaryNat (n : ℕ) : ℕ :=
  if n % 2 = 0 then n / 2 else 3 * n + 1

/-- C12: every positive orbit reaches 1 or one of the two specified odd residues. -/
def ModEightEscape : Prop :=
  ∀ n : ℕ, 0 < n → ∃ k : ℕ,
    ordinaryNat^[k] n = 1 ∨
    ordinaryNat^[k] n % 8 = 5 ∨ ordinaryNat^[k] n % 8 = 7

end IndependentCollatz
