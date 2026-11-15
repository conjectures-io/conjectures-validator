import HilbertDrafts.Hilbert16Open

/-!
Machine-checked non-vacuity witnesses for the two open Hilbert 16 cases.
They show that the hypotheses of the statements are satisfiable, so neither
statement can hold for an empty reason. They are not progress on the conjectures.
-/

namespace HilbertDrafts.Hilbert16OpenChecks

open HilbertDrafts.Hilbert16 HilbertDrafts.Hilbert16Classification
  HilbertDrafts.Hilbert16Open

noncomputable section

/-- The Fermat quintic `x₀⁵ + x₁⁵ + x₂⁵ + x₃⁵`. -/
def fermatQuintic : MvPolynomial (Fin 4) ℝ :=
  ∑ i : Fin 4, MvPolynomial.X i ^ 5

/-- The partial derivatives of the Fermat quintic. -/
theorem pderiv_fermatQuintic (i : Fin 4) :
    MvPolynomial.pderiv i fermatQuintic = MvPolynomial.C 5 * MvPolynomial.X i ^ 4 := by
  unfold fermatQuintic
  rw [map_sum, Finset.sum_eq_single i]
  · rw [MvPolynomial.pderiv_pow, MvPolynomial.pderiv_X_self, mul_one]
    rfl
  · intro j _ hj
    rw [MvPolynomial.pderiv_pow, MvPolynomial.pderiv_X_of_ne hj, mul_zero]
  · intro h
    exact absurd (Finset.mem_univ i) h

/-- The class of nonsingular real quintic surfaces is not empty: the Fermat
quintic is admissible. The quintic statement is therefore not vacuous. -/
theorem fermatQuintic_admissible : Admissible 3 5 true fermatQuintic := by
  refine ⟨?_, ?_, fun _ => ?_⟩
  · intro h
    have h1 := congrArg (MvPolynomial.eval (fun _ => (1 : ℝ))) h
    simp [fermatQuintic] at h1
  · unfold fermatQuintic
    exact MvPolynomial.IsHomogeneous.sum _ _ _
      fun i _ => (MvPolynomial.isHomogeneous_X ℝ i).pow 5
  · intro z hz _
    obtain ⟨i, hi⟩ : ∃ i, z i ≠ 0 := by
      by_contra h
      exact hz (funext fun j => by_contra fun hj => h ⟨j, hj⟩)
    refine ⟨i, ?_⟩
    rw [pderiv_fermatQuintic, MvPolynomial.eval₂_mul, MvPolynomial.eval₂_C,
      MvPolynomial.eval₂_pow, MvPolynomial.eval₂_X]
    exact mul_ne_zero (by simp) (pow_ne_zero 4 hi)

/-- The cubic field `x' = -y + x (1 - x² - y²)`, `y' = x + y (1 - x² - y²)`. -/
def cubicField : PolynomialVectorField where
  xComponent := -MvPolynomial.X 1 +
    MvPolynomial.X 0 * (1 - MvPolynomial.X 0 ^ 2 - MvPolynomial.X 1 ^ 2)
  yComponent := MvPolynomial.X 0 +
    MvPolynomial.X 1 * (1 - MvPolynomial.X 0 ^ 2 - MvPolynomial.X 1 ^ 2)

/-- The unit circle. -/
def unitCircle : Set Plane := {z | z 0 ^ 2 + z 1 ^ 2 = 1}

/-- The value of the cubic field at a point of the plane. -/
theorem cubicField_eval (z : Plane) :
    cubicField.eval z =
      ![-z 1 + z 0 * (1 - z 0 ^ 2 - z 1 ^ 2), z 0 + z 1 * (1 - z 0 ^ 2 - z 1 ^ 2)] := by
  simp [cubicField, PolynomialVectorField.eval]

