import Mathlib

namespace Candidate11.Certificate

structure Quad where
  c0 : ℚ
  c1 : ℚ
  c2 : ℚ
  c3 : ℚ
  deriving DecidableEq

def Quad.eval (q : Quad) (a b : ℝ) : ℝ := q.c0 + q.c1*a + q.c2*b + q.c3*(a*b)
def Quad.add (q r : Quad) : Quad := ⟨q.c0+r.c0,q.c1+r.c1,q.c2+r.c2,q.c3+r.c3⟩
def Quad.neg (q : Quad) : Quad := ⟨-q.c0,-q.c1,-q.c2,-q.c3⟩
def Quad.sub (q r : Quad) : Quad := q.add r.neg
def Quad.ofRat (r : ℚ) : Quad := ⟨r,0,0,0⟩
def Quad.mul (q r : Quad) : Quad :=
  ⟨q.c0*r.c0+2*q.c1*r.c1+6*q.c2*r.c2+12*q.c3*r.c3,
   q.c0*r.c1+q.c1*r.c0+6*(q.c2*r.c3+q.c3*r.c2),
   q.c0*r.c2+q.c2*r.c0+2*(q.c1*r.c3+q.c3*r.c1),
   q.c0*r.c3+q.c3*r.c0+q.c1*r.c2+q.c2*r.c1⟩

@[simp] theorem eval_add (q r : Quad) (a b : ℝ) :
    (q.add r).eval a b = q.eval a b + r.eval a b := by
  simp [Quad.eval,Quad.add]; ring
@[simp] theorem eval_neg (q : Quad) (a b : ℝ) :
    q.neg.eval a b = -q.eval a b := by
  simp [Quad.eval,Quad.neg]; ring
@[simp] theorem eval_sub (q r : Quad) (a b : ℝ) :
    (q.sub r).eval a b = q.eval a b - r.eval a b := by
  simp [Quad.sub,sub_eq_add_neg]
@[simp] theorem eval_ofRat (q : ℚ) (a b : ℝ) :
    (Quad.ofRat q).eval a b = q := by simp [Quad.eval,Quad.ofRat]
@[simp] theorem eval_mul (q r : Quad) (a b : ℝ) (ha : a^2=2) (hb : b^2=6) :
    (q.mul r).eval a b = q.eval a b * r.eval a b := by
  simp only [Quad.eval,Quad.mul]; push_cast
  ring_nf
  simp only [ha,hb]
  ring

def termLower (q l u : ℚ) : ℚ := if 0 ≤ q then q*l else q*u
def Quad.lower (q : Quad) : ℚ := q.c0 +
  termLower q.c1 (1414213/1000000) (1414214/1000000) +
  termLower q.c2 (2449489/1000000) (2449490/1000000) +
  termLower q.c3 (3464101/1000000) (3464102/1000000)

theorem termLower_le (q l u : ℚ) (x : ℝ) (hl : (l:ℝ) ≤ x) (hu : x ≤ (u:ℝ)) :
    (termLower q l u : ℝ) ≤ (q:ℝ)*x := by
  unfold termLower
  split_ifs with h
  · push_cast
    exact mul_le_mul_of_nonneg_left hl (by exact_mod_cast h)
  · push_cast
    exact mul_le_mul_of_nonpos_left hu (by exact_mod_cast (le_of_lt (lt_of_not_ge h)))

