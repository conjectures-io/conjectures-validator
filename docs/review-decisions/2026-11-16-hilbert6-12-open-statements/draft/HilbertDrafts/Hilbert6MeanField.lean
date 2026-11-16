import HilbertDrafts.Hilbert6

/-!
Hilbert 6: a well-known open problem in the programme, going back to Hilbert's
sixth problem, of deriving continuum equations from particle dynamics. It is
stated with the reviewed definitions of `HilbertDrafts.Hilbert6`. It is a modern
instance of that programme, not a question Hilbert posed, and it is separate
from the hard-sphere, Boltzmann and fluid derivations.

`VlasovPoissonMeanFieldLimit`: point particles in three-space move by Newton's
equations under the repulsive Coulomb force with mean-field scaling. Their
initial positions and velocities are independent samples of a smooth compactly
supported density. The statement asserts that the empirical distribution of the
particles converges in probability, at every later time, to the classical
solution of the Vlasov-Poisson equation with that initial density. The Coulomb
singularity is not truncated and no noise is added. The dimension is three, the
interaction is repulsive and the initial data are general, not monokinetic.

The statement has two parts. `VlasovPoissonExistence` is a known theorem of
Pfaffelmoser, Schaeffer and Lions and Perthame; it is included so that the
statement cannot be empty, and a proof must still establish it.
`MeanFieldConvergence` is the open part. It is expected to be true.

The known results do not decide it: the limit with the force truncated at a
radius shrinking like a small power of the particle number, the limit with
velocity noise, the two-dimensional limit for strictly positive data, and the
limit for monokinetic data. A theorem for another class of initial data, for
the torus, or for short times only would not by itself settle this statement.

The proposition is neither assumed nor proved here. The lemmas are consistency
checks on the statement, not progress on the conjecture.
-/

namespace HilbertDrafts.Hilbert6MeanField

open HilbertDrafts.Hilbert6 HilbertDrafts.Hilbert6Kinetic MeasureTheory Filter
open scoped Topology

noncomputable section

/-- A smooth compactly supported probability density on phase space. Smoothness
is stated order by order, so it cannot be read as analyticity, which no
compactly supported density has. -/
def SmoothDensity (f₀ : Phase → ℝ) : Prop :=
  (∀ n : ℕ, ContDiff ℝ n f₀) ∧ HasCompactSupport f₀ ∧
    (∀ z, 0 ≤ f₀ z) ∧ Integrable f₀ ∧ ∫ z, f₀ z = 1

/-- The Coulomb kernel `x / |x|^3`. Its value at the origin is never used:
particle positions stay distinct, and the origin is a null set in the integral. -/
def coulomb (x : Space) : Space := (‖x‖ ^ 3)⁻¹ • x

/-- The mean-field acceleration of particle `i`: the average over all `N + 1`
particles of the repulsive Coulomb force exerted by the others. -/
def acceleration {N : ℕ} (q : Fin (N + 1) → Space) (i : Fin (N + 1)) : Space :=
  (((N + 1 : ℕ) : ℝ))⁻¹ • ∑ j ∈ Finset.univ.erase i, coulomb (q i - q j)

/-- A global solution of Newton's equations for `N + 1` equal particles with
the mean-field scaled repulsive Coulomb interaction. Positions are distinct at
every time, so the kernel is never evaluated at the origin. -/
def IsCoulombTrajectory {N : ℕ} (z : ℝ → Configuration (N + 1)) : Prop :=
  ∀ t : ℝ,
    (∀ i j, i ≠ j → (z t i).1 ≠ (z t j).1) ∧
    ∀ i,
      HasDerivAt (fun s => (z s i).1) (z t i).2 t ∧
      HasDerivAt (fun s => (z s i).2) (acceleration (fun j => (z t j).1) i) t

/-- The self-consistent force field of a phase-space density. -/
def meanField (g : Phase → ℝ) (x : Space) : Space :=
  ∫ z : Phase, g z • coulomb (x - z.1)

