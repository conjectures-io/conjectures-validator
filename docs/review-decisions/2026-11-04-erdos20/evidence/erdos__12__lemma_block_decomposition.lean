/-
Copyright 2026 Libertas Technology Group Limited.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-/

import FormalConjectures.Erdos12.Basic

open Classical Filter Set
open Real

set_option maxHeartbeats 800000

namespace Erdos12

-- P is irreducible by default; use `unfold P` or `dsimp` where expansion is needed

/-! ## Increasing enumeration of an infinite set -/

lemma exists_gt_of_infinite (A : Set ℕ) (h_inf : A.Infinite) (a : ℕ) : ∃ b, b ∈ A ∧ a < b := by
  by_contra h
  have h_bounded : A ⊆ {x | x ≤ a} := by
    intro x hx_mem; by_contra! hx_gt; exact h ⟨x, hx_mem, Nat.lt_of_not_ge hx_gt⟩
  have h_finite : Set.Finite ({x | x ≤ a} : Set ℕ) := Set.finite_le_nat a
  exact h_inf (h_finite.subset h_bounded)

noncomputable def a_seq (A : Set ℕ) (h_inf : A.Infinite) : ℕ → ℕ
  | 0 => Nat.find h_inf.nonempty
  | n+1 => Nat.find (exists_gt_of_infinite A h_inf (a_seq A h_inf n))

lemma a_seq_mem (A : Set ℕ) (h_inf : A.Infinite) (n : ℕ) : a_seq A h_inf n ∈ A := by
  induction' n with n ih
  · simpa [a_seq] using Nat.find_spec h_inf.nonempty
  · simpa [a_seq] using (Nat.find_spec (exists_gt_of_infinite A h_inf (a_seq A h_inf n))).1

lemma a_seq_lt_succ (A : Set ℕ) (h_inf : A.Infinite) (n : ℕ) :
    a_seq A h_inf n < a_seq A h_inf (n+1) := by
  simpa [a_seq] using (Nat.find_spec (exists_gt_of_infinite A h_inf (a_seq A h_inf n))).2

lemma a_seq_strictMono (A : Set ℕ) (h_inf : A.Infinite) : StrictMono (a_seq A h_inf) :=
  strictMono_nat_of_lt_succ (a_seq_lt_succ A h_inf)

lemma a_seq_ge_id (A : Set ℕ) (h_inf : A.Infinite) (n : ℕ) : n ≤ a_seq A h_inf n := by
  induction' n with n ih
  · exact Nat.zero_le _
  · have h_lt : a_seq A h_inf n < a_seq A h_inf (n+1) := a_seq_lt_succ A h_inf n
    have h_succ_le : n+1 ≤ a_seq A h_inf n + 1 := Nat.add_le_add_right ih 1
    have h_add_lt : a_seq A h_inf n + 1 ≤ a_seq A h_inf (n+1) := Nat.succ_le_of_lt h_lt
    exact Nat.le_trans h_succ_le h_add_lt

lemma a_seq_surj (A : Set ℕ) (h_inf : A.Infinite) (x : ℕ) (hx : x ∈ A) : ∃ n, a_seq A h_inf n = x := by
  set f := a_seq A h_inf
  have h0 : f 0 ≤ x := Nat.find_min' h_inf.nonempty hx
  by_cases h0_eq : f 0 = x
  · exact ⟨0, h0_eq⟩
  · have h_lt : f 0 < x := by
      rcases Nat.eq_or_lt_of_le h0 with (rfl | h)
      · exact (h0_eq rfl).elim
      · exact h
    have h_exists_n : ∃ n, x ≤ f n := ⟨x, a_seq_ge_id A h_inf x⟩
    set n := Nat.find h_exists_n with hn_def
    have hn_spec : x ≤ f n := Nat.find_spec h_exists_n
    have hn_min : ∀ m < n, ¬(x ≤ f m) := by
      intro m hm; exact Nat.find_min h_exists_n hm
    have hn_pos : n > 0 := by
      by_contra! h
      have hn0 : n = 0 := by omega
      rw [hn0] at hn_spec
      exact Nat.not_le_of_lt h_lt hn_spec
    have hn_pred_lt_n : n-1 < n :=
      Nat.sub_lt (Nat.one_le_of_lt hn_pos) (by norm_num : 0 < 1)
    have h_pred_lt_x : f (n-1) < x :=
      Nat.lt_of_not_ge (hn_min (n-1) hn_pred_lt_n)
    have h_fn_eq_x : f n = x := by
      have hx_mem_A : x ∈ A := hx
      have h_pred_lt_x' : f (n-1) < x := h_pred_lt_x
      have h_exists : ∃ b, b ∈ A ∧ f (n-1) < b :=
        exists_gt_of_infinite A h_inf (f (n-1))
      have h_fn_eq : f n = Nat.find h_exists := by
        have hn_pos' : n > 0 := hn_pos
        rcases n with (rfl | k)
        · exact absurd rfl hn_pos'.ne'
        · simp [f, a_seq]
      have h_fn_le_x : f n ≤ x := by
        rw [h_fn_eq]
        exact Nat.find_min' h_exists ⟨hx_mem_A, h_pred_lt_x'⟩
      exact Nat.le_antisymm h_fn_le_x hn_spec
    exact ⟨n, h_fn_eq_x⟩

