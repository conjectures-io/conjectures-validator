# Elaborated target types

Lean 4.33.1; audited upstream snapshot; answer placeholders elaborated with `always_true`. These are statements, not proved results.

## 1. Odd perfect numbers

```lean
∀ (n : ℕ), n.Perfect → Even n
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 2. Infinitely many Mersenne primes

```lean
True ↔ {p | Nat.Prime (mersenne p)}.Infinite
```

Statement axioms: `propext`.

## 3. Infinitely many Fermat primes

```lean
True ↔ Infinite ↑{n | Prime n.fermatNumber}
```

Statement axioms: `propext`.

## 4. Perfect cuboid

```lean
True ↔ ∃ a b c, EulerBrick.IsPerfectCuboid a b c
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 5. Euler–Mascheroni constant irrationality

```lean
True ↔ Irrational Real.eulerMascheroniConstant
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 6. Goldbach conjecture

```lean
True ↔ ∀ (n : ℕ), 2 < n → Even n → ∃ p q, Prime p ∧ Prime q ∧ n = p + q
```

Statement axioms: `propext`.

## 7. Euler's idoneal-number completeness

```lean
True ↔ ∀ (n : ℕ), Idoneal.IsIdoneal n → n ∈ Idoneal.knownIdonealNumbers
```

Statement axioms: `propext, Quot.sound, Classical.choice`.

## 8. Legendre conjecture

```lean
True ↔ ∀ n ≥ 1, ∃ p ∈ Set.Ioo (n ^ 2) ((n + 1) ^ 2), Nat.Prime p
```

Statement axioms: `propext`.

## 9. Gauss real quadratic class-number-one problem

```lean
{d | Squarefree d ∧ d > 1 ∧ ClassNumberProblem.IsClassNumberOne d}.Infinite
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 10. Twin prime conjecture

```lean
True ↔ {p | Prime p ∧ Prime (p + 2)}.Infinite
```

Statement axioms: `propext`.

## 11. Kummer–Vandiver conjecture

```lean
∀ (p : ℕ+), p.Prime → ¬↑p ∣ NumberField.classNumber ↥(NumberField.maximalRealSubfield (CyclotomicField ↑p ℚ))
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 12. Infinitely many regular primes

```lean
RegularPrimes.RegularPrimeConjecture
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 13. Pollock's tetrahedral-number conjecture

```lean
∀ (N : ℕ), ∃ f, N = ∑ i, PollocksConjecture.tetrahedral (f i)
```

Statement axioms: `propext, Quot.sound, Classical.choice`.

## 14. Bunyakovsky conjecture

```lean
∀ (f : Polynomial ℤ),
  BunyakovskyCondition f ∧ SchinzelCondition {f} → Infinite ↑{n | Nat.Prime (Polynomial.eval (↑n) f).natAbs}
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 15. Riemann hypothesis

```lean
RiemannHypothesis
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 16. Catalan's constant irrationality

```lean
True ↔ Irrational catalanConstant
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 17. Brocard–Ramanujan factorial problem

```lean
True ↔ {n | ∃ m, n.factorial + 1 = m ^ 2} = {4, 5, 7}
```

Statement axioms: `propext`.

## 18. Catalan–Mersenne primality question

```lean
True ↔ ∀ n ≥ 5, Nat.Prime (Mersenne.catalanMersenne n)
```

Statement axioms: `propext`.

## 19. Oppermann conjecture

```lean
∀ (x : ℕ),
  2 ≤ x → (∃ p ∈ Finset.Ioo (x * (x - 1)) (x ^ 2), Nat.Prime p) ∧ ∃ p ∈ Finset.Ioo (x ^ 2) (x * (x + 1)), Nat.Prime p
```

Statement axioms: `propext, Quot.sound, Classical.choice`.

## 20. Inverse Galois problem

```lean
∀ {G : Type u_1} [Fintype G] [inst : Group G], InverseGalois.IsRealizable ℚ G
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 21. Hadamard matrix existence