/-- A global classical solution of the repulsive Vlasov-Poisson equation
`∂ₜ f + v · ∇ₓ f + E · ∇ᵥ f = 0`, with `E` the mean field of `f`. The solution
is continuously differentiable, nonnegative, and compactly supported uniformly
on bounded time intervals. Integrability of the force integrand is required, so
the totalized integral cannot silently return zero. -/
def IsVlasovPoissonSolution (f : ℝ → Phase → ℝ) : Prop :=
  ContDiff ℝ 1 (fun p : ℝ × Phase => f p.1 p.2) ∧
  (∀ t z, 0 ≤ f t z) ∧
  (∀ T : ℝ, ∃ R : ℝ, ∀ t : ℝ, |t| ≤ T → ∀ z : Phase, R < ‖z‖ → f t z = 0) ∧
  (∀ (t : ℝ) (x : Space), Integrable (fun z : Phase => f t z • coulomb (x - z.1))) ∧
  ∀ (t : ℝ) (x v : Space),
    fderiv ℝ (fun p : ℝ × Phase => f p.1 p.2) (t, (x, v))
      (1, (v, meanField (f t) x)) = 0

/-- Independent initial particles: the product of the one-particle density. -/
def productLaw (N : ℕ) (f₀ : Phase → ℝ) : Measure (Configuration (N + 1)) :=
  volume.withDensity (fun z => ENNReal.ofReal (∏ i, f₀ (z i)))

/-- Initial configurations from which the particle system fails to follow the
continuum prediction at time `t`: either no global trajectory starts there, or
some trajectory starting there has an empirical average of `φ` at distance at
least `δ` from the Vlasov-Poisson average. -/
def deviationSet (N : ℕ) (f : ℝ → Phase → ℝ) (t δ : ℝ) (φ : Phase → ℝ) :
    Set (Configuration (N + 1)) :=
  {z₀ | (¬ ∃ z : ℝ → Configuration (N + 1), IsCoulombTrajectory z ∧ z 0 = z₀) ∨
    ∃ z : ℝ → Configuration (N + 1), IsCoulombTrajectory z ∧ z 0 = z₀ ∧
      δ ≤ |empiricalAverage N φ (z t) - ∫ y, φ y * f t y|}

/-- The mean-field limit for the Vlasov-Poisson equation with the untruncated
repulsive Coulomb interaction. For every admissible initial density a global
classical solution exists, and for every such solution, every later time, every
continuous compactly supported observable and every tolerance, the probability
of the deviation set tends to zero as the number of particles grows. The
existence clause prevents a vacuous statement. Outer measure is used, so no
measurability of the deviation set is assumed. -/
def VlasovPoissonMeanFieldLimit : Prop :=
  ∀ f₀ : Phase → ℝ, SmoothDensity f₀ →
    (∃ f : ℝ → Phase → ℝ, IsVlasovPoissonSolution f ∧ ∀ z, f 0 z = f₀ z) ∧
    ∀ f : ℝ → Phase → ℝ, IsVlasovPoissonSolution f → (∀ z, f 0 z = f₀ z) →
      ∀ t : ℝ, 0 ≤ t →
        ∀ φ : Phase → ℝ, Continuous φ → HasCompactSupport φ →
          ∀ δ : ℝ, 0 < δ →
            Tendsto (fun N => productLaw N f₀ (deviationSet N f t δ φ)) atTop (𝓝 0)

/-- The kernel has the Coulomb magnitude `1 / |x|^2` away from the origin. -/
theorem norm_coulomb {x : Space} (hx : x ≠ 0) : ‖coulomb x‖ = (‖x‖ ^ 2)⁻¹ := by
  have hn : ‖x‖ ≠ 0 := norm_ne_zero_iff.mpr hx
  rw [coulomb, norm_smul, norm_inv, norm_pow, norm_norm]
  field_simp

/-- The force is repulsive: it points from the other particle to this one. -/
theorem coulomb_neg (x : Space) : coulomb (-x) = -coulomb x := by
  simp [coulomb]

/-- Every smooth density is an initial density in the reviewed kinetic sense. -/
theorem SmoothDensity.initialDensity {f₀ : Phase → ℝ} (h : SmoothDensity f₀) :
    InitialDensity f₀ :=
  ⟨by simpa using h.1 1, h.2.1, h.2.2.1, h.2.2.2.1, h.2.2.2.2⟩