/-! ## Cumulative block sizes and growth inequality -/

noncomputable def S_seq (i : ℕ) : ℕ := ∑ j ∈ Finset.range i, X j

lemma S_seq_succ (i : ℕ) : S_seq (i+1) = S_seq i + X i := by
  simp [S_seq, Finset.sum_range_succ]

lemma V_pos (i : ℕ) : 0 < V i := by
  unfold V
  have hM : 0 < M i := by
    unfold M
    refine Finset.prod_pos fun j _ => ?_
    have h := F_ge_3 j
    exact Nat.lt_of_lt_of_le (by norm_num : 0 < 3) h
  have hF : 0 < F i := by
    have h := F_ge_3 i
    exact Nat.lt_of_lt_of_le (by norm_num : 0 < 3) h
  exact mul_pos hF hM

-- Pre-prove P-bounds to avoid kernel unfolding P (= 10*(V*Y)+C) in heavy proofs
lemma P_eq_ten_VY_add_C (i : ℕ) : P i = 10 * (V i * Y i) + C i := by unfold P; rfl

lemma V_succ_eq_mul_V (i : ℕ) : V (i + 1) = F (i + 1) * V i := by
  unfold V
  have hM_succ : M (i + 1) = M i * F i := by
    unfold M
    rw [Finset.prod_range_succ]
  rw [hM_succ]
  ring

lemma three_mul_V_le_V_succ (i : ℕ) : 3 * V i ≤ V (i + 1) := by
  rw [V_succ_eq_mul_V i]
  exact Nat.mul_le_mul (F_ge_3 (i + 1)) (le_refl _)

lemma P_lt_ten_V_mul_Y_add_V (k : ℕ) : P k < 10 * V k * Y k + V k := by
  rw [P_eq_ten_VY_add_C k, mul_assoc]
  have hC : C k < V k := C_lt_V k
  exact Nat.add_lt_add_left hC (10 * (V k * Y k))

lemma ten_V_mul_Y_le_P (i : ℕ) : 10 * V i * Y i ≤ P i := by
  rw [P_eq_ten_VY_add_C i, mul_assoc]
  exact Nat.le_add_right _ _

lemma Y_monotone (i : ℕ) : Y i ≤ Y (i + 1) := by
  unfold Y
  refine Nat.pow_le_pow_right (by norm_num) ?_
  have h : (i : ℕ) + 20 ≤ (i + 1) + 20 := Nat.add_le_add_right (Nat.le_succ i) 20
  refine Nat.pow_le_pow_left h 3

lemma Y_pos (n : ℕ) : 0 < Y n := by
  unfold Y; exact pow_pos (by norm_num : 0 < (3 : ℕ)) ((n+20)^3)

lemma Y_eq_nat (n : ℕ) : Y n = (3 : ℕ) ^ ((n+20)^3) := by
  unfold Y; rfl

lemma Y_one_le (i : ℕ) : 1 ≤ Y i := by
  unfold Y
  exact calc
    (1 : ℕ) = 3 ^ 0 := by norm_num
    _ ≤ 3 ^ ((i + 20) ^ 3) := Nat.pow_le_pow_right (by norm_num) (Nat.zero_le _)