/-- The cubic field has degree at most three. -/
theorem cubicField_degreeAtMost : cubicField.DegreeAtMost 3 := by
  have hX : ∀ i : Fin 2, (MvPolynomial.X i : MvPolynomial (Fin 2) ℝ).totalDegree = 1 :=
    fun i => MvPolynomial.totalDegree_X i
  have hsq : ∀ i : Fin 2,
      ((MvPolynomial.X i : MvPolynomial (Fin 2) ℝ) ^ 2).totalDegree ≤ 2 :=
    fun i => (MvPolynomial.totalDegree_X_pow i 2).le
  have hq : ((1 : MvPolynomial (Fin 2) ℝ) - MvPolynomial.X 0 ^ 2 -
      MvPolynomial.X 1 ^ 2).totalDegree ≤ 2 := by
    refine (MvPolynomial.totalDegree_sub _ _).trans (max_le ?_ (hsq 1))
    refine (MvPolynomial.totalDegree_sub _ _).trans (max_le ?_ (hsq 0))
    rw [MvPolynomial.totalDegree_one]
    exact Nat.zero_le 2
  have hm : ∀ i : Fin 2, ((MvPolynomial.X i : MvPolynomial (Fin 2) ℝ) *
      (1 - MvPolynomial.X 0 ^ 2 - MvPolynomial.X 1 ^ 2)).totalDegree ≤ 3 := by
    intro i
    refine (MvPolynomial.totalDegree_mul _ _).trans ?_
    rw [hX]
    omega
  refine ⟨?_, ?_⟩
  · show (-MvPolynomial.X 1 + MvPolynomial.X 0 *
      (1 - MvPolynomial.X 0 ^ 2 - MvPolynomial.X 1 ^ 2) :
        MvPolynomial (Fin 2) ℝ).totalDegree ≤ 3
    refine (MvPolynomial.totalDegree_add _ _).trans (max_le ?_ (hm 0))
    rw [MvPolynomial.totalDegree_neg, hX]
    omega
  · show (MvPolynomial.X 0 + MvPolynomial.X 1 *
      (1 - MvPolynomial.X 0 ^ 2 - MvPolynomial.X 1 ^ 2) :
        MvPolynomial (Fin 2) ℝ).totalDegree ≤ 3
    refine (MvPolynomial.totalDegree_add _ _).trans (max_le ?_ (hm 1))
    rw [hX]
    omega

/-- Every point of the unit circle is `(cos θ, sin θ)` for some angle `θ`. -/
theorem exists_angle {a b : ℝ} (h : a ^ 2 + b ^ 2 = 1) :
    ∃ θ : ℝ, Real.cos θ = a ∧ Real.sin θ = b := by
  have hn : ‖(⟨a, b⟩ : ℂ)‖ = 1 := by
    rw [Complex.norm_eq_sqrt_sq_add_sq]
    simp [h]
  obtain ⟨θ, hθ⟩ := (Complex.norm_eq_one_iff _).mp hn
  refine ⟨θ, ?_, ?_⟩
  · have h0 := congrArg Complex.re hθ
    rwa [Complex.exp_ofReal_mul_I_re] at h0
  · have h1 := congrArg Complex.im hθ
    rwa [Complex.exp_ofReal_mul_I_im] at h1

/-- The unit circle is a periodic orbit of the cubic field, traced by
`t ↦ (cos t, sin t)`. -/
theorem unitCircle_isPeriodicOrbit : IsPeriodicOrbit cubicField unitCircle := by
  refine ⟨fun t => ![Real.cos t, Real.sin t], 2 * Real.pi, Real.two_pi_pos, ?_,
    ⟨0, Real.pi, ?_⟩, ?_, ?_⟩
  · intro t
    simp
  · intro h
    have h0 := congrFun h 0
    simp at h0
    norm_num at h0
  · intro t
    have hc : Real.cos t ^ 2 + Real.sin t ^ 2 = 1 := Real.cos_sq_add_sin_sq t
    have e : cubicField.eval ![Real.cos t, Real.sin t] = ![-Real.sin t, Real.cos t] := by
      rw [cubicField_eval]
      have h0 : 1 - (![Real.cos t, Real.sin t] : Plane) 0 ^ 2 -
          (![Real.cos t, Real.sin t] : Plane) 1 ^ 2 = 0 := by
        show 1 - Real.cos t ^ 2 - Real.sin t ^ 2 = 0
        linarith
      rw [h0]
      simp
    rw [e]
    exact hasDerivAt_pi.mpr
      (Fin.forall_fin_two.mpr ⟨Real.hasDerivAt_cos t, Real.hasDerivAt_sin t⟩)
  · ext z
    constructor
    · rintro ⟨t, rfl⟩
      show Real.cos t ^ 2 + Real.sin t ^ 2 = 1
      exact Real.cos_sq_add_sin_sq t
    · intro hz
      have hz' : z 0 ^ 2 + z 1 ^ 2 = 1 := hz
      obtain ⟨θ, h0, h1⟩ := exists_angle hz'
      exact ⟨θ, funext (Fin.forall_fin_two.mpr ⟨h0, h1⟩)⟩

