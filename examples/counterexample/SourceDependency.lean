theorem target : ¬ (∀ n : ℕ, n + 1 = n) := by
  have admitted := VerifierCounterexampleFixtures.falseUniversal
  intro claim
  have impossible := claim 0
  omega
