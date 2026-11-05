/-
Copyright 2026 Libertas Technology Group Limited

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

import FormalConjectures.Util.ProblemImports
import FormalConjectures.Erdos12.lemma_coprime_seq

/-!
# Shared Preamble for Erdős Problem #12

All common definitions with complete proofs.
Imports `lemma_coprime_seq` for `exists_coprime_seq`.
-/

open Classical Filter Set
open Real

namespace Erdos12

set_option maxHeartbeats 800000
set_option linter.style.ams_attribute false
set_option linter.style.category_attribute false

def f : ℕ → ℕ
| 0 => 0
| n + 1 => 3 * f ((n + 1) / 2) + (n + 1) % 2

lemma f_mod_3 (n : ℕ) : f n % 3 = n % 2 := by
  cases n with
  | zero => rw [f]
  | succ n =>
    rw [f]
    have h_b : (n + 1) % 2 < 2 := Nat.mod_lt (n + 1) (by decide)
    omega

lemma f_div_3 (n : ℕ) : f n / 3 = f (n / 2) := by
  cases n with
  | zero => rw [f]
  | succ n =>
    rw [f]
    have h_b : (n + 1) % 2 < 2 := Nat.mod_lt (n + 1) (by decide)
    omega

lemma f_eq_helper (k : ℕ) : ∀ a b c, a + b + c ≤ k → f a + f b = 2 * f c → a = c ∧ b = c := by
  induction k with
  | zero =>
    intro a b c h_sum h_eq
    have ha : a = 0 := by omega
    have hb : b = 0 := by omega
    have hc : c = 0 := by omega
    omega
  | succ k ih =>
    intro a b c h_sum h_eq
    by_cases h_k : a + b + c ≤ k
    · exact ih a b c h_k h_eq
    · have h_mod : (f a + f b) % 3 = (2 * f c) % 3 := by rw [h_eq]
      have ha3 := f_mod_3 a
      have hb3 := f_mod_3 b
      have hc3 := f_mod_3 c
      have ha_bound : a % 2 < 2 := Nat.mod_lt a (by decide)
      have hb_bound : b % 2 < 2 := Nat.mod_lt b (by decide)
      have hc_bound : c % 2 < 2 := Nat.mod_lt c (by decide)
      have h_mod2_left : (a % 2 + b % 2) % 3 = a % 2 + b % 2 := Nat.mod_eq_of_lt (by omega)
      have h_mod2_right : (2 * (c % 2)) % 3 = 2 * (c % 2) := Nat.mod_eq_of_lt (by omega)
      have h_mod2 : (a % 2 + b % 2) % 3 = (2 * (c % 2)) % 3 := by omega
      rw [h_mod2_left, h_mod2_right] at h_mod2
      have h_parity : a % 2 = c % 2 ∧ b % 2 = c % 2 := by omega
      have h_div : f a / 3 + f b / 3 = 2 * (f c / 3) := by
        have ha_div : f a = 3 * (f a / 3) + f a % 3 := Nat.div_add_mod (f a) 3 |>.symm
        have hb_div : f b = 3 * (f b / 3) + f b % 3 := Nat.div_add_mod (f b) 3 |>.symm
        have hc_div : f c = 3 * (f c / 3) + f c % 3 := Nat.div_add_mod (f c) 3 |>.symm
        omega
      rw [f_div_3, f_div_3, f_div_3] at h_div
      have h_sum_div : a / 2 + b / 2 + c / 2 ≤ k := by
        have ha_eq : a = 2 * (a / 2) + a % 2 := Nat.div_add_mod a 2 |>.symm
        have hb_eq : b = 2 * (b / 2) + b % 2 := Nat.div_add_mod b 2 |>.symm
        have hc_eq : c = 2 * (c / 2) + c % 2 := Nat.div_add_mod c 2 |>.symm
        omega
      have h_ind := ih (a / 2) (b / 2) (c / 2) h_sum_div h_div
      have ha_eq : a = 2 * (a / 2) + a % 2 := Nat.div_add_mod a 2 |>.symm
      have hb_eq : b = 2 * (b / 2) + b % 2 := Nat.div_add_mod b 2 |>.symm
      have hc_eq : c = 2 * (c / 2) + c % 2 := Nat.div_add_mod c 2 |>.symm
      omega

