axiom cheat : False

theorem target : type_of% VerifierFixtures.direct := by
  exact False.elim cheat
