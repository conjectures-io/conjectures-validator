import Mathlib

/-!
Candidate FIELD-TOWER formulation of algebraic Hilbert 13 over the complex numbers.
The remaining lower-bound assertion is that no tower of steps descending to
at most two algebraically independent parameters produces a generic septimic root.
Steps use the tensor-product descent condition of Farb--Wolfson Definition 2.3
directly. The ambient algebraic closure realizes the finite tower as subfields.
-/

namespace HilbertDrafts.Hilbert13

noncomputable section

open scoped TensorProduct

abbrev ParameterRing := MvPolynomial (Fin 7) ℂ
abbrev ParameterField := FractionRing ParameterRing
abbrev Ambient := AlgebraicClosure ParameterField

/-- Seven independent coefficients: X^7 + a_6 X^6 + ... + a_0. -/
def genericSeptimic : Polynomial ParameterField :=
  Polynomial.X ^ 7 + ∑ i : Fin 7,
    Polynomial.C (algebraMap ParameterRing ParameterField (MvPolynomial.X i)) *
      Polynomial.X ^ (i : ℕ)

/-- The image of the coefficient field inside a fixed algebraic closure. -/
def coefficientField : IntermediateField ℂ Ambient :=
  IntermediateField.adjoin ℂ (Set.range (algebraMap ParameterField Ambient))

/-- Literal finite-extension descent: F is E ⊗[E₀] E₁ as an E-algebra,
where E₁/E₀ is finite and trdeg_C(E₀) ≤ d. An algebra equivalence to the field F
requires the entire tensor product to be a field, not merely a chosen factor.
The coefficient subfield E₀ need not contain the generic coefficient field. -/
def ParameterStep (d : ℕ) (E F : IntermediateField ℂ Ambient) : Prop :=
  ∃ (E₀ E₁ : IntermediateField ℂ Ambient) (h₀ : E₀ ≤ E)
      (h₁ : E₀ ≤ E₁) (hEF : E ≤ F),
    letI : Algebra E₀ E := (IntermediateField.inclusion h₀).toRingHom.toAlgebra
    letI : Algebra E₀ E₁ := (IntermediateField.inclusion h₁).toRingHom.toAlgebra
    letI : Algebra E F := (IntermediateField.inclusion hEF).toRingHom.toAlgebra
    FiniteDimensional E₀ E₁ ∧ Algebra.trdeg ℂ E₀ ≤ (d : Cardinal) ∧
      Nonempty ((E ⊗[E₀] E₁) ≃ₐ[E] F)

/-- A finite sequence of permitted algebraic adjunctions. -/
inductive ParameterTower (d : ℕ) : IntermediateField ℂ Ambient →
    IntermediateField ℂ Ambient → Prop
  | refl (E) : ParameterTower d E E
  | snoc {E F G} : ParameterTower d E F → ParameterStep d F G → ParameterTower d E G

/-- All roots in the fixed algebraic closure must lie in the final tower field. -/
def ContainsGenericRoots (E : IntermediateField ℂ Ambient) : Prop :=
  ∀ α : Ambient,
    Polynomial.eval₂ (algebraMap ParameterField Ambient) α genericSeptimic = 0 → α ∈ E

def SplitSolvableWithParameters (d : ℕ) : Prop :=
  ∃ E : IntermediateField ℂ Ambient,
    ParameterTower d coefficientField E ∧ ContainsGenericRoots E

/-- Root-cover formulation: produce a root of the generic polynomial. -/
def SolvableWithParameters (d : ℕ) : Prop :=
  ∃ (E : IntermediateField ℂ Ambient) (α : Ambient),
    ParameterTower d coefficientField E ∧ α ∈ E ∧
    Polynomial.eval₂ (algebraMap ParameterField Ambient) α genericSeptimic = 0

/-- Candidate for the open lower-bound portion RD_ℂ(7) > 2.
The known upper bound of 3 is deliberately a separate assertion. -/
def AlgebraicLowerBound : Prop := ¬ SolvableWithParameters 2

def ClassicalUpperBound : Prop := SolvableWithParameters 3

/-- The full modern algebraic Hilbert-13 assertion, RD_C(7) = 3: a
three-parameter tower exists and no two-parameter tower exists. This records
the classical upper bound as part of the statement, not as an assumed theorem. -/
def AlgebraicHilbert13 : Prop := ClassicalUpperBound ∧ AlgebraicLowerBound

/-- Regression check: a permitted step must extend, never replace, the current field. -/
theorem parameterStep_le {d : ℕ} {E F : IntermediateField ℂ Ambient}
    (h : ParameterStep d E F) : E ≤ F := by
  obtain ⟨E₀, E₁, h₀, h₁, hEF, _⟩ := h
  exact hEF