lemma f_eq (a b c : ℕ) (h_eq : f a + f b = 2 * f c) : a = c ∧ b = c := by
  have h_sum : a + b + c ≤ a + b + c := Nat.le_refl _
  exact f_eq_helper (a + b + c) a b c h_sum h_eq

lemma f_le_pow (k : ℕ) : ∀ y < 2^k, f y < 3^k := by
  induction' k with k ih
  · intro y hy
    have hy0 : y = 0 := by omega
    subst hy0; simp [f]
  · intro y hy
    cases y with
    | zero => simp [f]
    | succ n =>
      rw [f]
      have hdiv_bound : (n + 1) / 2 < 2^k := by
        apply (Nat.div_lt_iff_lt_mul (by norm_num : 0 < 2)).mpr
        have hpow : 2^(k + 1) = 2 * 2^k := by ring
        omega
      have ih_div := ih ((n + 1) / 2) hdiv_bound
      have h_f_succ : f ((n + 1) / 2) + 1 ≤ 3^k := by omega
      have h_mul_succ : 3 * (f ((n + 1) / 2) + 1) ≤ 3 * 3^k :=
        Nat.mul_le_mul_left 3 h_f_succ
      have h_mod_lt_3 : (n + 1) % 2 < 3 := by
        have hlt : (n + 1) % 2 < 2 := Nat.mod_lt (n + 1) (by norm_num)
        omega
      have h3k : 3^(k + 1) = 3 * 3^k := by ring
      rw [h3k]
      calc
        3 * f ((n + 1) / 2) + (n + 1) % 2
            ≤ 3 * f ((n + 1) / 2) + 2 := by omega
        _ < 3 * (f ((n + 1) / 2) + 1) := by omega
        _ ≤ 3 * 3^k := h_mul_succ

@[irreducible] noncomputable def F : ℕ → ℕ := Classical.choose exists_coprime_seq

lemma F_ge_3 (i : ℕ) : F i ≥ 3 := by
  unfold F; exact (Classical.choose_spec exists_coprime_seq).1 i

lemma F_coprime (i j : ℕ) (h : i ≠ j) : Nat.Coprime (F i) (F j) := by
  unfold F; exact (Classical.choose_spec exists_coprime_seq).2.1 i j h

lemma F_bound (i : ℕ) : F i ≤ 2^(i + 2) := by
  unfold F; exact (Classical.choose_spec exists_coprime_seq).2.2 i

@[irreducible] noncomputable def M (i : ℕ) : ℕ := ∏ j ∈ Finset.range i, F j

lemma coprime_F_M (i : ℕ) : Nat.Coprime (F i) (M i) := by
  unfold M
  apply Nat.Coprime.prod_right
  intro j hj
  rw [Finset.mem_range] at hj
  have h_ne : i ≠ j := by omega
  exact F_coprime i j h_ne

lemma F_div_M (i j : ℕ) (h : i < j) : F i ∣ M j := by
  unfold M
  apply Finset.dvd_prod_of_mem
  simp [h]

@[irreducible] noncomputable def V (i : ℕ) : ℕ := F i * M i

