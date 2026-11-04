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

import FormalConjectures.Erdos12.Basic
import FormalConjectures.Erdos12.lemma_block_decomposition

/-!
# Part (iii) — Σ 1/n Convergence for Good Sets

The block construction partitions any IsGood A into blocks
|block i| = X_i with elements ≥ P_i, and proves Σ_{n∈A} 1/n < ∞.
This file provides the theorem statement proved by the bridge axioms.
-/

open Real

namespace Erdos12

set_option maxHeartbeats 200000

theorem summable_of_isGood (A : Set ℕ) (hA : IsGood A) :
    Summable (fun (n : A) ↦ (1 / (n : ℝ))) := by
  rcases block_decomposition_proved A hA with ⟨_blocks, _h_mem, _h_card, _h_lower, h_bound⟩
  have h_nonneg : ∀ n : A, 0 ≤ 1 / (n : ℝ) := by intro n; positivity
  set S := ∑' i : ℕ, (X i : ℝ) / (P i : ℝ) with hS_def
  have h_all_sets : ∀ (s : Finset A), ∑ n ∈ s, (1 / (n : ℝ)) ≤ S := by
    intro s
    let s' : Finset ℕ := Finset.map ⟨Subtype.val, Subtype.val_injective⟩ s
    have h_s_val : (s' : Set ℕ) ⊆ A := by
      intro x hx
      rcases Finset.mem_map.mp hx with ⟨a, _, rfl⟩
      exact a.2
    have h_eq : ∑ n ∈ s, (1 / (n : ℝ)) = ∑ m ∈ s', (1 / (m : ℝ)) := by
      simp [s', Finset.sum_map]
    rw [h_eq]; exact h_bound s' h_s_val
  exact summable_of_sum_le h_nonneg h_all_sets

end Erdos12