```lean
∀ (k : ℕ), ∃ M, Hadamard.IsHadamard M
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 22. Lemoine conjecture

```lean
∀ (n : ℕ), 6 < n → Odd n → ∃ p q, Nat.Prime p ∧ Nat.Prime q ∧ p + 2 * q = n
```

Statement axioms: `propext`.

## 23. No-three-in-line problem

```lean
True ↔ ∀ᶠ (N : ℕ) in Filter.atTop, ¬Green72.NoKInLineFor 3 N
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 24. Dickson conjecture

```lean
∀ (fs : Finset (Polynomial ℤ)),
  (∀ f ∈ fs, f.degree = 1 ∧ BunyakovskyCondition f) →
    SchinzelCondition fs → Infinite ↑{n | ∀ f ∈ fs, Nat.Prime (Polynomial.eval (↑n) f).natAbs}
```

Statement axioms: `propext, Quot.sound, Classical.choice`.

## 25. Brocard's prime-square conjecture

```lean
∀ (n : ℕ),
  1 ≤ n → 4 ≤ (Finset.filter Nat.Prime (Finset.Ioo (Nat.nth Nat.Prime n ^ 2) (Nat.nth Nat.Prime (n + 1) ^ 2))).card
```

Statement axioms: `propext, Quot.sound, Classical.choice`.

## 26. Carmichael totient conjecture

```lean
∀ ⦃n : ℕ⦄, 0 < n → CarmichaelTotient.CarmichaelTotientFor n
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 27. Normality of π in base 10

```lean
NormalNumber.IsNormalInBase 10 Real.pi
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 28. Inscribed square problem

```lean
True ↔
  ∀ (γ : Circle → EuclideanSpace ℝ (Fin 2)),
    Topology.IsEmbedding γ → ∃ t₁ t₂ t₃ t₄, InscribedSquare.IsRectangle (γ t₁) (γ t₂) (γ t₃) (γ t₄) 1
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 29. Infinitely many primes n²+1

```lean
True ↔ {n | Prime (n ^ 2 + 1)}.Infinite
```

Statement axioms: `propext`.

## 30. Gauss circle error bound

```lean
∃ o, ∃ (_ : Filter.Tendsto o Filter.atTop (nhds 0)), GaussCircleProblem.E =O[Filter.atTop] fun r => r ^ (1 / 2 + o r)
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 31. Goormaghtigh conjecture

```lean
∀ (N : ℕ), Goormaghtigh.IsGoormaghtighNumber N → N = 31 ∨ N = 8191
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 32. First Hardy–Littlewood conjecture

```lean
∀ {k : ℕ} (m : Fin k.succ → ℕ), HardyLittlewood.FirstHardyLittlewoodConjectureFor m
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 33. Second Hardy–Littlewood conjecture

```lean
∀ {x y : ℕ}, 2 ≤ x → 2 ≤ y → HardyLittlewood.SecondHardyLittlewoodConjectureFor x y
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 34. Artin primitive-root conjecture

```lean
∀ (a : ℤ), ¬IsSquare a → a ≠ -1 → ∃ x > 0, (ArtinPrimitiveRootsConjecture.S a).HasDensity x {p | Nat.Prime p}
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 35. Köthe conjecture

```lean
∀ {R : Type u_1} [inst : Ring R] (I J : Ideal R), Koethe.IsNil I → Koethe.IsNil J → Koethe.IsNil (I + J)
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 36. Lehmer totient problem

```lean
True ↔ ∃ n > 1, ¬Prime n ∧ n.totient ∣ n - 1
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 37. Lehmer Mahler-measure problem

```lean
∃ μ,
  ∀ (f : Polynomial ℤ),
    μ > 1 ∧ (LehmerMahlerMeasureProblem.mahlerMeasureZ f > 1 → LehmerMahlerMeasureProblem.mahlerMeasureZ f ≥ μ)
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 38. Borsuk problem in dimension four

```lean
Borsuk.BorsukConjecture 4
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 39. Erdős–Szekeres convex-polygon conjecture

```lean
True ↔ ∀ n ≥ 3, Erdos107.f n = 2 ^ (n - 2) + 1
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

## 40. Scholz addition-chain conjecture

```lean
True ↔ ∀ (n : ℕ), 0 < n → additionChainLength (2 ^ n - 1) ≤ n - 1 + additionChainLength n
```

Statement axioms: `propext, Classical.choice, Quot.sound`.