lemma exists_C (i : ℕ) : ∃ c < V i, c % F i = 0 ∧ (i > 0 → c % M i = 1) := by
  unfold V
  have h_cop : Nat.Coprime (F i) (M i) := coprime_F_M i
  have h_crt := Nat.chineseRemainder h_cop 0 1
  set x := h_crt.val with hx_def
  have hx_mod_F : x ≡ (0 : ℕ) [MOD F i] := h_crt.prop.1
  have hx_mod_M : x ≡ (1 : ℕ) [MOD M i] := h_crt.prop.2
  have hprod_pos : F i * M i > 0 := by
    have hF : F i ≥ 3 := F_ge_3 i
    have hM_pos : M i > 0 := by
      unfold M
      apply Finset.prod_pos
      intro j _
      have h := F_ge_3 j
      omega
    nlinarith
  set c := x % (F i * M i) with hc_def
  have hc_lt : c < F i * M i := Nat.mod_lt _ hprod_pos
  have hc_mod_F : c ≡ x [MOD F i] := by
    have h_dvd : F i ∣ F i * M i := ⟨M i, by ring⟩
    have hc_mod_prod : c ≡ x [MOD (F i * M i)] := by
      rw [hc_def, Nat.ModEq]
      exact Nat.mod_mod x (F i * M i)
    exact Nat.ModEq.of_dvd h_dvd hc_mod_prod
  have hc_mod_M : c ≡ x [MOD M i] := by
    have h_dvd : M i ∣ F i * M i := ⟨F i, by ring⟩
    have hc_mod_prod : c ≡ x [MOD (F i * M i)] := by
      rw [hc_def, Nat.ModEq]
      exact Nat.mod_mod x (F i * M i)
    exact Nat.ModEq.of_dvd h_dvd hc_mod_prod
  have hc_mod_F_val : c % F i = 0 := by
    have hc_zero : c ≡ (0 : ℕ) [MOD F i] := hc_mod_F.trans hx_mod_F
    rw [hc_zero, show (0 : ℕ) % F i = 0 from Nat.zero_mod _]
  have hc_mod_M_val (hi : i > 0) : c % M i = 1 := by
    have hM_gt_1 : M i > 1 := by
      have h_contains_0 : (0 : ℕ) ∈ Finset.range i := by
        apply Finset.mem_range.2; omega
      have h_nonneg : ∀ j ∈ Finset.range i, 1 ≤ F j := by
        intro j hj; have h := F_ge_3 j; omega
      unfold M
      have h_single : F 0 ≤ ∏ j ∈ Finset.range i, F j :=
        Finset.single_le_prod' (s := Finset.range i) (f := F) h_nonneg h_contains_0
      have hF0 : F 0 ≥ 3 := F_ge_3 0
      omega
    have hc_one : c ≡ (1 : ℕ) [MOD M i] := hc_mod_M.trans hx_mod_M
    rw [hc_one]
    exact Nat.mod_eq_of_lt (by omega)
  exact ⟨c, hc_lt, hc_mod_F_val, hc_mod_M_val⟩

@[irreducible] noncomputable def C (i : ℕ) : ℕ := Classical.choose (exists_C i)

lemma C_mod_F_eq_0 (i : ℕ) : C i % F i = 0 := by
  unfold C; exact (Classical.choose_spec (exists_C i)).2.1

lemma C_mod_M_eq_1 (i : ℕ) (hi : i > 0) : C i % M i = 1 := by
  unfold C; exact (Classical.choose_spec (exists_C i)).2.2 hi

lemma C_lt_V (i : ℕ) : C i < V i := by
  unfold C; exact (Classical.choose_spec (exists_C i)).1

lemma C_mod_F_eq_1 (i j : ℕ) (h : i < j) : C j % F i = 1 := by
  have h_dvd : F i ∣ M j := F_div_M i j h
  calc
    C j % F i = ((M j * (C j / M j)) + (C j % M j)) % F i := by
      rw [Nat.div_add_mod (C j) (M j)]
    _ = ((M j * (C j / M j)) + 1) % F i := by rw [C_mod_M_eq_1 j (by omega)]
    _ = ((M j * (C j / M j)) % F i + 1 % F i) % F i := by rw [Nat.add_mod]
    _ = (0 + 1 % F i) % F i := by
      rw [Nat.mod_eq_zero_of_dvd (dvd_mul_of_dvd_left h_dvd _)]
    _ = 1 % F i := by simp
    _ = 1 := Nat.mod_eq_of_lt (by have h := F_ge_3 i; omega)

