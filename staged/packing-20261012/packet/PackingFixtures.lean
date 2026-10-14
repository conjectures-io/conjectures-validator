import Candidates
open Candidate11 Candidate11.Certificate

-- The finite checker certifies the raw algebraic coordinates; this lemma
-- proves that scaling by the reciprocal side gives disks in the unit square.
theorem scaled_square {N : ℕ} (L : Quad) (q : Fin N → Quad × Quad)
    (hc : PackingCheck L q) (a b : ℝ)
    (ha : a^2=2) (hb : b^2=6) (ha0 : 0≤a) (hb0 : 0≤b)
    (hL : 0<L.eval a b) :
    SquarePacking (1/L.eval a b) (fun i =>
      ((1/L.eval a b)*(q i).1.eval a b,(1/L.eval a b)*(q i).2.eval a b)) := by
  let r := 1/L.eval a b
  have hr : 0<r := one_div_pos.mpr hL
  have hrL : r*L.eval a b=1 := by
    dsimp [r]
    exact one_div_mul_cancel (ne_of_gt hL)
  refine ⟨hr,?_,?_⟩
  · intro i
    obtain ⟨hx1,hx2,hy1,hy2⟩ := checked_bounds L q hc a b ha hb ha0 hb0 i
    have ex1 := mul_le_mul_of_nonneg_left hx1 (le_of_lt hr)
    have ex2 := mul_le_mul_of_nonneg_left hx2 (le_of_lt hr)
    have ey1 := mul_le_mul_of_nonneg_left hy1 (le_of_lt hr)
    have ey2 := mul_le_mul_of_nonneg_left hy2 (le_of_lt hr)
    change r≤r*(q i).1.eval a b ∧ r*(q i).1.eval a b≤1-r ∧
      r≤r*(q i).2.eval a b ∧ r*(q i).2.eval a b≤1-r
    exact ⟨by nlinarith,by nlinarith,by nlinarith,by nlinarith⟩
  · intro i j hij
    have hd := checked_pair L q hc a b ha hb ha0 hb0 i j hij
    change (2*r)^2≤(r*(q i).1.eval a b-r*(q j).1.eval a b)^2+
      (r*(q i).2.eval a b-r*(q j).2.eval a b)^2
    calc
      (2*r)^2=r^2*4 := by ring
      _≤r^2*(((q i).1.eval a b-(q j).1.eval a b)^2+
        ((q i).2.eval a b-(q j).2.eval a b)^2) :=
          mul_le_mul_of_nonneg_left hd (sq_nonneg r)
      _=(r*(q i).1.eval a b-r*(q j).1.eval a b)^2+
        (r*(q i).2.eval a b-r*(q j).2.eval a b)^2 := by ring

theorem r34_exact : r34=1/(4+2*Real.sqrt 2+2*Real.sqrt 6) := by
  norm_num [r34,Frozen.side34,Quad.eval]
theorem r35_exact : r35=1/(8+Real.sqrt 2+Real.sqrt 6) := by
  norm_num [r35,Frozen.side35,Quad.eval]

theorem packing34_valid : SquarePacking r34 p34 := by
  apply scaled_square Frozen.side34 Frozen.centers34 Frozen.packingCheck34
    (Real.sqrt 2) (Real.sqrt 6)
    (Real.sq_sqrt (by norm_num)) (Real.sq_sqrt (by norm_num))
    (Real.sqrt_nonneg _) (Real.sqrt_nonneg _)
  norm_num [Frozen.side34,Quad.eval]
  positivity

theorem packing35_valid : SquarePacking r35 p35 := by
  apply scaled_square Frozen.side35 Frozen.centers35 Frozen.packingCheck35
    (Real.sqrt 2) (Real.sqrt 6)
    (Real.sq_sqrt (by norm_num)) (Real.sq_sqrt (by norm_num))
    (Real.sqrt_nonneg _) (Real.sqrt_nonneg _)
  norm_num [Frozen.side35,Quad.eval]
  positivity

-- These are fixed propositions; no submitted AlgebraicAnswer is accepted.
example : Prop := P16
example : Prop := P34
example : Prop := P35

-- Genuine negative controls for the same rational checker.
def coincident34 : Fin 34 → Quad × Quad := fun _ => Frozen.centers34 0
set_option maxRecDepth 100000 in
set_option maxHeartbeats 0 in
theorem coincident34_rejected : ¬PackingCheck Frozen.side34 coincident34 := by decide +kernel

def smallerSide35 : Quad := Frozen.side35.sub (Quad.ofRat (1/1000000))
set_option maxRecDepth 100000 in
set_option maxHeartbeats 0 in
theorem smaller_container_rejected : ¬PackingCheck smallerSide35 Frozen.centers35 := by decide +kernel

#print axioms packing34_valid
#print axioms packing35_valid
#print axioms coincident34_rejected
#print axioms smaller_container_rejected
#print axioms P16
#print axioms P34
#print axioms P35