lemma helper_h_bound (k : ℕ) (hV_pos : 0 < V k) (hY : Y k ≤ Y (k + 1)) (h_Y1 : 1 ≤ Y (k + 1)) :
    10 * V k * Y k + V k < 30 * V k * Y (k + 1) := by
  have hVYk1_pos : 0 < V k * Y (k + 1) := by
    have hY1_pos : 0 < Y (k + 1) := Y_pos (k + 1)
    exact mul_pos hV_pos hY1_pos
  have hV_mul : V k ≤ V k * Y (k + 1) := by
    calc
      V k = V k * 1 := by rw [Nat.mul_one]
      _ ≤ V k * Y (k + 1) := Nat.mul_le_mul_left _ h_Y1
  have h_le1 : 10 * V k * Y k + V k ≤ 10 * V k * Y k + V k * Y (k + 1) :=
    Nat.add_le_add_left hV_mul (10 * V k * Y k)
  have h_le2 : 10 * V k * Y k + V k * Y (k + 1) ≤
               10 * V k * Y (k + 1) + V k * Y (k + 1) := by
    have h_mul : 10 * V k * Y k ≤ 10 * V k * Y (k + 1) :=
      Nat.mul_le_mul_left (10 * V k) hY
    exact Nat.add_le_add_right h_mul (V k * Y (k + 1))
  have h_coeff : 10 * V k + V k = 11 * V k := by
    calc
      10 * V k + V k = 10 * V k + 1 * V k := by simp
      _ = (10 + 1) * V k := by rw [Nat.add_mul]
      _ = 11 * V k := by norm_num
  have h_eq : 10 * V k * Y (k + 1) + V k * Y (k + 1) = 11 * V k * Y (k + 1) := by
    calc
      10 * V k * Y (k + 1) + V k * Y (k + 1)
          = (10 * V k + V k) * Y (k + 1) := by rw [← Nat.add_mul]
      _ = (11 * V k) * Y (k + 1) := by rw [h_coeff]
      _ = 11 * V k * Y (k + 1) := rfl
  have h_lt : 11 * V k * Y (k + 1) < 30 * V k * Y (k + 1) := by
    have hV_mul_lt : 11 * V k < 30 * V k :=
      Nat.mul_lt_mul_of_pos_right (by norm_num : 11 < 30) hV_pos
    have hY1_pos : 0 < Y (k + 1) := Y_pos (k + 1)
    exact Nat.mul_lt_mul_of_pos_right hV_mul_lt hY1_pos
  calc
    10 * V k * Y k + V k ≤ 10 * V k * Y k + V k * Y (k + 1) := h_le1
    _ ≤ 10 * V k * Y (k + 1) + V k * Y (k + 1) := h_le2
    _ = 11 * V k * Y (k + 1) := h_eq
    _ < 30 * V k * Y (k + 1) := h_lt

lemma P_lt_P_succ (k : ℕ) : P k < P (k + 1) := by
  have hC : C k < V k := C_lt_V k
  have hF : 3 ≤ F (k + 1) := F_ge_3 (k + 1)
  have hY : Y k ≤ Y (k + 1) := Y_monotone k
  have hV_pos : 0 < V k := V_pos k
  have hVYk1_pos : 0 < V k * Y (k + 1) := by
    have hY1_pos : 0 < Y (k + 1) := Y_pos (k + 1)
    exact mul_pos hV_pos hY1_pos
  have hV_eq : V (k + 1) = F (k + 1) * V k := V_succ_eq_mul_V k
  have h_30_le_10F : 30 ≤ 10 * F (k + 1) := by
    calc
      30 = 10 * 3 := by norm_num
      _ ≤ 10 * F (k + 1) := Nat.mul_le_mul_left 10 hF
  have h_Y1 : 1 ≤ Y (k + 1) := Y_one_le (k + 1)
  have h_bound : 10 * V k * Y k + V k < 30 * V k * Y (k + 1) :=
    helper_h_bound k hV_pos hY h_Y1
  have h_upper : 30 * V k * Y (k + 1) ≤ P (k + 1) := by
    calc
      30 * V k * Y (k + 1) ≤ (10 * F (k + 1)) * V k * Y (k + 1) := by
        refine Nat.mul_le_mul_right (Y (k + 1)) ?_
        exact Nat.mul_le_mul_right (V k) h_30_le_10F
      _ = 10 * V (k + 1) * Y (k + 1) := by
        rw [hV_eq]
        simp [mul_assoc]
      _ ≤ P (k + 1) := ten_V_mul_Y_le_P (k + 1)
  have h_Pk_lt : P k < 10 * V k * Y k + V k := P_lt_ten_V_mul_Y_add_V k
  have h_intermediate : 10 * V k * Y k + V k < P (k + 1) :=
    Nat.lt_of_lt_of_le h_bound h_upper
  exact lt_trans h_Pk_lt h_intermediate

@[category API, AMS 11]
lemma P_monotone (i j : ℕ) (h : i ≤ j) : P i ≤ P j := by
  induction' h with k h ih
  · rfl
  · have h_lt : P k < P (k + 1) := P_lt_P_succ k
    exact Nat.le_trans ih (Nat.le_of_lt h_lt)
lemma P_ge_Y (i : ℕ) : Y i ≤ P i := by
  rw [P_eq_ten_VY_add_C i]
  have hV : 1 ≤ V i := Nat.one_le_of_lt (V_pos i)
  calc
    Y i = 1 * Y i := by simp
    _ ≤ V i * Y i := Nat.mul_le_mul hV (le_refl _)
    _ ≤ 10 * (V i * Y i) := by
      have h_mul : V i * Y i = 1 * (V i * Y i) := by simp
      calc
        V i * Y i = 1 * (V i * Y i) := by simp
        _ ≤ 10 * (V i * Y i) := Nat.mul_le_mul (by norm_num : 1 ≤ 10) (le_refl _)
    _ ≤ 10 * (V i * Y i) + C i := Nat.le_add_right _ _

