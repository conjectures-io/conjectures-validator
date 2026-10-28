import FormalConjecturesUtil.Attributes.Basic

namespace CatalogSafety

theorem witness_exists : ∃ n : Nat, n = 1 := by sorry
noncomputable def chosen : Nat := Classical.choose witness_exists

-- The statement itself has no syntactic sorry, but its definition depends on one.
@[category research open, AMS 11]
theorem hidden_statement_hole : chosen = 1 := by sorry

def value_question.answer : Nat := 7

-- Even a filled answer definition must not turn a value question into a fixed reward.
@[category research open, question value, AMS 11]
theorem value_question : 1 + 1 = value_question.answer := by sorry

@[category research open, AMS 11]
theorem ordinary_proposition : ∀ n : Nat, n + 0 = n := by sorry

end CatalogSafety