@[irreducible] def Y (i : ℕ) : ℕ := 3^((i+20)^3)

@[irreducible] noncomputable def P (i : ℕ) : ℕ := 10 * (V i * Y i) + C i

noncomputable def X (i : ℕ) : ℕ := Nat.sqrt (P (i + 1))

def MyBlock (i : ℕ) : Set ℕ :=
  { n | ∃ y < X i, n = P i + V i * f y }

def MySeq : Set ℕ := ⋃ i, MyBlock i

lemma pow_le_pow_left_my {a b : ℕ} (h : a ≤ b) (k : ℕ) : a^k ≤ b^k := by
  induction k with
  | zero => rfl
  | succ k ih =>
    have h1 : a * a^k ≤ b * a^k := Nat.mul_le_mul_right (a^k) h
    have h2 : b * a^k ≤ b * b^k := Nat.mul_le_mul_left b ih
    have h3 : a^(k + 1) = a * a^k := by ring
    have h4 : b^(k + 1) = b * b^k := by ring
    omega

lemma le_of_pow5_le_pow5 {a b : ℕ} (h : a^5 ≤ b^5) : a ≤ b := by
  by_contra hlt
  have h1 : b + 1 ≤ a := by omega
  have h2 : (b + 1)^5 ≤ a^5 := pow_le_pow_left_my h1 5
  have h3 : b^5 < (b + 1)^5 := by
    have : (b + 1)^5 = b^5 + 5 * b^4 + 10 * b^3 + 10 * b^2 + 5 * b + 1 := by ring
    omega
  omega