/-- The descent condition really forces a finite extension at every step. -/
theorem parameterStep_finite {d : ℕ} {E F : IntermediateField ℂ Ambient}
    (h : ParameterStep d E F) :
    ∃ hEF : E ≤ F,
      letI : Algebra E F := (IntermediateField.inclusion hEF).toRingHom.toAlgebra
      FiniteDimensional E F := by
  obtain ⟨E₀, E₁, h₀, h₁, hEF, hfin, htr, ⟨e⟩⟩ := h
  refine ⟨hEF, ?_⟩
  let : Algebra E₀ E := (IntermediateField.inclusion h₀).toRingHom.toAlgebra
  let : Algebra E₀ E₁ := (IntermediateField.inclusion h₁).toRingHom.toAlgebra
  let : Algebra E F := (IntermediateField.inclusion hEF).toRingHom.toAlgebra
  let := hfin
  exact FiniteDimensional.of_injective e.symm.toLinearMap e.symm.injective

theorem parameterTower_le {d : ℕ} {E F : IntermediateField ℂ Ambient}
    (h : ParameterTower d E F) : E ≤ F := by
  induction h with
  | refl => exact le_rfl
  | snoc ht hs ih => exact ih.trans (parameterStep_le hs)

theorem parameterStep_mono {d e : ℕ} {E F : IntermediateField ℂ Ambient}
    (hde : d ≤ e) (h : ParameterStep d E F) : ParameterStep e E F := by
  obtain ⟨E₀, E₁, h₀, h₁, hEF, hfin, htr, hiso⟩ := h
  exact ⟨E₀, E₁, h₀, h₁, hEF, hfin, htr.trans (by exact_mod_cast hde), hiso⟩

theorem parameterTower_mono {d e : ℕ} {E F : IntermediateField ℂ Ambient}
    (hde : d ≤ e) (h : ParameterTower d E F) : ParameterTower e E F := by
  induction h with
  | refl => exact .refl _
  | snoc ht hs ih => exact .snoc ih (parameterStep_mono hde hs)

theorem solvable_mono {d e : ℕ} (hde : d ≤ e)
    (h : SolvableWithParameters d) : SolvableWithParameters e := by
  obtain ⟨E, α, ht, hm, hr⟩ := h
  exact ⟨E, α, parameterTower_mono hde ht, hm, hr⟩

/-- The combined statement says exactly that three is the least allowable
parameter count; it does not rely on an infimum's default value. -/
theorem algebraicHilbert13_iff_least :
    AlgebraicHilbert13 ↔
      SolvableWithParameters 3 ∧ ∀ d : ℕ, SolvableWithParameters d → 3 ≤ d := by
  constructor
  · rintro ⟨hu, hl⟩
    refine ⟨hu, ?_⟩
    intro d hd
    by_contra hn
    have hle : d ≤ 2 := by omega
    exact hl (solvable_mono hle hd)
  · rintro ⟨hu, hleast⟩
    refine ⟨hu, ?_⟩
    intro htwo
    have hbad := hleast 2 htwo
    omega

/-- The target is genuinely degree seven, independently of any conjecture. -/
theorem genericSeptimic_monic : genericSeptimic.Monic := by
  apply Polynomial.monic_X_pow_add
  exact Polynomial.degree_sum_fin_lt _

theorem genericSeptimic_natDegree : genericSeptimic.natDegree = 7 := by
  have hlow : (∑ i : Fin 7,
      Polynomial.C (algebraMap ParameterRing ParameterField (MvPolynomial.X i)) *
        Polynomial.X ^ (i : ℕ)).degree < (7 : WithBot ℕ) :=
    Polynomial.degree_sum_fin_lt _
  unfold genericSeptimic
  rw [Polynomial.natDegree_add_eq_left_of_degree_lt]
  · simp
  · simpa using hlow

/-- The root-cover target is nonempty; it cannot succeed or fail because the
chosen ambient field lacks roots of the generic polynomial. -/
theorem genericSeptimic_has_root :
    ∃ α : Ambient,
      Polynomial.eval₂ (algebraMap ParameterField Ambient) α genericSeptimic = 0 := by
  apply IsAlgClosed.exists_eval₂_eq_zero
  intro hzero
  have hn : genericSeptimic.natDegree = 0 := Polynomial.natDegree_eq_of_degree_eq_some hzero
  rw [genericSeptimic_natDegree] at hn
  omega

end
end HilbertDrafts.Hilbert13