/-- A positive periodic solution of `f' = 2 f (1 - f)` is identically one:
the derivative vanishes at a global maximum and at a global minimum. -/
theorem periodic_logistic_eq_one {f : ℝ → ℝ} {T : ℝ} (hT : 0 < T)
    (hper : Function.Periodic f T) (hpos : ∀ t, 0 < f t)
    (hf : ∀ t, HasDerivAt f (2 * f t * (1 - f t)) t) : ∀ t, f t = 1 := by
  have hcont : Continuous f :=
    continuous_iff_continuousAt.mpr fun t => (hf t).continuousAt
  have hne : (Set.Icc (0 : ℝ) T).Nonempty := Set.nonempty_Icc.mpr hT.le
  obtain ⟨a, -, ha⟩ := isCompact_Icc.exists_isMaxOn hne hcont.continuousOn
  obtain ⟨b, -, hb⟩ := isCompact_Icc.exists_isMinOn hne hcont.continuousOn
  have hmax : ∀ t, f t ≤ f a := by
    intro t
    obtain ⟨s, hs, hst⟩ := hper.exists_mem_Ico₀ hT t
    rw [hst]
    exact isMaxOn_iff.mp ha s (Set.Ico_subset_Icc_self hs)
  have hmin : ∀ t, f b ≤ f t := by
    intro t
    obtain ⟨s, hs, hst⟩ := hper.exists_mem_Ico₀ hT t
    rw [hst]
    exact isMinOn_iff.mp hb s (Set.Ico_subset_Icc_self hs)
  have key : ∀ c, 2 * f c * (1 - f c) = 0 → f c = 1 := by
    intro c hc
    rcases mul_eq_zero.mp hc with h | h
    · have hc0 := hpos c
      linarith
    · linarith
  have hamax : IsLocalMax f a := Filter.Eventually.of_forall hmax
  have hbmin : IsLocalMin f b := Filter.Eventually.of_forall hmin
  have ha1 : f a = 1 := key a (hamax.hasDerivAt_eq_zero (hf a))
  have hb1 : f b = 1 := key b (hbmin.hasDerivAt_eq_zero (hf b))
  intro t
  have h1 := hmax t
  have h2 := hmin t
  linarith

/-- The solutions of `x' = -y`, `y' = x` are the rotations of their initial value. -/
theorem rotation_solution {x y : ℝ → ℝ} (hx : ∀ t, HasDerivAt x (-y t) t)
    (hy : ∀ t, HasDerivAt y (x t) t) (t : ℝ) :
    x t = x 0 * Real.cos t - y 0 * Real.sin t ∧
      y t = x 0 * Real.sin t + y 0 * Real.cos t := by
  have hu : ∀ s, HasDerivAt (fun r => x r * Real.cos r + y r * Real.sin r) 0 s := by
    intro s
    have h : HasDerivAt (fun r => x r * Real.cos r + y r * Real.sin r)
        (-y s * Real.cos s + x s * -Real.sin s + (x s * Real.sin s + y s * Real.cos s)) s :=
      ((hx s).mul (Real.hasDerivAt_cos s)).add ((hy s).mul (Real.hasDerivAt_sin s))
    exact h.congr_deriv (by ring)
  have hv : ∀ s, HasDerivAt (fun r => y r * Real.cos r - x r * Real.sin r) 0 s := by
    intro s
    have h : HasDerivAt (fun r => y r * Real.cos r - x r * Real.sin r)
        (x s * Real.cos s + y s * -Real.sin s - (-y s * Real.sin s + x s * Real.cos s)) s :=
      ((hy s).mul (Real.hasDerivAt_cos s)).sub ((hx s).mul (Real.hasDerivAt_sin s))
    exact h.congr_deriv (by ring)
  have hu0 : x t * Real.cos t + y t * Real.sin t = x 0 := by
    have h := is_const_of_deriv_eq_zero (fun s => (hu s).differentiableAt)
      (fun s => (hu s).deriv) t 0
    simpa using h
  have hv0 : y t * Real.cos t - x t * Real.sin t = y 0 := by
    have h := is_const_of_deriv_eq_zero (fun s => (hv s).differentiableAt)
      (fun s => (hv s).deriv) t 0
    simpa using h
  have hc : Real.cos t ^ 2 + Real.sin t ^ 2 = 1 := Real.cos_sq_add_sin_sq t
  constructor
  · linear_combination Real.cos t * hu0 - Real.sin t * hv0 - x t * hc
  · linear_combination Real.sin t * hu0 + Real.cos t * hv0 - y t * hc