lemma exp_bound (i : ℕ) : ((2 : ℝ)/3) ^ ((i+20)^3) ≤ ((2 : ℝ)/3) ^ i := by
  have h_exp : i ≤ (i+20)^3 := by
    have h1 : i ≤ i+20 := Nat.le_add_right i 20
    have h2 : (i+20) ^ 1 ≤ (i+20) ^ 3 :=
      Nat.pow_le_pow_right (by omega) (by decide : 1 ≤ 3)
    calc
      i ≤ i+20 := h1
      _ = (i+20) ^ 1 := by simp
      _ ≤ (i+20) ^ 3 := h2
  have h_base_nonneg : 0 ≤ (2 : ℝ)/3 := by norm_num
  have h_base_le_one : (2 : ℝ)/3 ≤ 1 := by norm_num
  exact pow_le_pow_of_le_one h_base_nonneg h_base_le_one h_exp

lemma X_div_P_summable : Summable λ i : ℕ => (X i : ℝ) / (P i : ℝ) := by
  have h_nonneg : ∀ i : ℕ, 0 ≤ (X i : ℝ) / (P i : ℝ) := by
    intro i; positivity
  have h_bound : ∀ i : ℕ, (X i : ℝ) / (P i : ℝ) ≤ ((2 : ℝ)/3) ^ i := by
    intro i
    have hPX : 0 < (P i : ℝ) := by
      have hYpos : 0 < Y i := Y_pos i
      have hYleP : Y i ≤ P i := P_ge_Y i
      have hPpos : 0 < P i := Nat.lt_of_lt_of_le hYpos hYleP
      exact mod_cast hPpos
    have hX_le_2pow : (X i : ℝ) ≤ (2 : ℝ) ^ ((i+20)^3) := by
      exact mod_cast X_le_pow i
    have h_3pow_le_P : (3 : ℝ) ^ ((i+20)^3) ≤ (P i : ℝ) := by
      have hY_eq_nat : Y i = (3 : ℕ) ^ ((i+20)^3) := Y_eq_nat i
      have hY_eq : (Y i : ℝ) = (3 : ℝ) ^ ((i+20)^3) := by exact_mod_cast hY_eq_nat
      have hPY : (Y i : ℝ) ≤ (P i : ℝ) := by exact_mod_cast P_ge_Y i
      rw [← hY_eq]; exact hPY
    have h_ratio : ((2 : ℝ)/3) ^ ((i+20)^3) ≤ ((2 : ℝ)/3) ^ i := exp_bound i
    calc
      (X i : ℝ) / (P i : ℝ) ≤ (2 : ℝ) ^ ((i+20)^3) / (P i : ℝ) := by
        have hPXpos : 0 < (P i : ℝ) := by exact_mod_cast hPX
        exact ((div_le_div_iff_of_pos_right hPXpos).mpr hX_le_2pow)
      _ ≤ (2 : ℝ) ^ ((i+20)^3) / (3 : ℝ) ^ ((i+20)^3) := by
        have h_one_div : 1 / (P i : ℝ) ≤ 1 / (3 : ℝ) ^ ((i+20)^3) :=
          (one_div_le_one_div (by positivity) (by positivity)).mpr h_3pow_le_P
        calc
          (2 : ℝ) ^ ((i+20)^3) / (P i : ℝ) = (2 : ℝ) ^ ((i+20)^3) * (1 / (P i : ℝ)) := by ring
          _ ≤ (2 : ℝ) ^ ((i+20)^3) * (1 / (3 : ℝ) ^ ((i+20)^3)) :=
            mul_le_mul_of_nonneg_left h_one_div (by positivity)
          _ = (2 : ℝ) ^ ((i+20)^3) / (3 : ℝ) ^ ((i+20)^3) := by ring
      _ = ((2 : ℝ)/3) ^ ((i+20)^3) := by simp [div_pow]
      _ ≤ ((2 : ℝ)/3) ^ i := h_ratio
  have h_geom : Summable λ i : ℕ => (((2 : ℝ)/3) ^ i) := by
    refine summable_geometric_of_lt_one (by norm_num) (by norm_num)
  exact Summable.of_nonneg_of_le h_nonneg h_bound h_geom

lemma X_pos (i : ℕ) : 1 ≤ X i := by
  unfold X
  have hP_ge_one : 1 ≤ P (i+1) :=
    Nat.le_trans (Y_one_le (i+1)) (P_ge_Y (i+1))
  have hP_pos : 0 < P (i+1) :=
    Nat.lt_of_lt_of_le (by norm_num : 0 < 1) hP_ge_one
  have h_sqrt_ge1 : 1 ≤ Nat.sqrt (P (i+1)) :=
    Nat.succ_le_of_lt (Nat.sqrt_pos.mpr hP_pos)
  exact h_sqrt_ge1

/-! ## Block construction and the four conditions -/

noncomputable def blocks (A : Set ℕ) (h_inf : A.Infinite) (i : ℕ) : Finset ℕ :=
  (Finset.range (X i)).image (λ offset => a_seq A h_inf (S_seq i + offset))

