import PartialCollatz

set_option autoImplicit false
namespace IndependentCollatz

-- Clock and first-passage controls, including rejection of a later return.
example : ordinaryNat 3 = 10 ∧ shortcutNat 3 = 5 := by decide
example : FirstSqrtPassage 16 2 := by
  norm_num [FirstSqrtPassage, Function.iterate_succ_apply, shortcutNat]
  intro j hj
  interval_cases j <;> norm_num [Function.iterate_succ_apply, shortcutNat]
example : ¬ FirstSqrtPassage 16 3 := by
  intro h
  have := h.2 2 (by decide)
  norm_num [Function.iterate_succ_apply, shortcutNat] at this
example : FirstSqrtPassage 1 0 := by simp [FirstSqrtPassage]

-- The exact published masses in Tao's paragraph following Lemma 1.12.
example : syracuseMass 1 0 = 0 ∧ syracuseMass 1 1 = 1/3 ∧
    syracuseMass 1 2 = 2/3 := by norm_num [syracuseMass, Finset.sum_Icc_succ_top]

-- No research assertion is used as an axiom or proved here.
#print axioms ModEightEscape
#print axioms SqrtPassageGaussian
#print axioms ExponentialSyracuseMixing
end IndependentCollatz
