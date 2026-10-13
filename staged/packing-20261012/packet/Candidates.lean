import FrozenSquareData
import FrozenCircleData

/- TEST statement definitions only. None of the eleven research targets is asserted as a theorem. -/
namespace Candidate11

def T (n : ℕ) : ℕ := if n % 2 = 0 then n / 2 else (3*n+1)/2
def orbit (n k : ℕ) : ℕ := T^[k] n
def FirstHit (n t : ℕ) : Prop :=
  0 < n ∧ orbit n t = 1 ∧ ∀ j < t, orbit n j ≠ 1
def oddCount (n t : ℕ) : ℕ :=
  ((Finset.range t).filter fun j => orbit n j % 2 = 1).card

-- T(n) ≥ n/2, so every positive k-preimage of a lies in [1,2^k*a].
-- A finset counts distinct starts, never paths or repeated visits.
def predecessors (a k : ℕ) : Finset ℕ :=
  (Finset.range (2^k*a+1)).filter fun n => 0 < n ∧ orbit n k = a
def C1 : Prop := ∀ a : ℕ, 0 < a → ¬ 3 ∣ a →
  ∃ K : ℕ, ∀ k ≥ K, 131^k ≤ 100^k * (predecessors a k).card
def C2 : Prop := ∀ B : ℕ, ∃ n t : ℕ,
  B < n ∧ 2 ≤ n ∧ FirstHit n t ∧ t ≤ 2 * oddCount n t
def C3 : Prop := ∀ n t : ℕ, FirstHit n t →
  2^t * 993 * 3^32 ≤ 2^61 * n * 3^(oddCount n t)
def C4 : Prop := ∀ B : ℕ, ∃ n k : ℕ,
  B < n ∧ 2 ≤ n ∧ n^8 < (orbit n k)^5

noncomputable def convergentUpTo (X : ℕ) : Finset ℕ := by
  classical
  exact (Finset.range (X+1)).filter fun n => 2 ≤ n ∧ ∃ t, FirstHit n t
-- FirstHit carries the true stopping time, avoiding a default tau on divergent starts.
noncomputable def exceptionalUpTo (X : ℕ) (ε : ℝ) : Finset ℕ := by
  classical
  exact (convergentUpTo X).filter fun n => ∃ t, FirstHit n t ∧
    ε < |(t : ℝ) / Real.log (n : ℝ) - 2 / Real.log (4/3 : ℝ)|
def C5 : Prop := ∀ ε : ℝ, 0 < ε → ∀ δ : ℝ, 0 < δ →
  ∃ X₀ : ℕ, ∀ X ≥ X₀, 0 < (convergentUpTo X).card ∧
    ((exceptionalUpTo X ε).card : ℝ) < δ * (convergentUpTo X).card

-- A concrete inverse-limit presentation of Z₂; x m is reduction modulo 2^m.
-- The bridge to the named Bernstein–Lagarias Phi must be reviewed before release.
structure TwoAdic where
  residue : ℕ → ℕ
  bounded : ∀ m, residue m < 2^m
  coherent : ∀ m, residue (m+1) % 2^m = residue m
def parityPrefix (n m : ℕ) : ℕ :=
  ∑ j ∈ Finset.range m, (orbit n j % 2) * 2^j
-- Q(x)=x iff its m parity bits equal its m low binary bits, for every m.
-- Fixed points of Q and Phi=Q⁻¹ coincide. No arbitrary conjugacy parameter.
def C6 : Prop := ∀ x : TwoAdic,
  x.residue 1 = 1 →
  (∀ m, parityPrefix (x.residue m) m = x.residue m) →
  (∀ m, x.residue m = 2^m-1) ∨
  (∀ m, (3 * x.residue m) % 2^m = 1 % 2^m)

abbrev Point := ℝ × ℝ
def normSq (p : Point) : ℝ := p.1^2 + p.2^2
def distSq (p q : Point) : ℝ := (p.1-q.1)^2 + (p.2-q.2)^2
def separated {N : ℕ} (r : ℝ) (p : Fin N → Point) : Prop :=
  ∀ i j, i ≠ j → (2*r)^2 ≤ distSq (p i) (p j)
