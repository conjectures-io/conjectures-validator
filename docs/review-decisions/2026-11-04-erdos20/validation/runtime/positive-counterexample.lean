theorem target : ¬ (∀ n : ℕ, n + 1 = n) := by
  intro claim
  have impossible := claim 0
  omega