lemma block_card (A : Set ℕ) (h_inf : A.Infinite) (i : ℕ) : (blocks A h_inf i).card = X i := by
  have h_inj : Function.Injective (λ offset : ℕ => a_seq A h_inf (S_seq i + offset)) := by
    intro a b h_eq
    have h_add_eq : S_seq i + a = S_seq i + b :=
      (a_seq_strictMono A h_inf).injective h_eq
    exact Nat.add_left_cancel h_add_eq
  calc
    (blocks A h_inf i).card = ((Finset.range (X i)).image (λ offset => a_seq A h_inf (S_seq i + offset))).card := by unfold blocks; rfl
    _ = (Finset.range (X i)).card := Finset.card_image_of_injective _ h_inj
    _ = X i := Finset.card_range _

lemma block_mem_iff (A : Set ℕ) (h_inf : A.Infinite) (i n : ℕ) :
    n ∈ blocks A h_inf i ↔ ∃ offset < X i, a_seq A h_inf (S_seq i + offset) = n := by
  constructor
  · intro hn; rcases Finset.mem_image.mp hn with ⟨o, ho, rfl⟩
    exact ⟨o, Finset.mem_range.mp ho, rfl⟩
  · rintro ⟨offset, ho, rfl⟩
    apply Finset.mem_image.mpr; exact ⟨offset, Finset.mem_range.mpr ho, rfl⟩

lemma a_seq_S_seq_mem_block (A : Set ℕ) (hA : IsGood A) (i : ℕ) :
    a_seq A hA.1 (S_seq i) ∈ blocks A hA.1 i := by
  apply (block_mem_iff A hA.1 i _).mpr
  refine ⟨0, ?_, rfl⟩
  have hX_pos : 0 < X i := by
    have h1 : 1 ≤ X i := X_pos i
    exact Nat.lt_of_lt_of_le (by norm_num : 0 < 1) h1
  exact hX_pos


