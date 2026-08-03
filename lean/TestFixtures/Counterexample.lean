import Mathlib
import FormalConjecturesUtil.Attributes.Basic

set_option warn.sorry false

namespace VerifierCounterexampleFixtures

@[category research open]
theorem falseUniversal : ∀ n : ℕ, n + 1 = n := by
  sorry

end VerifierCounterexampleFixtures
