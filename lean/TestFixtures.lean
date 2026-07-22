import Mathlib
import FormalConjecturesUtil.Answer

set_option warn.sorry false

open Google

namespace VerifierFixtures

theorem direct : True := by
  sorry

theorem propAnswer : answer(sorry) ↔ True := by
  sorry

theorem natAnswer : answer(sorry) = 4 := by
  sorry

theorem intAnswer : answer(sorry) = (-2 : ℤ) := by
  sorry

end VerifierFixtures