theorem lower_le_eval (q : Quad) (a b : ℝ)
    (ha : a^2=2) (hb : b^2=6) (ha0 : 0≤a) (hb0 : 0≤b) :
    (q.lower : ℝ) ≤ q.eval a b := by
  have haL : (1414213/1000000:ℝ) ≤ a := by nlinarith
  have haU : a ≤ (1414214/1000000:ℝ) := by nlinarith
  have hbL : (2449489/1000000:ℝ) ≤ b := by nlinarith
  have hbU : b ≤ (2449490/1000000:ℝ) := by nlinarith
  have hab0 : 0≤a*b := mul_nonneg ha0 hb0
  have hab : (a*b)^2=12 := by rw [mul_pow,ha,hb]; norm_num
  have habL : (3464101/1000000:ℝ) ≤ a*b := by nlinarith
  have habU : a*b ≤ (3464102/1000000:ℝ) := by nlinarith
  have h1 := termLower_le q.c1 (1414213/1000000) (1414214/1000000) a (by norm_num at haL ⊢; exact haL) (by norm_num at haU ⊢; exact haU)
  have h2 := termLower_le q.c2 (2449489/1000000) (2449490/1000000) b (by norm_num at hbL ⊢; exact hbL) (by norm_num at hbU ⊢; exact hbU)
  have h3 := termLower_le q.c3 (3464101/1000000) (3464102/1000000) (a*b) (by norm_num at habL ⊢; exact habL) (by norm_num at habU ⊢; exact habU)
  simp only [Quad.lower,Quad.eval,Rat.cast_add]
  linarith

theorem eval_nonneg (q : Quad) (h : 0≤q.lower) (a b : ℝ)
    (ha : a^2=2) (hb : b^2=6) (ha0 : 0≤a) (hb0 : 0≤b) : 0≤q.eval a b := by
  have := lower_le_eval q a b ha hb ha0 hb0
  have h' : (0:ℝ)≤q.lower := by exact_mod_cast h
  linarith

end Candidate11.Certificate

namespace Candidate11.Certificate

def pairSlack (p q : Quad × Quad) : Quad :=
  (((p.1.sub q.1).mul (p.1.sub q.1)).add
    ((p.2.sub q.2).mul (p.2.sub q.2))).sub (Quad.ofRat 4)

def PackingCheck {N : ℕ} (L : Quad) (p : Fin N → Quad × Quad) : Prop :=
  (∀ i, 0≤((p i).1.sub (Quad.ofRat 1)).lower ∧
    0≤((L.sub (Quad.ofRat 1)).sub (p i).1).lower ∧
    0≤((p i).2.sub (Quad.ofRat 1)).lower ∧
    0≤((L.sub (Quad.ofRat 1)).sub (p i).2).lower) ∧
  ∀ i j, i≠j → 0≤(pairSlack (p i) (p j)).lower

instance {N : ℕ} (L : Quad) (p : Fin N → Quad × Quad) : Decidable (PackingCheck L p) := by
  unfold PackingCheck
  infer_instance

theorem checked_bounds {N : ℕ} (L : Quad) (p : Fin N → Quad × Quad)
    (h : PackingCheck L p) (a b : ℝ) (ha : a^2=2) (hb : b^2=6)
    (ha0 : 0≤a) (hb0 : 0≤b) (i : Fin N) :
    1≤(p i).1.eval a b ∧ (p i).1.eval a b≤L.eval a b-1 ∧
    1≤(p i).2.eval a b ∧ (p i).2.eval a b≤L.eval a b-1 := by
  rcases h.1 i with ⟨h1,h2,h3,h4⟩
  have e1 := eval_nonneg _ h1 a b ha hb ha0 hb0
  have e2 := eval_nonneg _ h2 a b ha hb ha0 hb0
  have e3 := eval_nonneg _ h3 a b ha hb ha0 hb0
  have e4 := eval_nonneg _ h4 a b ha hb ha0 hb0
  simp only [eval_sub,eval_ofRat,Rat.cast_one] at e1 e2 e3 e4
  exact ⟨by linarith,by linarith,by linarith,by linarith⟩

theorem checked_pair {N : ℕ} (L : Quad) (p : Fin N → Quad × Quad)
    (h : PackingCheck L p) (a b : ℝ) (ha : a^2=2) (hb : b^2=6)
    (ha0 : 0≤a) (hb0 : 0≤b) (i j : Fin N) (hij : i≠j) :
    4≤((p i).1.eval a b-(p j).1.eval a b)^2+
       ((p i).2.eval a b-(p j).2.eval a b)^2 := by
  have e := eval_nonneg _ (h.2 i j hij) a b ha hb ha0 hb0
  simp only [pairSlack,eval_sub,eval_add,eval_mul _ _ a b ha hb,eval_ofRat] at e
  norm_num at e
  nlinarith

end Candidate11.Certificate