def DiskPacking {N : ℕ} (r : ℝ) (p : Fin N → Point) : Prop :=
  0 < r ∧ r ≤ 1 ∧ (∀ i, normSq (p i) ≤ (1-r)^2) ∧ separated r p
def SquarePacking {N : ℕ} (r : ℝ) (p : Fin N → Point) : Prop :=
  0 < r ∧ (∀ i, r ≤ (p i).1 ∧ (p i).1 ≤ 1-r ∧
    r ≤ (p i).2 ∧ (p i).2 ≤ 1-r) ∧ separated r p
def DiskUpper (N : ℕ) (r : ℝ) : Prop :=
  ∀ s : ℝ, ∀ p : Fin N → Point, DiskPacking s p → s ≤ r
def SquareUpper (N : ℕ) (r : ℝ) : Prop :=
  ∀ s : ℝ, ∀ p : Fin N → Point, SquarePacking s p → s ≤ r

noncomputable def c15 : ℝ := Real.sqrt (1+2/Real.sqrt 5)
noncomputable def r15 : ℝ := 1/(1+Real.sqrt (1+(2+c15)^2))
noncomputable def rotate72 (p : Point) : Point :=
  let c := (Real.sqrt 5-1)/4
  let s := Real.sqrt (10+2*Real.sqrt 5)/4
  (c*p.1-s*p.2, s*p.1+c*p.2)
noncomputable def p15 (i : Fin 15) : Point :=
  let p : Point := if i.val % 3 = 0 then (1,-c15)
    else if i.val % 3 = 1 then (1,-c15-2) else (-1,-c15-2)
  let q := rotate72^[i.val / 3] p
  (r15*q.1, r15*q.2)
def P15 : Prop := DiskPacking r15 p15 ∧ DiskUpper 15 r15

noncomputable def r18 : ℝ := 1/(1+Real.sqrt 2+Real.sqrt 6)
noncomputable def raw18 (i : Fin 18) : Point :=
  let a := Real.sqrt 3
  ![(2,0),(-2,0),(1,a),(1,-a),(-1,a),(-1,-a),
    (1,2+a),(1,-(2+a)),(-1,2+a),(-1,-(2+a)),
    (2+a,1),(2+a,-1),(-(2+a),1),(-(2+a),-1),
    (1+a,1+a),(1+a,-(1+a)),(-(1+a),1+a),(-(1+a),-(1+a))] i
noncomputable def p18 (i : Fin 18) : Point :=
  (r18*(raw18 i).1, r18*(raw18 i).2)
def P18 : Prop := DiskPacking r18 p18 ∧ DiskUpper 18 r18

-- Fixed answers: no AlgebraicAnswer parameter or optimum-existence target.
noncomputable def r16 : ℝ := Frozen.goldbergRoot 0/(1+Frozen.goldbergRoot 0)
noncomputable def p16 (i : Fin 16) : Point :=
  ((Frozen.goldbergRaw i).1/(1+Frozen.goldbergRoot 0),
   (Frozen.goldbergRaw i).2/(1+Frozen.goldbergRoot 0))
def P16 : Prop := DiskUpper 16 r16
-- Preparation assertion, not an assumed axiom or the reward target.
def P16Construction : Prop :=
  (Frozen.goldbergBox Frozen.goldbergRoot ∧ Frozen.goldbergSystem Frozen.goldbergRoot) ∧
  DiskPacking r16 p16

noncomputable def r34 : ℝ := 1/(Frozen.side34.eval (Real.sqrt 2) (Real.sqrt 6))
noncomputable def p34 (i : Fin 34) : Point :=
  (r34*(Frozen.centers34 i).1.eval (Real.sqrt 2) (Real.sqrt 6),
   r34*(Frozen.centers34 i).2.eval (Real.sqrt 2) (Real.sqrt 6))
def P34 : Prop := SquareUpper 34 r34
noncomputable def r35 : ℝ := 1/(Frozen.side35.eval (Real.sqrt 2) (Real.sqrt 6))
noncomputable def p35 (i : Fin 35) : Point :=
  (r35*(Frozen.centers35 i).1.eval (Real.sqrt 2) (Real.sqrt 6),
   r35*(Frozen.centers35 i).2.eval (Real.sqrt 2) (Real.sqrt 6))
def P35 : Prop := SquareUpper 35 r35

end Candidate11