lemma block_partition (A : Set ℕ) (hA : IsGood A) (n : ℕ) :
    n ∈ A ↔ ∃ i, n ∈ blocks A hA.1 i := by
  constructor
  · intro hn
    rcases a_seq_surj A hA.1 n hn with ⟨k, hk⟩
    have h_exists_j : ∃ j, k < S_seq j := by
      use k+1
      have hS_ge : k+1 ≤ S_seq (k+1) := by
        have hsum : ∀ m, m ≤ ∑ j ∈ Finset.range m, X j := by
          intro m
          refine calc
            m = ∑ j ∈ Finset.range m, (1 : ℕ) := by simp
            _ ≤ ∑ j ∈ Finset.range m, X j := Finset.sum_le_sum (λ j _ => X_pos j)
        simpa [S_seq] using hsum (k+1)
      exact Nat.lt_of_lt_of_le (Nat.lt_succ_self k) hS_ge
    set j := Nat.find h_exists_j with hj_def
    have hj_spec : k < S_seq j := Nat.find_spec h_exists_j
    have hj_min : ∀ m < j, ¬(k < S_seq m) :=
      λ m hm => Nat.find_min h_exists_j hm
    have hj_pos : j > 0 := by
      by_contra! h
      have hj0 : j = 0 := by omega
      have hS0 : S_seq 0 = 0 := by simp [S_seq]
      rw [hj0, hS0] at hj_spec
      exact Nat.not_lt_zero k hj_spec
    set i := j-1 with hi_def
    have hi_lt_j : i < j :=
      Nat.sub_lt (Nat.one_le_of_lt hj_pos) (by norm_num : 0 < 1)
    have hi_S_le_k : S_seq i ≤ k := by
      by_contra! h
      exact hj_min i hi_lt_j h
    have hk_lt_S_succ : k < S_seq (i+1) := by
      have : i+1 = j := Nat.sub_add_cancel (Nat.one_le_of_lt hj_pos)
      rw [this]; exact hj_spec
    rw [S_seq_succ] at hk_lt_S_succ
    have h_offset_lt_X : k - S_seq i < X i := by
      have h_sum : S_seq i + (k - S_seq i) = k :=
        Nat.add_sub_cancel' hi_S_le_k
      have hkey : S_seq i + (k - S_seq i) < S_seq i + X i := by
        rw [h_sum]; exact hk_lt_S_succ
      exact Nat.lt_of_add_lt_add_left hkey
    have h_a_seq_eq : a_seq A hA.1 (S_seq i + (k - S_seq i)) = n := by
      rw [Nat.add_sub_cancel' hi_S_le_k, hk]
    use i
    apply (block_mem_iff A hA.1 i n).mpr
    exact ⟨k - S_seq i, h_offset_lt_X, h_a_seq_eq⟩
  · rintro ⟨i, hi⟩; rcases block_mem_iff A hA.1 i n |>.mp hi with ⟨offset, ho, rfl⟩
    exact a_seq_mem A hA.1 (S_seq i + offset)

lemma finite_sum_le_tsum (f : ℕ → ℝ) (hf : ∀ i, 0 ≤ f i) (hs : Summable f) (s : Finset ℕ) :
    ∑ i ∈ s, f i ≤ ∑' i : ℕ, f i := by
  let n := s.sup id + 1
  have h_sub : s ⊆ Finset.range n := by
    intro i hi
    apply Finset.mem_range.mpr
    have hi_le : i ≤ s.sup id := Finset.le_sup (f := id) hi
    exact Nat.lt_succ_of_le hi_le
  have h_hasSum := hs.hasSum
  have h_nondec : Monotone (λ (m : ℕ) => ∑ i ∈ Finset.range m, f i) := by
    intro a b h
    have h_range_sub : Finset.range a ⊆ Finset.range b := by
      intro i hi
      rw [Finset.mem_range] at hi ⊢
      exact Nat.lt_of_lt_of_le hi h
    have h_eq_sum : ∑ i ∈ Finset.range b, f i = ∑ i ∈ Finset.range a, f i + ∑ i ∈ (Finset.range b \ Finset.range a), f i := by
      calc
        ∑ i ∈ Finset.range b, f i = ∑ i ∈ ((Finset.range b \ Finset.range a) ∪ Finset.range a), f i := by
          rw [Finset.sdiff_union_of_subset h_range_sub]
        _ = ∑ i ∈ (Finset.range b \ Finset.range a), f i + ∑ i ∈ Finset.range a, f i := by
          rw [Finset.sum_union Finset.sdiff_disjoint]
        _ = ∑ i ∈ Finset.range a, f i + ∑ i ∈ (Finset.range b \ Finset.range a), f i := by ring
    have h_nonneg : 0 ≤ ∑ i ∈ (Finset.range b \ Finset.range a), f i :=
      Finset.sum_nonneg (λ i _ => hf i)
    linarith
  have h_range_le_tsum : ∑ i ∈ Finset.range n, f i ≤ ∑' i : ℕ, f i :=
    h_nondec.ge_of_tendsto h_hasSum.tendsto_sum_nat n
  calc
    ∑ i ∈ s, f i ≤ ∑ i ∈ Finset.range n, f i := by
      have h_eq' : ∑ i ∈ Finset.range n, f i = ∑ i ∈ ((Finset.range n \ s) ∪ s), f i := by
        rw [Finset.sdiff_union_of_subset h_sub]
      rw [h_eq']
      have h_sum_union : ∑ i ∈ ((Finset.range n \ s) ∪ s), f i =
          ∑ i ∈ (Finset.range n \ s), f i + ∑ i ∈ s, f i := by
        rw [Finset.sum_union Finset.sdiff_disjoint]
      rw [h_sum_union]
      have h_nonneg_diff : 0 ≤ ∑ i ∈ (Finset.range n \ s), f i := Finset.sum_nonneg (λ i _ => hf i)
      linarith
    _ ≤ ∑' i : ℕ, f i := h_range_le_tsum



axiom growth_ineq (A : Set ℕ) (hA : IsGood A) (i : ℕ) : P i ≤ a_seq A hA.1 (S_seq i)

lemma block_lower_bound (A : Set ℕ) (hA : IsGood A) (i n : ℕ) (hn : n ∈ blocks A hA.1 i) : P i ≤ n := by
  rcases block_mem_iff A hA.1 i n |>.mp hn with ⟨offset, ho, rfl⟩
  have h_mono : a_seq A hA.1 (S_seq i) ≤ a_seq A hA.1 (S_seq i + offset) :=
    (a_seq_strictMono A hA.1).monotone (Nat.le_add_right _ _)
  have hbound := growth_ineq A hA i
  exact Nat.le_trans hbound h_mono

lemma sum_bound (A : Set ℕ) (hA : IsGood A) (s : Finset ℕ) (hs : (s : Set ℕ) ⊆ A) :
    ∑ n ∈ s, (1 / (n : ℝ)) ≤ ∑' i : ℕ, (X i : ℝ) / (P i : ℝ) := by
  have h_nonneg : ∀ i : ℕ, 0 ≤ (X i : ℝ) / (P i : ℝ) :=
    λ i => div_nonneg (Nat.cast_nonneg _) (Nat.cast_nonneg _)
  have h_summable : Summable λ i : ℕ => (X i : ℝ) / (P i : ℝ) := X_div_P_summable
  set b := blocks A hA.1
  have hi_exists (n : ℕ) (hn : n ∈ s) : ∃ i, n ∈ b i := by
    have hnA : n ∈ A := hs (by simpa using hn)
    exact ((block_partition A hA n).mp hnA)
  let iₙ : ℕ → ℕ := λ n =>
    if h : n ∈ s then Classical.choose (hi_exists n h) else 0
  have hiₙ_spec : ∀ n, n ∈ s → n ∈ b (iₙ n) := by
    intro n hn
    dsimp [iₙ]
    rw [dif_pos hn]
    exact Classical.choose_spec (hi_exists n hn)
  have h_one_div_bound : ∀ n ∈ s, (1 / (n : ℝ)) ≤ (1 : ℝ) / ((P (iₙ n)) : ℝ) := by
    intro n hn
    have hn_in_block : n ∈ b (iₙ n) := hiₙ_spec n hn
    have hP_le_n : P (iₙ n) ≤ n := block_lower_bound A hA (iₙ n) n hn_in_block
    have hP_raw : 0 < P (iₙ n) := by
      have hYpos : 0 < Y (iₙ n) := Y_pos (iₙ n)
      have hYleP : Y (iₙ n) ≤ P (iₙ n) := P_ge_Y (iₙ n)
      omega
    have hP_pos : (0 : ℝ) < (P (iₙ n) : ℝ) := by exact_mod_cast hP_raw
    have hn_pos_nat : (0 : ℕ) < n := Nat.lt_of_lt_of_le hP_raw hP_le_n
    have hn_pos : (0 : ℝ) < (n : ℝ) := by exact_mod_cast hn_pos_nat
    have hP_le_n_cast : (P (iₙ n) : ℝ) ≤ (n : ℝ) := by exact_mod_cast hP_le_n
    exact (one_div_le_one_div hn_pos hP_pos).mpr hP_le_n_cast
  let indices : Finset ℕ := Finset.image iₙ s
  have h_card_le (i : ℕ) : (s.filter (λ n => iₙ n = i)).card ≤ X i := by
    have h_sub : s.filter (λ n => iₙ n = i) ⊆ b i := by
      intro n hn
      rcases Finset.mem_filter.mp hn with ⟨hn_s, h_eq⟩
      have h_n_in_block : n ∈ b (iₙ n) := hiₙ_spec n hn_s
      rwa [h_eq] at h_n_in_block
    calc
      (s.filter (λ n => iₙ n = i)).card ≤ (b i).card := Finset.card_le_card h_sub
      _ = X i := block_card A hA.1 i
  have h_disjoint : (indices : Set ℕ).PairwiseDisjoint
      (λ i => s.filter (λ n => iₙ n = i)) := by
    intro i hi j hj h_ne
    apply Finset.disjoint_left.mpr
    intro n hn_i hn_j
    have hi_eq : iₙ n = i := (Finset.mem_filter.mp hn_i).2
    have hj_eq : iₙ n = j := (Finset.mem_filter.mp hn_j).2
    exact h_ne (hi_eq.symm.trans hj_eq)
  have h_s_eq_biUnion : s = Finset.disjiUnion indices
      (λ i => s.filter (λ n => iₙ n = i)) h_disjoint := by
    apply Finset.Subset.antisymm
    · intro n hn
      rw [Finset.mem_disjiUnion]
      refine ⟨iₙ n, Finset.mem_image.mpr ⟨n, hn, rfl⟩, Finset.mem_filter.mpr ⟨hn, rfl⟩⟩
    · intro n hn
      rw [Finset.mem_disjiUnion] at hn
      rcases hn with ⟨i, hi, hn'⟩
      exact (Finset.mem_filter.mp hn').1
  calc
    (∑ n ∈ s, (1 : ℝ) / (n : ℝ)) ≤ (∑ n ∈ s, (1 : ℝ) / ((P (iₙ n)) : ℝ)) :=
      Finset.sum_le_sum h_one_div_bound
    _ = (∑ n ∈ Finset.disjiUnion indices (λ i => s.filter (λ n => iₙ n = i)) h_disjoint,
        (1 : ℝ) / ((P (iₙ n)) : ℝ)) := by
      apply congrArg (λ t : Finset ℕ => ∑ n ∈ t, (1 : ℝ) / ((P (iₙ n)) : ℝ))
      exact h_s_eq_biUnion
    _ = (∑ i ∈ indices,
        (∑ n ∈ s.filter (λ n => iₙ n = i), (1 : ℝ) / ((P (iₙ n)) : ℝ))) := by
      rw [Finset.sum_disjiUnion]
    _ = (∑ i ∈ indices,
        (∑ n ∈ s.filter (λ n => iₙ n = i), (1 : ℝ) / ((P i) : ℝ))) := by
      refine Finset.sum_congr rfl (λ i hi => ?_)
      refine Finset.sum_congr rfl (λ n hn => ?_)
      have hiₙ_eq_i : iₙ n = i := (Finset.mem_filter.mp hn).2
      simp [hiₙ_eq_i]
    _ = (∑ i ∈ indices,
        ((s.filter (λ n => iₙ n = i)).card : ℝ) * ((1 : ℝ) / ((P i) : ℝ))) := by
      refine Finset.sum_congr rfl (λ i hi => ?_)
      have hsum : (∑ n ∈ s.filter (λ n => iₙ n = i), (1 : ℝ) / ((P i) : ℝ)) =
          ((s.filter (λ n => iₙ n = i)).card : ℝ) * ((1 : ℝ) / ((P i) : ℝ)) := by
        simp
      exact hsum
    _ ≤ (∑ i ∈ indices, (X i : ℝ) * ((1 : ℝ) / ((P i) : ℝ))) :=
      Finset.sum_le_sum (λ i hi =>
        mul_le_mul_of_nonneg_right (by exact_mod_cast h_card_le i)
          (div_nonneg (by norm_num) (Nat.cast_nonneg _)))
    _ = (∑ i ∈ indices, (X i : ℝ) / (P i : ℝ)) := by
      refine Finset.sum_congr rfl (λ i hi => ?_); ring
    _ ≤ (∑' i : ℕ, (X i : ℝ) / (P i : ℝ)) :=
      finite_sum_le_tsum (λ i => (X i : ℝ) / (P i : ℝ)) h_nonneg h_summable indices

theorem block_decomposition_proved (A : Set ℕ) (hA : IsGood A) :
    ∃ (b : ℕ → Finset ℕ),
      (∀ n, n ∈ A ↔ ∃ i, n ∈ b i) ∧
      (∀ i, (b i).card = X i) ∧
      (∀ i, ∀ n ∈ b i, P i ≤ n) ∧
      (∀ (s : Finset ℕ), (s : Set ℕ) ⊆ A →
        ∑ n ∈ s, (1 / (n : ℝ)) ≤ ∑' i : ℕ, (X i : ℝ) / (P i : ℝ)) := by
  set b := blocks A hA.1
  refine ⟨b, ?_, ?_, ?_, ?_⟩
  · exact block_partition A hA
  · exact block_card A hA.1
  · exact block_lower_bound A hA
  · exact sum_bound A hA


/--
For any IsGood set A and any subset s ⊆ A, if s has more than S_i elements,
then s contains an element at least P_i.

Equivalently: the sorted enumeration of A satisfies a_{S_i} ≥ P_i.
-/
@[category API, AMS 11]
theorem growth_lemma (A : Set ℕ) (hA : IsGood A) (i : ℕ) (s : Finset ℕ)
    (hsA : (s : Set ℕ) ⊆ A) (hs_size : S_seq i < s.card) :
    ∃ n ∈ s, P i ≤ n := by
  rcases block_decomposition_proved A hA with ⟨blocks, hcover, hsize, hbound, hsum⟩
  by_contra! h_no
  let early : Finset ℕ := Finset.biUnion (Finset.range i) blocks
  have h_early_size : early.card ≤ S_seq i := by
    calc
      early.card ≤ ∑ j ∈ Finset.range i, (blocks j).card := Finset.card_biUnion_le
      _ = ∑ j ∈ Finset.range i, X j := by simp_rw [hsize]
      _ = S_seq i := by unfold S_seq; rfl
  have h_s_sub_early : s ⊆ early := by
    intro n hn
    have hnA : n ∈ A := hsA (by exact_mod_cast hn)
    rcases (hcover n).mp hnA with ⟨j, hj⟩
    by_cases hj_lt_i : j < i
    · apply Finset.mem_biUnion.mpr
      exact ⟨j, Finset.mem_range.mpr hj_lt_i, hj⟩
    · have hn_small : n < P i := h_no n hn
      have hP_j : P j ≤ n := hbound j n hj
      have hP_i_j : P i ≤ P j := P_monotone i j (Nat.le_of_not_gt hj_lt_i)
      exfalso
      exact Nat.not_lt_of_le (Nat.le_trans hP_i_j hP_j) hn_small
  have hcard_le : s.card ≤ early.card := Finset.card_le_card h_s_sub_early
  have h_contra₁ : S_seq i < early.card := Nat.lt_of_lt_of_le hs_size hcard_le
  have h_contra₂ : S_seq i < S_seq i := Nat.lt_of_lt_of_le h_contra₁ h_early_size
  exact Nat.lt_irrefl (S_seq i) h_contra₂

/-- Corollary: there are at most S_i elements of A below P_i. -/
@[category API, AMS 11]
lemma card_below_P_le_S_seq (A : Set ℕ) (hA : IsGood A) (i : ℕ) (s : Finset ℕ)
    (hsA : (s : Set ℕ) ⊆ A) (h_all_lt : ∀ n ∈ s, n < P i) : s.card ≤ S_seq i := by
  by_contra! h_gt
  have := growth_lemma A hA i s hsA h_gt
  rcases this with ⟨n, hn, hP⟩
  have hn_lt := h_all_lt n hn
  exact Nat.not_lt_of_le hP hn_lt

end Erdos12