/-- A periodic orbit of the cubic field that avoids the origin is the unit circle. -/
theorem periodicOrbit_eq_unitCircle {C : Set Plane} (hC : IsPeriodicOrbit cubicField C)
    (hpos : ∀ z ∈ C, 0 < z 0 ^ 2 + z 1 ^ 2) : C = unitCircle := by
  obtain ⟨η, T, hT, hper, -, hder, rfl⟩ := hC
  have hx : ∀ t, HasDerivAt (fun s => η s 0)
      (-η t 1 + η t 0 * (1 - η t 0 ^ 2 - η t 1 ^ 2)) t := by
    intro t
    have h := hasDerivAt_pi.mp (hder t) 0
    rw [cubicField_eval] at h
    exact h
  have hy : ∀ t, HasDerivAt (fun s => η s 1)
      (η t 0 + η t 1 * (1 - η t 0 ^ 2 - η t 1 ^ 2)) t := by
    intro t
    have h := hasDerivAt_pi.mp (hder t) 1
    rw [cubicField_eval] at h
    exact h
  have hf : ∀ t, HasDerivAt (fun s => η s 0 ^ 2 + η s 1 ^ 2)
      (2 * (η t 0 ^ 2 + η t 1 ^ 2) * (1 - (η t 0 ^ 2 + η t 1 ^ 2))) t := by
    intro t
    have e : (fun s => η s 0 ^ 2 + η s 1 ^ 2) =
        fun s => η s 0 * η s 0 + η s 1 * η s 1 := by
      funext s
      ring
    have h : HasDerivAt (fun s => η s 0 * η s 0 + η s 1 * η s 1)
        ((-η t 1 + η t 0 * (1 - η t 0 ^ 2 - η t 1 ^ 2)) * η t 0 +
            η t 0 * (-η t 1 + η t 0 * (1 - η t 0 ^ 2 - η t 1 ^ 2)) +
          ((η t 0 + η t 1 * (1 - η t 0 ^ 2 - η t 1 ^ 2)) * η t 1 +
            η t 1 * (η t 0 + η t 1 * (1 - η t 0 ^ 2 - η t 1 ^ 2)))) t :=
      ((hx t).mul (hx t)).add ((hy t).mul (hy t))
    rw [e]
    exact h.congr_deriv (by ring)
  have hfper : Function.Periodic (fun s => η s 0 ^ 2 + η s 1 ^ 2) T := by
    intro t
    show η (t + T) 0 ^ 2 + η (t + T) 1 ^ 2 = η t 0 ^ 2 + η t 1 ^ 2
    rw [hper t]
  have hone : ∀ t, η t 0 ^ 2 + η t 1 ^ 2 = 1 :=
    periodic_logistic_eq_one hT hfper (fun t => hpos (η t) (Set.mem_range_self t)) hf
  have hzero : ∀ t, 1 - η t 0 ^ 2 - η t 1 ^ 2 = 0 := by
    intro t
    have h := hone t
    linarith
  have hx' : ∀ t, HasDerivAt (fun s => η s 0) (-η t 1) t := by
    intro t
    refine (hx t).congr_deriv ?_
    rw [hzero t]
    ring
  have hy' : ∀ t, HasDerivAt (fun s => η s 1) (η t 0) t := by
    intro t
    refine (hy t).congr_deriv ?_
    rw [hzero t]
    ring
  have hrot := rotation_solution hx' hy'
  apply Set.Subset.antisymm
  · rintro _ ⟨t, rfl⟩
    exact hone t
  · intro z hz
    have hz' : z 0 ^ 2 + z 1 ^ 2 = 1 := hz
    obtain ⟨α, hα0, hα1⟩ := exists_angle (hone 0)
    obtain ⟨β, hβ0, hβ1⟩ := exists_angle hz'
    have hα : Real.cos α ^ 2 + Real.sin α ^ 2 = 1 := Real.cos_sq_add_sin_sq α
    obtain ⟨h0, h1⟩ := hrot (β - α)
    refine ⟨β - α, funext (Fin.forall_fin_two.mpr ⟨?_, ?_⟩)⟩
    · rw [h0, ← hα0, ← hα1, ← hβ0, Real.cos_sub, Real.sin_sub]
      linear_combination Real.cos β * hα
    · rw [h1, ← hα0, ← hα1, ← hβ1, Real.cos_sub, Real.sin_sub]
      linear_combination Real.sin β * hα

/-- The notion of limit cycle is satisfiable: the unit circle is a limit cycle
of the cubic field. A cycle bound is therefore never true merely because no
set can satisfy `IsLimitCycle`. -/
theorem unitCircle_isLimitCycle : IsLimitCycle cubicField unitCircle := by
  refine ⟨unitCircle_isPeriodicOrbit, {z | 0 < z 0 ^ 2 + z 1 ^ 2}, ?_, ?_, ?_⟩
  · exact isOpen_lt continuous_const (by fun_prop)
  · intro z hz
    have hz' : z 0 ^ 2 + z 1 ^ 2 = 1 := hz
    show 0 < z 0 ^ 2 + z 1 ^ 2
    rw [hz']
    exact one_pos
  · intro C' hC' hsub
    exact periodicOrbit_eq_unitCircle hC' fun z hz => hsub hz

/-- Consequently the bound zero fails in degree three. -/
theorem not_cycleBound_three_zero : ¬ CycleBound 3 0 := by
  intro h
  have h1 := h cubicField cubicField_degreeAtMost 1 (fun _ => unitCircle)
    (fun i j _ => Subsingleton.elim i j) (fun _ => unitCircle_isLimitCycle)
  omega

end

end HilbertDrafts.Hilbert16OpenChecks