lemma bound_3_4 (A B : ℕ) (h : 4 * A ≤ 5 * B) : 3^A ≤ 4^B := by
  have h1 : 3^5 ≤ 4^4 := by decide
  have h2 : (3^5)^A ≤ (4^4)^A := pow_le_pow_left_my h1 A
  have h3 : 4^(4 * A) ≤ 4^(5 * B) := Nat.pow_le_pow_right (by decide) h
  have h4 : (3^A)^5 = 3^(A * 5) := by rw [←pow_mul]
  have h4' : 3^(A * 5) = 3^(5 * A) := by rw [Nat.mul_comm]
  have h4'' : (3^5)^A = 3^(5 * A) := by rw [←pow_mul]
  have h_3A5 : (3^A)^5 = (3^5)^A := by rw [h4, h4', ←h4'']
  have h5 : (4^A)^4 = 4^(A * 4) := by rw [←pow_mul]
  have h5' : 4^(A * 4) = 4^(4 * A) := by rw [Nat.mul_comm]
  have h5'' : (4^4)^A = 4^(4 * A) := by rw [←pow_mul]
  have h_4A4 : 4^(4 * A) = (4^4)^A := h5''.symm
  have h7 : (4^B)^5 = 4^(B * 5) := by rw [←pow_mul]
  have h7' : 4^(B * 5) = 4^(5 * B) := by rw [Nat.mul_comm]
  have h_4B5 : 4^(5 * B) = (4^B)^5 := by rw [←h7', ←h7]
  have h8 : (3^A)^5 ≤ (4^B)^5 := by
    calc (3^A)^5 = (3^5)^A := h_3A5
         _ ≤ (4^4)^A := h2
         _ = 4^(4 * A) := h5''
         _ ≤ 4^(5 * B) := h3
         _ = (4^B)^5 := h_4B5
  exact le_of_pow5_le_pow5 h8

lemma bound_11_2 (i : ℕ) : 11 * 2^((i + 4)^2) ≤ 3^((i + 5)^2) := by
  have h1 : 2^((i + 4)^2) ≤ 3^((i + 4)^2) := pow_le_pow_left_my (by decide) _
  have h2 : 11 * 2^((i + 4)^2) ≤ 11 * 3^((i + 4)^2) := Nat.mul_le_mul_left 11 h1
  have h3 : (i + 5)^2 = (i + 4)^2 + 2 * i + 9 := by ring
  have h4 : 3^((i + 5)^2) = 3^((i + 4)^2) * 3^(2 * i + 9) := by
    have h_pow : 3^((i + 4)^2 + (2 * i + 9)) = 3^((i + 4)^2) * 3^(2 * i + 9) := by rw [pow_add]
    have h_sum : (i + 4)^2 + 2 * i + 9 = (i + 4)^2 + (2 * i + 9) := by ring
    rw [h3, h_sum, h_pow]
  have h5 : 11 ≤ 3^(2 * i + 9) := by
    have : 3^9 ≤ 3^(2 * i + 9) := Nat.pow_le_pow_right (by decide) (by omega)
    have : 11 ≤ 3^9 := by decide
    omega
  have h6 : 11 * 3^((i + 4)^2) ≤ 3^(2 * i + 9) * 3^((i + 4)^2) := Nat.mul_le_mul_right _ h5
  have h7 : 3^(2 * i + 9) * 3^((i + 4)^2) = 3^((i + 4)^2) * 3^(2 * i + 9) := Nat.mul_comm _ _
  omega

lemma sqrt_mono {a b : ℕ} (h : a ≤ b) : Nat.sqrt a ≤ Nat.sqrt b :=
  Nat.sqrt_le_sqrt h

lemma M_bound_step (i : ℕ) : ∏ j ∈ Finset.range i, F j ≤ ∏ j ∈ Finset.range i, 2^(j + 2) := by
  apply Finset.prod_le_prod
  · intro j _; have := F_ge_3 j; omega
  · intro j _; exact F_bound j

lemma sum_j_plus_2 (i : ℕ) : ∑ j ∈ Finset.range i, (j + 2) ≤ (i + 2)^2 := by
  induction i with
  | zero => decide
  | succ i ih =>
    rw [Finset.sum_range_succ]
    have h_add : (i + 3)^2 = (i + 2)^2 + (2*i + 5) := by ring
    rw [h_add]
    omega

lemma prod_2_pow (i : ℕ) : ∏ j ∈ Finset.range i, 2^(j + 2) = 2^(∑ j ∈ Finset.range i, (j + 2)) := by
  induction i with
  | zero => rfl
  | succ i ih =>
    rw [Finset.prod_range_succ, Finset.sum_range_succ, ih, ← pow_add]

lemma M_bound (i : ℕ) : M i ≤ 2^((i + 2)^2) := by
  unfold M
  have h1 := M_bound_step i
  have h2 := prod_2_pow i
  have h3 := sum_j_plus_2 i
  have h4 : 2^(∑ j ∈ Finset.range i, (j + 2)) ≤ 2^((i + 2)^2) :=
    Nat.pow_le_pow_right (by decide) h3
  omega

lemma V_bound (i : ℕ) : V i ≤ 2^((i + 3)^2) := by
  unfold V
  have hF := F_bound i
  have hM := M_bound i
  have h_mul : F i * M i ≤ 2^(i + 2) * 2^((i + 2)^2) := Nat.mul_le_mul hF hM
  have h_pow : 2^(i + 2) * 2^((i + 2)^2) = 2^(i + 2 + (i + 2)^2) := by rw [← pow_add]
  have h_eq : i + 2 + (i + 2)^2 ≤ (i + 3)^2 := by nlinarith
  have h_le : 2^(i + 2 + (i + 2)^2) ≤ 2^((i + 3)^2) :=
    Nat.pow_le_pow_right (by decide) h_eq
  omega

lemma P_le_pow (i : ℕ) : P (i + 1) ≤ 4^((i+20)^3) := by
  have hV := V_bound (i + 1)
  have hC := C_lt_V (i + 1)
  have h_10V : 10 * V (i + 1) + C (i + 1) ≤ 11 * 2^((i + 4)^2) := by
    have hV2 : V (i + 1) ≤ 2^((i + 4)^2) := by
      have h_bound := V_bound (i + 1)
      have : (i + 1 + 3)^2 = (i + 4)^2 := by ring
      rwa [this] at h_bound
    omega
  have h_11 : 11 * 2^((i + 4)^2) ≤ 3^((i + 5)^2) := bound_11_2 i
  have hP : P (i + 1) ≤ (10 * V (i + 1) + C (i + 1)) * Y (i + 1) := by
    unfold P
    have h_Y_pos : 1 ≤ Y (i + 1) := by
      unfold Y; exact Nat.one_le_pow _ _ (by decide)
    nlinarith
  have h_comb : P (i + 1) ≤ 3^((i + 5)^2) * 3^((i + 21)^3) := by
    calc P (i + 1) ≤ (10 * V (i + 1) + C (i + 1)) * Y (i + 1) := hP
         _ ≤ 11 * 2^((i + 4)^2) * Y (i + 1) := Nat.mul_le_mul_right _ h_10V
         _ ≤ 3^((i + 5)^2) * Y (i + 1) := Nat.mul_le_mul_right _ h_11
         _ = 3^((i + 5)^2) * 3^((i + 21)^3) := by unfold Y; ring
  have h_exp_ineq : 4 * ((i + 5)^2 + (i + 21)^3) ≤ 5 * (i + 20)^3 := by
    ring_nf; omega
  have h_3_4 := bound_3_4 ((i + 5)^2 + (i + 21)^3) ((i + 20)^3) h_exp_ineq
  have h_comb2 : 3^((i + 5)^2) * 3^((i + 21)^3) = 3^((i + 5)^2 + (i + 21)^3) := by
    simpa using (pow_add 3 ((i + 5)^2) ((i + 21)^3)).symm
  calc
    P (i + 1) ≤ 3^((i + 5)^2) * 3^((i + 21)^3) := h_comb
    _ = 3^((i + 5)^2 + (i + 21)^3) := h_comb2
    _ ≤ 4^((i + 20)^3) := h_3_4

lemma X_le_pow (i : ℕ) : X i ≤ 2^((i+20)^3) := by
  have hP : P (i + 1) ≤ 4^((i+20)^3) := P_le_pow i
  unfold X
  have h_pow : 4^((i+20)^3) = (2^((i+20)^3))^2 := by
    have : 4 = 2^2 := rfl
    rw [this, ← pow_mul, Nat.mul_comm, pow_mul]
  have hP2 : P (i + 1) ≤ (2^((i+20)^3))^2 := by
    rw [← h_pow]; exact hP
  have h_mono : Nat.sqrt (P (i + 1)) ≤ Nat.sqrt ((2^((i+20)^3))^2) := Nat.sqrt_le_sqrt hP2
  have h_sqrt : Nat.sqrt ((2^((i+20)^3))^2) = 2^((i+20)^3) := by norm_num
  calc
    Nat.sqrt (P (i + 1)) ≤ Nat.sqrt ((2^((i+20)^3))^2) := h_mono
    _ = 2^((i+20)^3) := h_sqrt

lemma f_bound (y i : ℕ) (hy : y < X i) : f y < Y i := by
  have hy2 : y < 2^((i+20)^3) := lt_of_lt_of_le hy (X_le_pow i)
  unfold Y
  exact f_le_pow ((i+20)^3) y hy2

abbrev IsGood (A : Set ℕ) : Prop := A.Infinite ∧
  ∀ᵉ (a ∈ A) (b ∈ A) (c ∈ A), a ∣ b + c → a < b →
  a < c → b = c

end Erdos12