/-- Two particles: each feels half the Coulomb force of the other. -/
theorem acceleration_pair (q : Fin 2 → Space) :
    acceleration (N := 1) q 0 = (2 : ℝ)⁻¹ • coulomb (q 0 - q 1) := by
  have h : Finset.univ.erase (0 : Fin 2) = {1} := by decide
  unfold acceleration
  rw [h, Finset.sum_singleton]
  norm_num

/-- The known part: every admissible initial density has a global classical
solution of the Vlasov-Poisson equation. -/
def VlasovPoissonExistence : Prop :=
  ∀ f₀ : Phase → ℝ, SmoothDensity f₀ →
    ∃ f : ℝ → Phase → ℝ, IsVlasovPoissonSolution f ∧ ∀ z, f 0 z = f₀ z

/-- The open part: empirical averages of the particle system converge in
probability to the averages of every classical solution with the same initial
density. -/
def MeanFieldConvergence : Prop :=
  ∀ f₀ : Phase → ℝ, SmoothDensity f₀ →
    ∀ f : ℝ → Phase → ℝ, IsVlasovPoissonSolution f → (∀ z, f 0 z = f₀ z) →
      ∀ t : ℝ, 0 ≤ t →
        ∀ φ : Phase → ℝ, Continuous φ → HasCompactSupport φ →
          ∀ δ : ℝ, 0 < δ →
            Tendsto (fun N => productLaw N f₀ (deviationSet N f t δ φ)) atTop (𝓝 0)

/-- The statement is exactly the known existence theorem together with the
open convergence statement. -/
theorem vlasovPoissonMeanFieldLimit_iff :
    VlasovPoissonMeanFieldLimit ↔ VlasovPoissonExistence ∧ MeanFieldConvergence :=
  ⟨fun h => ⟨fun f₀ h₀ => (h f₀ h₀).1, fun f₀ h₀ => (h f₀ h₀).2⟩,
    fun h f₀ h₀ => ⟨h.1 f₀ h₀, h.2 f₀ h₀⟩⟩

/-- A single particle feels no force. -/
theorem acceleration_single (q : Fin 1 → Space) (i : Fin 1) : acceleration q i = 0 := by
  have h : Finset.univ.erase i = (∅ : Finset (Fin 1)) := by
    ext j
    simp [Subsingleton.elim j i]
  unfold acceleration
  rw [h, Finset.sum_empty, smul_zero]

/-- The notion of trajectory is satisfiable: one particle moves freely. -/
theorem freeParticle_isCoulombTrajectory (x₀ v₀ : Space) :
    IsCoulombTrajectory (N := 0) (fun t _ => (x₀ + t • v₀, v₀)) := by
  intro t
  have hsub : ∀ i j : Fin (0 + 1), i = j := fun i j =>
    Fin.ext (by have := i.isLt; have := j.isLt; omega)
  refine ⟨fun i j hij => absurd (hsub i j) hij, fun i => ⟨?_, ?_⟩⟩
  · have h : HasDerivAt (fun s : ℝ => x₀ + s • v₀) v₀ t := by
      simpa using ((hasDerivAt_id t).smul_const v₀).const_add x₀
    exact h
  · rw [acceleration_single]
    exact hasDerivAt_const t v₀

/-- The zero density solves the equation, so the solution predicate is
consistent. It is not an admissible initial density. -/
theorem zero_isVlasovPoissonSolution : IsVlasovPoissonSolution (fun _ _ => 0) := by
  refine ⟨contDiff_const, fun _ _ => le_rfl, fun _ => ⟨0, fun _ _ _ _ => rfl⟩, ?_, ?_⟩
  · intro t x
    simp
  · intro t x v
    simp

/-- A trajectory starting at a configuration keeps that configuration's
particles distinct, so coincident initial positions lie in every deviation set. -/
theorem trajectory_distinct {N : ℕ} {z : ℝ → Configuration (N + 1)}
    (h : IsCoulombTrajectory z) (t : ℝ) {i j : Fin (N + 1)} (hij : i ≠ j) :
    (z t i).1 ≠ (z t j).1 := (h t).1 i j hij

end

end HilbertDrafts.Hilbert6MeanField
