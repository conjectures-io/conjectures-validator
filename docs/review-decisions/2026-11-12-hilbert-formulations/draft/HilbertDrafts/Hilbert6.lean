import Mathlib

/-!
Hilbert 6: probability, mean values, finite classical mechanics, and kinetic/
fluid-limit formulations in three dimensions.
The second namespace supplies a closed target, with explicit elastic dynamics,
conditioned product initial laws, collision integral, and limiting quantifiers.
This kinetic component is not the entire axiomatization-of-physics program.
-/

namespace HilbertDrafts.Hilbert6

open MeasureTheory Filter
open scoped Topology

abbrev Space := EuclideanSpace ℝ (Fin 3)
abbrev Phase := Space × Space
abbrev Configuration (N : ℕ) := Fin N → Phase

/-- Hard spheres in Euclidean three-space; this is not the periodic box model. -/
def Nonoverlap {N : ℕ} (diameter : ℝ) (z : Configuration N) : Prop :=
  0 < diameter ∧ ∀ i j, i ≠ j → diameter ≤ dist (z i).1 (z j).1

/-- Newtonian free flight between collisions. Collision times and boundary
events require their own construction and are not hidden in this predicate. -/
def FreeFlight {N : ℕ} (z : ℝ → Configuration N) (I : Set ℝ) : Prop :=
  ∀ t ∈ I, ∀ i,
    HasDerivAt (fun s => (z s i).1) (z t i).2 t ∧
    HasDerivAt (fun s => (z s i).2) 0 t

/-- Three-dimensional Boltzmann--Grad scaling, with a nonzero finite limit.
The indexing uses N+1 particles, so an empty system is never averaged. -/
def BoltzmannGrad (diameter : ℕ → ℝ) (density : ℝ) : Prop :=
  0 < density ∧ (∀ N, 0 < diameter N) ∧
    Tendsto diameter atTop (𝓝 0) ∧
    Tendsto (fun N => ((N + 1 : ℕ) : ℝ) * diameter N ^ 2) atTop (𝓝 density)

noncomputable def empiricalAverage (N : ℕ) (φ : Phase → ℝ)
    (z : Configuration (N + 1)) : ℝ :=
  (∑ i, φ (z i)) / ((N + 1 : ℕ) : ℝ)

/-- Expected empirical observable under an N+1-particle probability law. -/
noncomputable def microscopicObservable (N : ℕ)
    (law : Measure (Configuration (N + 1))) (φ : Phase → ℝ) : ℝ :=
  ∫ z, empiricalAverage N φ z ∂law

/-- Uniform-in-time convergence of expected one-particle observables on [0,T].
This does NOT assert concentration, higher marginal convergence, or a PDE.
The laws are normalized; explicit integrability prevents Lean's totalized
Bochner integral from silently assigning zero to nonintegrable observables. -/
def ObservableLimit
    (micro : (N : ℕ) → ℝ → Measure (Configuration (N + 1)))
    (continuum : ℝ → Measure Phase) (T : ℝ) : Prop :=
  0 < T ∧
  (∀ N t, t ∈ Set.Icc 0 T → IsProbabilityMeasure (micro N t)) ∧
  (∀ t, t ∈ Set.Icc 0 T → IsProbabilityMeasure (continuum t)) ∧
  ∀ φ : Phase → ℝ, Continuous φ → HasCompactSupport φ →
    (∀ N t, t ∈ Set.Icc 0 T → Integrable (empiricalAverage N φ) (micro N t)) ∧
    (∀ t, t ∈ Set.Icc 0 T → Integrable φ (continuum t)) ∧
    ∀ η : ℝ, 0 < η → ∃ N₀ : ℕ, ∀ N ≥ N₀, ∀ t ∈ Set.Icc 0 T,
      |microscopicObservable N (micro N t) φ - ∫ z, φ z ∂(continuum t)| < η

/-- A normalization regression: a constant observable has exactly that value
on every nonempty finite configuration. -/
theorem empiricalAverage_const (N : ℕ) (c : ℝ) (z : Configuration (N + 1)) :
    empiricalAverage N (fun _ => c) z = c := by
  have hN : (N : ℝ) + 1 ≠ 0 := by positivity
  simp [empiricalAverage, mul_comm, hN]

/-- A warning encoded as a theorem: frozen configurations meet free-flight
equations when velocities vanish. The equations alone do not select a gas law. -/
theorem staticConfiguration_freeFlight {N : ℕ} (q : Fin N → Space) (I : Set ℝ) :
    FreeFlight (fun _ i => (q i, 0)) I := by
  intro t ht i
  exact ⟨hasDerivAt_const t (q i), hasDerivAt_const t 0⟩

end HilbertDrafts.Hilbert6

namespace HilbertDrafts.Hilbert6Kinetic

open HilbertDrafts.Hilbert6 MeasureTheory Filter
open scoped Topology InnerProductSpace

noncomputable section

/-- Equal-mass elastic scattering. -/
def scatterLeft (v w n : Space) : Space := v - inner ℝ (v - w) n • n
def scatterRight (v w n : Space) : Space := w + inner ℝ (v - w) n • n

/-- A genuine incoming binary collision, with the line of centres fixing the
normal. Every unaffected particle retains its velocity. -/
def BinaryCollision {N : ℕ} (ε : ℝ) (q : Fin N → Space)
    (before after : Fin N → Space) : Prop :=
  ∃ i j : Fin N, i ≠ j ∧ dist (q i) (q j) = ε ∧
    let n := ε⁻¹ • (q i - q j)
    inner ℝ (before i - before j) n < 0 ∧
    after i = scatterLeft (before i) (before j) n ∧
    after j = scatterRight (before i) (before j) n ∧
    ∀ k, k ≠ i → k ≠ j → after k = before k

/-- Finite collision histories on [0,T]. Positions are continuous, motion
between collisions is exactly affine, and velocities use the right-continuous
convention. Endpoints and the last velocity are specified explicitly. -/
def CollisionPath {N : ℕ} (ε T : ℝ) (z : ℝ → Configuration N) : Prop :=
  0 < ε ∧ 0 < T ∧
  (∀ t ∈ Set.Icc 0 T, Nonoverlap ε (z t)) ∧
  ∃ (m : ℕ) (times : Fin (m + 2) → ℝ)
    (velocities : Fin (m + 1) → Fin N → Space),
    StrictMono times ∧ times 0 = 0 ∧ times (Fin.last (m + 1)) = T ∧
    (∀ k : Fin (m + 1), ∀ t ∈ Set.Icc (times k.castSucc) (times k.succ), ∀ i,
      (z t i).1 = (z (times k.castSucc) i).1 +
        (t - times k.castSucc) • velocities k i) ∧
    (∀ k : Fin (m + 1), ∀ t ∈ Set.Ico (times k.castSucc) (times k.succ), ∀ i,
      (z t i).2 = velocities k i) ∧
    (∀ i, (z T i).2 = velocities (Fin.last m) i) ∧
    ∀ k : Fin m,
      BinaryCollision ε (fun i => (z (times k.succ.castSucc) i).1)
        (velocities k.castSucc) (velocities k.succ)

/-- A measurable realization of hard-sphere dynamics outside a Lebesgue-null
exceptional set. Singular simultaneous/grazing initial conditions are not
required to have a chosen dynamics. No statistical convergence is assumed. -/
def IsHardSphereFlow {N : ℕ} (ε T : ℝ)
    (Φ : ℝ → Configuration N → Configuration N) : Prop :=
  (∀ t ∈ Set.Icc 0 T, Measurable (Φ t)) ∧
  ∀ᵐ z ∂(volume.restrict {z : Configuration N | Nonoverlap ε z}),
    Φ 0 z = z ∧ CollisionPath ε T (fun t => Φ t z)

/-- Surface-area integration on S² through the usual spherical coordinates;
the sin(theta) Jacobian is explicit, avoiding Hausdorff normalization ambiguity. -/
def spherePoint (θ ϕ : ℝ) : Space :=
  WithLp.toLp 2 ![Real.sin θ * Real.cos ϕ, Real.sin θ * Real.sin ϕ, Real.cos θ]

def collisionIntegrand (f : ℝ → Phase → ℝ) (t : ℝ) (x v w : Space)
    (θ ϕ : ℝ) : ℝ :=
  let n := spherePoint θ ϕ
  Real.sin θ * max (inner ℝ (v - w) n) 0 *
    (f t (x, scatterLeft v w n) * f t (x, scatterRight v w n) -
      f t (x, v) * f t (x, w))

def collisionAngular (f : ℝ → Phase → ℝ) (t : ℝ) (x v w : Space) : ℝ :=
  ∫ θ in (0 : ℝ)..Real.pi, ∫ ϕ in (0 : ℝ)..(2 * Real.pi),
    collisionIntegrand f t x v w θ ϕ

def collisionOperator (f : ℝ → Phase → ℝ) (t : ℝ) (x v : Space) : ℝ :=
  ∫ w : Space, collisionAngular f t x v w

def CollisionIntegrable (f : ℝ → Phase → ℝ) (t : ℝ) (x v : Space) : Prop :=
  Integrable (collisionAngular f t x v) ∧
  (∀ w, IntervalIntegrable (fun θ => ∫ ϕ in (0 : ℝ)..(2 * Real.pi),
      collisionIntegrand f t x v w θ ϕ) volume 0 Real.pi) ∧
  ∀ w θ, θ ∈ Set.Icc 0 Real.pi →
    IntervalIntegrable (collisionIntegrand f t x v w θ) volume 0 (2 * Real.pi)

/-- A concrete smooth, compactly supported probability density. This selects
a regular subclass of the weighted initial-data hypotheses in the source. -/
def InitialDensity (f₀ : Phase → ℝ) : Prop :=
  ContDiff ℝ 1 f₀ ∧ HasCompactSupport f₀ ∧
    (∀ z, 0 ≤ f₀ z) ∧ Integrable f₀ ∧ ∫ z, f₀ z = 1

/-- The hard-sphere Boltzmann equation written along straight characteristics.
The initial trace, positive mass, Gaussian velocity bound, and integrability
of the displayed collision integral are all part of the explicit definition. -/
def StrongBoltzmannSolution (f₀ : Phase → ℝ) (T : ℝ) (f : ℝ → Phase → ℝ) : Prop :=
  0 < T ∧
  ContinuousOn (fun p : ℝ × Phase => f p.1 p.2) (Set.Icc 0 T ×ˢ Set.univ) ∧
  (∀ z, f 0 z = f₀ z) ∧
  (∀ t ∈ Set.Icc 0 T, (∀ z, 0 ≤ f t z) ∧ Integrable (f t) ∧ ∫ z, f t z = 1) ∧
  (∃ β A : ℝ, 0 < β ∧ 0 < A ∧ ∀ t ∈ Set.Icc 0 T, ∀ x v : Space,
    f t (x, v) ≤ A * Real.exp (-β * ‖v‖ ^ 2)) ∧
  ∀ t ∈ Set.Ioo 0 T, ∀ x v : Space,
    CollisionIntegrable f t x v ∧
    HasDerivAt (fun s => f s (x + (s - t) • v, v)) (collisionOperator f t x v) t

/-- N+1 particles and diameter (N+1)^(-1/2): collision rate one in dimension 3. -/
def diameter (N : ℕ) : ℝ := Real.sqrt (1 / ((N + 1 : ℕ) : ℝ))

def initialWeight (N : ℕ) (f₀ : Phase → ℝ) (z : Configuration (N + 1)) : ℝ := by
  classical
  exact if Nonoverlap (diameter N) z then ∏ i, f₀ (z i) else 0

def partitionFunction (N : ℕ) (f₀ : Phase → ℝ) : ℝ := ∫ z, initialWeight N f₀ z

def initialLaw (N : ℕ) (f₀ : Phase → ℝ) : Measure (Configuration (N + 1)) :=
  (ENNReal.ofReal (partitionFunction N f₀))⁻¹ •
    volume.withDensity (fun z => ENNReal.ofReal (initialWeight N f₀ z))

def evolvedLaw (N : ℕ) (f₀ : Phase → ℝ)
    (Φ : ℝ → Configuration (N + 1) → Configuration (N + 1)) (t : ℝ) :
    Measure (Configuration (N + 1)) := Measure.map (Φ t) (initialLaw N f₀)

/-- Uniform-in-time weak convergence and concentration of empirical means,
for each compactly supported continuous test, under the transported laws.
The probability clause is the kinetic counterpart of the fluid mean-value law. -/
def KineticConvergence (f₀ : Phase → ℝ) (T : ℝ) (f : ℝ → Phase → ℝ)
    (Φ : (N : ℕ) → ℝ → Configuration (N + 1) → Configuration (N + 1)) : Prop :=
  (∀ᶠ N in atTop, Integrable (initialWeight N f₀) ∧
    0 < partitionFunction N f₀ ∧ IsProbabilityMeasure (initialLaw N f₀)) ∧
  ∀ φ : Phase → ℝ, Continuous φ → HasCompactSupport φ →
    ∀ η : ℝ, 0 < η → ∃ N₀ : ℕ, ∀ N ≥ N₀, ∀ t ∈ Set.Icc 0 T,
      Integrable (empiricalAverage N φ) (evolvedLaw N f₀ (Φ N) t) ∧
      Integrable (fun z => φ z * f t z) ∧
      |microscopicObservable N (evolvedLaw N f₀ (Φ N) t) φ -
        ∫ z, φ z * f t z| < η ∧
      (evolvedLaw N f₀ (Φ N) t)
        {z | η ≤ |empiricalAverage N φ z - ∫ y, φ y * f t y|} < ENNReal.ofReal η

/-- Closed formulation of the Newton-to-Boltzmann kinetic component of #6.
Existence clauses prevent vacuous quantification over nonexistent flows or PDE
solutions. This canonical-ensemble variant is asserted in the reference's
Remark 1.5(4), whose proof treats the grand-canonical ensemble. Its exact
statement is an obligation here, not a certified transcription or new bounty. -/
def NewtonBoltzmannLimit : Prop :=
  (∀ f₀ : Phase → ℝ, InitialDensity f₀ →
    ∃ (T : ℝ) (f : ℝ → Phase → ℝ), StrongBoltzmannSolution f₀ T f) ∧
  (∀ (N : ℕ) (ε T : ℝ), 0 < ε → 0 < T →
    ∃ Φ : ℝ → Configuration N → Configuration N, IsHardSphereFlow ε T Φ) ∧
  ∀ (f₀ : Phase → ℝ) (T : ℝ) (f : ℝ → Phase → ℝ),
    InitialDensity f₀ → StrongBoltzmannSolution f₀ T f →
    ∀ Φ : (N : ℕ) → ℝ → Configuration (N + 1) → Configuration (N + 1),
      (∀ N, IsHardSphereFlow (diameter N) T (Φ N)) → KineticConvergence f₀ T f Φ

theorem scatter_momentum (v w n : Space) :
    scatterLeft v w n + scatterRight v w n = v + w := by
  unfold scatterLeft scatterRight
  abel

theorem diameter_pos (N : ℕ) : 0 < diameter N := by
  unfold diameter
  positivity

theorem diameter_scaling (N : ℕ) : ((N + 1 : ℕ) : ℝ) * diameter N ^ 2 = 1 := by
  rw [diameter, Real.sq_sqrt (by positivity)]
  have hN : ((N + 1 : ℕ) : ℝ) ≠ 0 := by positivity
  field_simp

end
end HilbertDrafts.Hilbert6Kinetic

/-! Wikipedia-guided additions: probability axioms, mean values, and classical
mechanics. These are concrete mathematical specifications and theorems, not
uninterpreted predicates standing for an unspecified physical theory. -/
namespace HilbertDrafts.Hilbert6Probability

open MeasureTheory ProbabilityTheory Filter
open scoped Topology ProbabilityTheory ENNReal

noncomputable section

/-- Kolmogorov data on a sigma algebra. Nonnegativity is built into ENNReal;
normalization and countable additivity are explicit. -/
structure ProbabilityAxioms (Ω : Type) [MeasurableSpace Ω] where
  probability : Set Ω → ℝ≥0∞
  empty : probability ∅ = 0
  total : probability Set.univ = 1
  additive : ∀ (s : ℕ → Set Ω), (∀ n, MeasurableSet (s n)) →
    Pairwise (fun i j => Disjoint (s i) (s j)) → probability (⋃ n, s n) = ∑' n, probability (s n)

variable {Ω : Type} [MeasurableSpace Ω]

def ProbabilityAxioms.toMeasure (P : ProbabilityAxioms Ω) : Measure Ω :=
  Measure.ofMeasurable (fun s _ => P.probability s) P.empty
    (fun s hs hd => P.additive s hs hd)

theorem ProbabilityAxioms.toMeasure_apply (P : ProbabilityAxioms Ω)
    (s : Set Ω) (hs : MeasurableSet s) : P.toMeasure s = P.probability s :=
  Measure.ofMeasurable_apply s hs

instance ProbabilityAxioms.isProbabilityMeasure (P : ProbabilityAxioms Ω) :
    IsProbabilityMeasure P.toMeasure :=
  ⟨(P.toMeasure_apply Set.univ MeasurableSet.univ).trans P.total⟩

/-- Countable-additivity data determine one and only one measure on the given
sigma algebra. No assertion about nonmeasurable event values is needed. -/
theorem ProbabilityAxioms.unique_measure (P : ProbabilityAxioms Ω)
    (μ : Measure Ω) (h : ∀ s, MeasurableSet s → μ s = P.probability s) :
    μ = P.toMeasure := by
  apply Measure.ext
  intro s hs
  rw [h s hs, P.toMeasure_apply s hs]

/-- Every probability measure supplies a model of the proposed axioms. -/
def ProbabilityAxioms.ofMeasure (μ : Measure Ω) [IsProbabilityMeasure μ] :
    ProbabilityAxioms Ω where
  probability := μ
  empty := measure_empty
  total := measure_univ
  additive := fun _s hs hd => measure_iUnion hd hs

/-- Actual model existence establishes compatibility of the probability
axioms on every nonempty sample space, without a first-order translation. -/
theorem probability_axioms_consistent [Nonempty Ω] : Nonempty (ProbabilityAxioms Ω) :=
  ⟨ProbabilityAxioms.ofMeasure (Measure.dirac (Classical.choice ‹Nonempty Ω›))⟩

/-- A fully quantified version of the method of mean values: empirical means
of pairwise independent identically distributed integrable real observables
converge almost surely to their expectation. -/
def MeanValueLaw : Prop :=
  ∀ (Ω : Type) [MeasurableSpace Ω] (μ : Measure Ω) [IsProbabilityMeasure μ]
    (X : ℕ → Ω → ℝ), Integrable (X 0) μ →
    Pairwise (fun i j => IndepFun (X i) (X j) μ) →
    (∀ i, IdentDistrib (X i) (X 0) μ μ) →
    ∀ᵐ ω ∂μ, Tendsto (fun n : ℕ => (∑ i ∈ Finset.range n, X i ω) / n)
      atTop (𝓝 (∫ ω, X 0 ω ∂μ))

/-- This component is already proved mathematics, via Mathlib's strong law. -/
theorem meanValueLaw : MeanValueLaw := by
  intro Ω _ μ _ X hint hindep hident
  exact strong_law_ae_real X hint hindep hident

end
end HilbertDrafts.Hilbert6Probability

namespace HilbertDrafts.Hilbert6Mechanics

open HilbertDrafts.Hilbert6

noncomputable section

/-- A finite classical system with time-dependent forces and positive masses.
Collisional hard-sphere dynamics are specified separately in Hilbert6Kinetic. -/
structure System (N : ℕ) where
  mass : Fin N → ℝ
  mass_positive : ∀ i, 0 < mass i
  force : ℝ → (Fin N → Space) → Fin N → Space

/-- Newton's equations on a specified time domain, in velocity variables. -/
def Newtonian {N : ℕ} (S : System N) (I : Set ℝ)
    (q v : ℝ → Fin N → Space) : Prop :=
  ∀ t ∈ I, ∀ i,
    HasDerivAt (fun s => q s i) (v t i) t ∧
    HasDerivAt (fun s => v s i) ((S.mass i)⁻¹ • S.force t (q t) i) t

/-- The same physical equations in momentum variables. This is not called a
Hamiltonian system: no potential-force hypothesis has been imposed. -/
def MomentumEquations {N : ℕ} (S : System N) (I : Set ℝ)
    (q p : ℝ → Fin N → Space) : Prop :=
  ∀ t ∈ I, ∀ i,
    HasDerivAt (fun s => q s i) ((S.mass i)⁻¹ • p t i) t ∧
    HasDerivAt (fun s => p s i) (S.force t (q t) i) t

/-- Changing variables p_i = m_i v_i preserves the equations in both
 directions. This gives a checked relation between two axiom presentations. -/
theorem newtonian_iff_momentum {N : ℕ} (S : System N) (I : Set ℝ)
    (q v : ℝ → Fin N → Space) :
    Newtonian S I q v ↔
      MomentumEquations S I q (fun t i => S.mass i • v t i) := by
  constructor
  · intro h t ht i
    have hm : S.mass i ≠ 0 := ne_of_gt (S.mass_positive i)
    refine ⟨by simpa [smul_smul, hm] using (h t ht i).1, ?_⟩
    simpa [Pi.smul_apply, smul_smul, hm] using ((h t ht i).2.fun_const_smul (S.mass i))
  · intro h t ht i
    have hm : S.mass i ≠ 0 := ne_of_gt (S.mass_positive i)
    refine ⟨by simpa [smul_smul, hm] using (h t ht i).1, ?_⟩
    simpa [Pi.smul_apply, smul_smul, hm] using ((h t ht i).2.fun_const_smul (S.mass i)⁻¹)

/-- A nonempty class of actual models: arbitrary initial positions and
velocities evolve by free motion when the force vanishes. -/
theorem freeMotion {N : ℕ} (S : System N)
    (hF : ∀ t q i, S.force t q i = 0) (q₀ v₀ : Fin N → Space) (I : Set ℝ) :
    Newtonian S I (fun t i => q₀ i + t • v₀ i) (fun _ i => v₀ i) := by
  intro t ht i
  constructor
  · simpa using ((hasDerivAt_id t).smul_const (v₀ i)).const_add (q₀ i)
  · simpa [hF] using hasDerivAt_const t (v₀ i)

end
end HilbertDrafts.Hilbert6Mechanics

namespace HilbertDrafts.Hilbert6Fluid

open scoped ContDiff

open HilbertDrafts.Hilbert6 HilbertDrafts.Hilbert6Kinetic MeasureTheory Filter
open scoped Topology InnerProductSpace

noncomputable section

/-- Lift of an integer lattice translation to Euclidean three-space. -/
def lattice (k : Fin 3 → ℤ) : Space := WithLp.toLp 2 (fun i => (k i : ℝ))
def cell : Set Space := {x | ∀ i, 0 ≤ x i ∧ x i < 1}
def phaseCell : Set Phase := {z | z.1 ∈ cell}
def Periodic {A : Type} (f : Space → A) : Prop :=
  ∀ x k, f (x + lattice k) = f x

/-- Lifted positions are separated from every periodic image. -/
def TorusNonoverlap {N : ℕ} (ε : ℝ) (z : Configuration N) : Prop :=
  0 < ε ∧ ε < 1 / 2 ∧
    ∀ i j, i ≠ j → ∀ k : Fin 3 → ℤ, ε ≤ dist (z i).1 ((z j).1 + lattice k)

def TorusCollision {N : ℕ} (ε : ℝ) (q : Fin N → Space)
    (before after : Fin N → Space) : Prop :=
  ∃ (i j : Fin N) (k : Fin 3 → ℤ), i ≠ j ∧ dist (q i) (q j + lattice k) = ε ∧
    let n := ε⁻¹ • (q i - (q j + lattice k))
    inner ℝ (before i - before j) n < 0 ∧
    after i = scatterLeft (before i) (before j) n ∧
    after j = scatterRight (before i) (before j) n ∧
    ∀ l, l ≠ i → l ≠ j → after l = before l

/-- Continuous lifts of torus positions, affine between genuine periodic
collisions. Velocities have the same right-continuous convention as above. -/
def TorusPath {N : ℕ} (ε T : ℝ) (z : ℝ → Configuration N) : Prop :=
  0 < T ∧ (∀ t ∈ Set.Icc 0 T, TorusNonoverlap ε (z t)) ∧
  ∃ (m : ℕ) (times : Fin (m + 2) → ℝ)
    (velocities : Fin (m + 1) → Fin N → Space),
    StrictMono times ∧ times 0 = 0 ∧ times (Fin.last (m + 1)) = T ∧
    (∀ k : Fin (m + 1), ∀ t ∈ Set.Icc (times k.castSucc) (times k.succ), ∀ i,
      (z t i).1 = (z (times k.castSucc) i).1 +
        (t - times k.castSucc) • velocities k i) ∧
    (∀ k : Fin (m + 1), ∀ t ∈ Set.Ico (times k.castSucc) (times k.succ), ∀ i,
      (z t i).2 = velocities k i) ∧
    (∀ i, (z T i).2 = velocities (Fin.last m) i) ∧
    ∀ k : Fin m, TorusCollision ε (fun i => (z (times k.succ.castSucc) i).1)
      (velocities k.castSucc) (velocities k.succ)

def IsTorusFlow {N : ℕ} (ε T : ℝ)
    (Φ : ℝ → Configuration N → Configuration N) : Prop :=
  (∀ t ∈ Set.Icc 0 T, Measurable (Φ t)) ∧
  ∀ᵐ z ∂(volume.restrict {z : Configuration N |
      (∀ i, (z i).1 ∈ cell) ∧ TorusNonoverlap ε z}),
    Φ 0 z = z ∧ TorusPath ε T (fun t => Φ t z)

/-- A mean free path κ corresponds to (N+1) ε² = κ⁻¹. -/
def fluidDiameter (κ : ℝ) (N : ℕ) : ℝ :=
  Real.sqrt (1 / (κ * ((N + 1 : ℕ) : ℝ)))

def torusWeight (κ : ℝ) (N : ℕ) (f₀ : Phase → ℝ)
    (z : Configuration (N + 1)) : ℝ := by
  classical
  exact if (∀ i, (z i).1 ∈ cell) ∧ TorusNonoverlap (fluidDiameter κ N) z
    then ∏ i, f₀ (z i) else 0

def torusPartition (κ : ℝ) (N : ℕ) (f₀ : Phase → ℝ) : ℝ :=
  ∫ z, torusWeight κ N f₀ z

def torusLaw (κ : ℝ) (N : ℕ) (f₀ : Phase → ℝ) : Measure (Configuration (N + 1)) :=
  (ENNReal.ofReal (torusPartition κ N f₀))⁻¹ •
    volume.withDensity (fun z => ENNReal.ofReal (torusWeight κ N f₀ z))

def torusEvolvedLaw (κ : ℝ) (N : ℕ) (f₀ : Phase → ℝ)
    (Φ : ℝ → Configuration (N + 1) → Configuration (N + 1)) (t : ℝ) :
    Measure (Configuration (N + 1)) := Measure.map (Φ t) (torusLaw κ N f₀)

/-- A local Maxwell distribution with density ρ, bulk velocity u, and
 temperature θ. Positivity of θ is required in the solution predicates. -/
def maxwell (ρ θ : ℝ) (u v : Space) : ℝ :=
  ρ / (Real.sqrt (2 * Real.pi * θ)) ^ 3 *
    Real.exp (-‖v - u‖ ^ 2 / (2 * θ))

structure FluidState where
  density : ℝ → Space → ℝ
  velocity : ℝ → Space → Space
  temperature : ℝ → Space → ℝ

def localMaxwell (U : FluidState) (t : ℝ) (z : Phase) : ℝ :=
  maxwell (U.density t z.1) (U.temperature t z.1) (U.velocity t z.1) z.2

def coordinateDerivative (f : Space → ℝ) (x : Space) (i : Fin 3) : ℝ :=
  deriv (fun a : ℝ => f (x + a • WithLp.toLp 2 (Pi.single i 1))) 0

def divergence (f : Space → Space) (x : Space) : ℝ :=
  ∑ i, coordinateDerivative (fun y => f y i) x i

def laplacian (f : Space → ℝ) (x : Space) : ℝ :=
  ∑ i, coordinateDerivative (fun y => coordinateDerivative f y i) x i

def energy (U : FluidState) (t : ℝ) (x : Space) : ℝ :=
  U.density t x * (‖U.velocity t x‖ ^ 2 + 3 * U.temperature t x) / 2

/-- Compressible monatomic Euler, in conservation form. Smoothness ensures
that all displayed total derivatives mean genuine derivatives. Periodicity,
positive density/temperature, and unit mass fix the physical interpretation. -/
def SmoothEuler (U : FluidState) (T : ℝ) : Prop :=
  0 < T ∧
  ContDiff ℝ ∞ (fun p : ℝ × Space => U.density p.1 p.2) ∧
  ContDiff ℝ ∞ (fun p : ℝ × Space => U.velocity p.1 p.2) ∧
  ContDiff ℝ ∞ (fun p : ℝ × Space => U.temperature p.1 p.2) ∧
  ∀ t ∈ Set.Icc 0 T,
    Periodic (U.density t) ∧ Periodic (U.velocity t) ∧ Periodic (U.temperature t) ∧
    (∀ x, 0 < U.density t x ∧ 0 < U.temperature t x) ∧
    (∫ x in cell, U.density t x) = 1 ∧
    ∀ x,
      deriv (fun s => U.density s x) t +
        divergence (fun y => U.density t y • U.velocity t y) x = 0 ∧
      (∀ i : Fin 3,
        deriv (fun s => U.density s x * U.velocity s x i) t +
          (∑ j : Fin 3, coordinateDerivative (fun y =>
            U.density t y * U.velocity t y i * U.velocity t y j +
            if i = j then U.density t y * U.temperature t y else 0) x j) = 0) ∧
      deriv (fun s => energy U s x) t + divergence (fun y =>
        (energy U t y + U.density t y * U.temperature t y) • U.velocity t y) x = 0

/-- Incompressible Navier--Stokes--Fourier with universal positive transport
coefficients. The density fluctuation is fixed to -θ (Boussinesq relation).
Mean-zero temperature makes the initial particle density integrate to one. -/
def SmoothNSF (ν χ T : ℝ) (u : ℝ → Space → Space)
    (θ p : ℝ → Space → ℝ) : Prop :=
  0 < ν ∧ 0 < χ ∧ 0 < T ∧
  ContDiff ℝ ∞ (fun q : ℝ × Space => u q.1 q.2) ∧
  ContDiff ℝ ∞ (fun q : ℝ × Space => θ q.1 q.2) ∧
  ContDiff ℝ ∞ (fun q : ℝ × Space => p q.1 q.2) ∧
  ∀ t ∈ Set.Icc 0 T, Periodic (u t) ∧ Periodic (θ t) ∧ Periodic (p t) ∧
    (∫ x in cell, θ t x) = 0 ∧ ∀ x,
      divergence (u t) x = 0 ∧
      (∀ i : Fin 3, deriv (fun s => u s x i) t +
        (∑ j : Fin 3, u t x j * coordinateDerivative (fun y => u t y i) x j) +
        coordinateDerivative (p t) x i = ν * laplacian (fun y => u t y i) x) ∧
      deriv (fun s => θ s x) t +
        (∑ j : Fin 3, u t x j * coordinateDerivative (θ t) x j) =
          χ * laplacian (θ t) x

/-- Local existence for arbitrary smooth positive periodic Euler data.
This is a known PDE theorem obligation, stronger than an equilibrium witness.
Global smooth extensions are only a representation convention: the equations
and positivity are imposed on the displayed compact time interval. -/
def EulerLocalExistence : Prop :=
  ∀ (ρ θ : Space → ℝ) (u : Space → Space),
    ContDiff ℝ ∞ ρ → ContDiff ℝ ∞ θ → ContDiff ℝ ∞ u →
    Periodic ρ → Periodic θ → Periodic u →
    (∀ x, 0 < ρ x ∧ 0 < θ x) → (∫ x in cell, ρ x) = 1 →
    ∃ (U : FluidState) (T : ℝ), SmoothEuler U T ∧
      U.density 0 = ρ ∧ U.temperature 0 = θ ∧ U.velocity 0 = u

/-- Local existence for arbitrary smooth divergence-free periodic velocity
and mean-zero temperature data, at the specified positive coefficients. -/
def NSFLocalExistence (ν χ : ℝ) : Prop :=
  0 < ν → 0 < χ → ∀ (u₀ : Space → Space) (θ₀ : Space → ℝ),
    ContDiff ℝ ∞ u₀ → ContDiff ℝ ∞ θ₀ → Periodic u₀ → Periodic θ₀ →
    (∀ x, divergence u₀ x = 0) → (∫ x in cell, θ₀ x) = 0 →
    ∃ (T : ℝ) (u : ℝ → Space → Space) (θ p : ℝ → Space → ℝ),
      SmoothNSF ν χ T u θ p ∧ u 0 = u₀ ∧ θ 0 = θ₀

def nsfInitial (u : Space → Space) (θ : Space → ℝ) (κ : ℝ) (z : Phase) : ℝ :=
  maxwell (1 - κ * θ z.1) (1 + κ * θ z.1) (κ • u z.1) z.2

def nsfFluctuation (u : Space → Space) (θ : Space → ℝ) (z : Phase) : ℝ :=
  maxwell 1 1 0 z.2 *
    (inner ℝ (u z.1) z.2 + θ z.1 * (‖z.2‖ ^ 2 - 5) / 2)

/-- Periodic, continuous tests with bounded velocity support. -/
def FluidTest (φ : Phase → ℝ) : Prop :=
  Continuous φ ∧ (∀ v, Periodic (fun x => φ (x, v))) ∧
    ∃ R : ℝ, 0 < R ∧ ∀ x v, R ≤ ‖v‖ → φ (x, v) = 0

/-- A direct particle-to-fluid limit. κ is sent to zero after the particle
limit. rescale=1 gives Euler; rescale=κ and observing at t/κ gives NSF.
Nonzero partition functions, integrability, and probability normalization are
conclusions, so totalized integrals cannot manufacture convergence. -/
def ParticleFluidLimit (initial : ℝ → Phase → ℝ) (limit : ℝ → Phase → ℝ)
    (baseline : Phase → ℝ) (rescale : ℝ → ℝ) (T : ℝ) : Prop :=
  ∀ Φ : (κ : ℝ) → (N : ℕ) → ℝ → Configuration (N + 1) → Configuration (N + 1),
    (∀ κ : ℝ, 0 < κ → ∀ᶠ N in atTop,
      IsTorusFlow (fluidDiameter κ N) (T / rescale κ) (Φ κ N)) →
    ∀ φ : Phase → ℝ, FluidTest φ → ∀ η : ℝ, 0 < η →
      ∃ κ₀ : ℝ, 0 < κ₀ ∧ ∀ κ : ℝ, 0 < κ → κ < κ₀ →
        ∃ N₀ : ℕ, ∀ N ≥ N₀,
          (∀ z, 0 ≤ initial κ z) ∧ Integrable (torusWeight κ N (initial κ)) ∧
          0 < torusPartition κ N (initial κ) ∧
          IsProbabilityMeasure (torusLaw κ N (initial κ)) ∧
          ∀ t ∈ Set.Icc 0 T,
            let law := torusEvolvedLaw κ N (initial κ) (Φ κ N) (t / rescale κ)
            IsProbabilityMeasure law ∧ Integrable (empiricalAverage N φ) law ∧
            IntegrableOn (fun z => φ z * baseline z) phaseCell ∧
            IntegrableOn (fun z => φ z * limit t z) phaseCell ∧
            |(microscopicObservable N law φ - ∫ z in phaseCell, φ z * baseline z) /
              rescale κ - ∫ z in phaseCell, φ z * limit t z| < η ∧
            law {z | η ≤ |(empiricalAverage N φ z -
              ∫ y in phaseCell, φ y * baseline y) / rescale κ -
                ∫ y in phaseCell, φ y * limit t y|} < ENNReal.ofReal η

/-- Explicit existence of microscopic flows is part of the task. -/
def TorusFlowExistence : Prop :=
  ∀ (N : ℕ) (ε T : ℝ), 0 < ε → ε < 1 / 2 → 0 < T →
    ∃ Φ : ℝ → Configuration N → Configuration N, IsTorusFlow ε T Φ

/-- Proposed periodic, canonical-ensemble fluid formulation. This is a
review candidate inspired by the source's fluid limits, not a transcription
of its grand-canonical quantitative theorem. The exact-Maxwellian Euler
initial data also differ from the source's prepared Hilbert-expansion data;
an initial-layer argument is a separate, unverified obligation. -/
def FluidLimitProgram : Prop :=
  TorusFlowExistence ∧
  EulerLocalExistence ∧
  (∀ (U : FluidState) (T : ℝ), SmoothEuler U T →
    ParticleFluidLimit (fun _ => localMaxwell U 0) (localMaxwell U)
      (fun _ => 0) (fun _ => 1) T) ∧
  ∃ ν χ : ℝ, 0 < ν ∧ 0 < χ ∧
    NSFLocalExistence ν χ ∧
    ∀ (T : ℝ) (u : ℝ → Space → Space) (θ p : ℝ → Space → ℝ),
      SmoothNSF ν χ T u θ p →
        ParticleFluidLimit (nsfInitial (u 0) (θ 0))
          (fun t => nsfFluctuation (u t) (θ t)) (fun z => maxwell 1 1 0 z.2) id T

/-- A closed statement for the selected classical gas program. Probability
axioms and Newton/momentum equivalence are separately constructed and proved.
This does not purport to axiomatize quantum field theory or general relativity. -/
def ClassicalGasProgram : Prop :=
  Hilbert6Probability.MeanValueLaw ∧ NewtonBoltzmannLimit ∧ FluidLimitProgram

theorem fluidDiameter_scaling {κ : ℝ} (hκ : 0 < κ) (N : ℕ) :
    ((N + 1 : ℕ) : ℝ) * fluidDiameter κ N ^ 2 = κ⁻¹ := by
  rw [fluidDiameter, Real.sq_sqrt (by positivity)]
  have hN : ((N + 1 : ℕ) : ℝ) ≠ 0 := by positivity
  field_simp

theorem classicalGas_implies_fluid (h : ClassicalGasProgram) : FluidLimitProgram := h.2.2

theorem classicalGas_implies_kinetic (h : ClassicalGasProgram) : NewtonBoltzmannLimit := h.2.1

end
end HilbertDrafts.Hilbert6Fluid

/-! Optional appendix: general first-order semantic tools. These illustrate
consequence, equivalence and compatibility; they are not required components
of the selected physical-model interpretation of Hilbert 6. -/
namespace HilbertDrafts.Hilbert6Logic

open FirstOrder FirstOrder.Language

universe u v

variable (L : FirstOrder.Language.{u, v})

/-- The semantic deductive closure: exactly the first-order sentences true in
all nonempty models of the specified theory. This does not assert that every
physical theory has already been translated into this language. -/
def consequences (T : L.Theory) : L.Theory := {φ | T ⊨ᵇ φ}

/-- Two presentations in the same language have the same logical content. -/
def EquivalentAxioms (T U : L.Theory) : Prop :=
  consequences L T = consequences L U

/-- Adjoining U to T is compatible precisely when the combined axioms have
a nonempty model. This is a mathematical criterion, not an asserted decision
algorithm or a claim that arbitrary extensions are always compatible. -/
def Compatible (T U : L.Theory) : Prop := (T ∪ U).IsSatisfiable

/-- An actual common model witnesses compatibility. -/
theorem compatible_of_common_model (T U : L.Theory) (M : Type*)
    [Nonempty M] [L.Structure M] (hT : M ⊨ T) (hU : M ⊨ U) :
    Compatible L T U := by
  let : M ⊨ T ∪ U := hT.union hU
  exact Theory.Model.isSatisfiable M

/-- Any compatible extension retains a model of the old axioms. -/
theorem compatible_implies_base_satisfiable (T U : L.Theory)
    (h : Compatible L T U) : T.IsSatisfiable :=
  h.mono Set.subset_union_left

/-- Equivalence can be checked by mutual semantic derivability of the axioms. -/
theorem equivalent_iff_mutual_consequence (T U : L.Theory) :
    EquivalentAxioms L T U ↔
      (∀ φ ∈ T, U ⊨ᵇ φ) ∧ (∀ φ ∈ U, T ⊨ᵇ φ) := by
  constructor
  · intro h
    constructor
    · intro φ hφ
      have hmem : φ ∈ consequences L T := Theory.models_sentence_of_mem hφ
      rwa [show consequences L T = consequences L U from h] at hmem
    · intro φ hφ
      have hmem : φ ∈ consequences L U := Theory.models_sentence_of_mem hφ
      rwa [← show consequences L T = consequences L U from h] at hmem
  · rintro ⟨hTU, hUT⟩
    apply Set.ext
    intro φ
    exact ⟨Theory.models_of_models_theory hTU, Theory.models_of_models_theory hUT⟩

/-- Every semantic consequence is already forced by finitely many axioms.
This is compactness; no effective enumeration of all consequences is claimed. -/
theorem consequence_has_finite_basis (T : L.Theory) (φ : L.Sentence) :
    φ ∈ consequences L T ↔
      ∃ S : Finset L.Sentence, (S : L.Theory) ⊆ T ∧ (S : L.Theory) ⊨ᵇ φ :=
  Theory.models_iff_finset_models

/-- All finite fragments must be jointly satisfiable, not merely each new
axiom individually. This is the exact first-order compatibility condition. -/
theorem compatible_iff_finite_fragments (T U : L.Theory) :
    Compatible L T U ↔
      ∀ S : Finset L.Sentence, (S : L.Theory) ⊆ T ∪ U → Theory.IsSatisfiable (S : L.Theory) :=
  Theory.isSatisfiable_iff_isFinitelySatisfiable

end HilbertDrafts.Hilbert6Logic

namespace HilbertDrafts.Hilbert6Rigid

open scoped Matrix.Norms.Frobenius

open HilbertDrafts.Hilbert6 MeasureTheory Filter
open scoped Topology

noncomputable section

abbrev Mat := Matrix (Fin 3) (Fin 3) ℝ

def act (A : Mat) (x : Space) : Space :=
  WithLp.toLp 2 (fun i => ∑ j, A i j * x j)

def properRotations : Set Mat := {R | R.transpose * R = 1 ∧ R.det = 1}

def cross (x y : Space) : Space :=
  WithLp.toLp 2 ![x 1 * y 2 - x 2 * y 1,
    x 2 * y 0 - x 0 * y 2, x 0 * y 1 - x 1 * y 0]

def spin (ω : Space) : Mat :=
  !![0, -ω 2, ω 1; ω 2, 0, -ω 0; -ω 1, ω 0, 0]

/-- Reference bodies are rectangular continua of arbitrary positive half-side
lengths and uniform density. Different side lengths permit anisotropic inertia. -/
def body (a : Fin 3 → ℝ) : Set Space := {x | ∀ i, |x i| ≤ a i}

/-- Principal moments up to the common positive factor mass/3, which cancels
from Euler's torque-free equations. -/
def principalMoments (a : Fin 3 → ℝ) : Fin 3 → ℝ :=
  ![a 1 ^ 2 + a 2 ^ 2, a 0 ^ 2 + a 2 ^ 2, a 0 ^ 2 + a 1 ^ 2]

/-- Angular acceleration in body coordinates, including the unequal-inertia
terms. No constant-spin assumption is made for an anisotropic body. -/
def eulerAngular (a : Fin 3 → ℝ) (ω : Space) : Space :=
  let I := principalMoments a
  WithLp.toLp 2 ![(I 1 - I 2) / I 0 * ω 1 * ω 2,
    (I 2 - I 0) / I 1 * ω 2 * ω 0, (I 0 - I 1) / I 2 * ω 0 * ω 1]

def gradient (f : Space → Space) (x : Space) : Mat :=
  fun i j => fderiv ℝ f x (WithLp.toLp 2 (Pi.single j 1)) i

def velocity (y : ℝ → Space → Space) (t : ℝ) (x : Space) : Space :=
  deriv (fun s => y s x) t

/-- Continuum axioms for a free body becoming stiff. They specify smooth
motion, integrable physical observables, conserved linear/angular momenta,
and a uniform kinetic-plus-stiffness energy bound. They do not assume a rigid
limit, a rotation field, or the Euler equations that occur in the conclusion.
This is an explicit selected model, not a universal constitutive law. -/
def StiffContinuum (a : Fin 3 → ℝ) (T : ℝ)
    (y : ℕ → ℝ → Space → Space) : Prop :=
  (∀ i, 0 < a i) ∧ 0 < T ∧
  (∀ N, ContDiff ℝ 2 (fun p : ℝ × Space => y N p.1 p.2)) ∧
  ∃ C : ℝ, 0 < C ∧ ∀ N : ℕ, ∀ t ∈ Set.Icc 0 T,
    IntegrableOn (y N t) (body a) ∧
    IntegrableOn (velocity (y N) t) (body a) ∧
    IntegrableOn (fun x => cross (y N t x) (velocity (y N) t x)) (body a) ∧
    IntegrableOn (fun x => ‖velocity (y N) t x‖ ^ 2) (body a) ∧
    IntegrableOn (fun x => Metric.infDist (gradient (y N t) x) properRotations ^ 2)
      (body a) ∧
    (∫ x in body a, velocity (y N) t x) = (∫ x in body a, velocity (y N) 0 x) ∧
    (∫ x in body a, cross (y N t x) (velocity (y N) t x)) =
      (∫ x in body a, cross (y N 0 x) (velocity (y N) 0 x)) ∧
    (∫ x in body a, ‖velocity (y N) t x‖ ^ 2) + ((N + 1 : ℕ) : ℝ) ^ 2 *
      (∫ x in body a, Metric.infDist (gradient (y N t) x) properRotations ^ 2) ≤ C

/-- Well-prepared initial position and velocity, measured in squared L² norm.
The initial spin is in body coordinates, hence R₀(ω₀ × x). -/
def InitialPreparation (a : Fin 3 → ℝ) (c₀ V₀ ω₀ : Space) (R₀ : Mat)
    (y : ℕ → ℝ → Space → Space) : Prop :=
  R₀ ∈ properRotations ∧
  Tendsto (fun N => ∫ x in body a, ‖y N 0 x - (c₀ + act R₀ x)‖ ^ 2)
    atTop (𝓝 0) ∧
  Tendsto (fun N => ∫ x in body a,
    ‖velocity (y N) 0 x - (V₀ + act R₀ (cross ω₀ x))‖ ^ 2) atTop (𝓝 0)

/-- Torque-free Euler equations and rotation kinematics. Translation is
c₀+tV₀ and is included in the convergence statement below. -/
def FreeRigidMotion (a : Fin 3 → ℝ) (T : ℝ) (R₀ : Mat) (ω₀ : Space)
    (R : ℝ → Mat) (ω : ℝ → Space) : Prop :=
  R 0 = R₀ ∧ ω 0 = ω₀ ∧ ∀ t ∈ Set.Icc 0 T,
    R t ∈ properRotations ∧
    (∀ i j, HasDerivAt (fun s => R s i j) ((R t * spin (ω t)) i j) t) ∧
    HasDerivAt ω (eulerAngular a (ω t)) t

/-- Geometric-rigidity and compactness theorem obligation for the specified
global balances. This is not proposed as a new open conjecture. Existence of
rigid dynamics is required independently of the continuum family. The limit
is uniform in time in squared L² position and concerns the whole sequence.
The separate local-elasticity bridge below supplies the continuum dynamics. -/
def ContinuumRigidLimit : Prop :=
  (∀ (a : Fin 3 → ℝ) (T : ℝ) (R₀ : Mat) (ω₀ : Space),
    (∀ i, 0 < a i) → 0 < T → R₀ ∈ properRotations →
    ∃ (R : ℝ → Mat) (ω : ℝ → Space), FreeRigidMotion a T R₀ ω₀ R ω) ∧
  ∀ (a : Fin 3 → ℝ) (T : ℝ) (c₀ V₀ ω₀ : Space) (R₀ : Mat)
    (y : ℕ → ℝ → Space → Space),
    StiffContinuum a T y → InitialPreparation a c₀ V₀ ω₀ R₀ y →
    ∃ (R : ℝ → Mat) (ω : ℝ → Space), FreeRigidMotion a T R₀ ω₀ R ω ∧
      ∀ η : ℝ, 0 < η → ∃ N₀ : ℕ, ∀ N ≥ N₀, ∀ t ∈ Set.Icc 0 T,
        IntegrableOn (fun x => ‖y N t x - (c₀ + t • V₀ + act (R t) x)‖ ^ 2)
          (body a) ∧
        (∫ x in body a, ‖y N t x - (c₀ + t • V₀ + act (R t) x)‖ ^ 2) < η

/-- Empty-distance conventions cannot make every gradient appear rotational. -/
theorem identity_is_properRotation : (1 : Mat) ∈ properRotations := by
  simp [properRotations]

theorem spin_zero : spin (0 : Space) = 0 := by
  ext i j
  fin_cases i <;> fin_cases j <;> simp [spin]

theorem eulerAngular_zero (a : Fin 3 → ℝ) : eulerAngular a 0 = 0 := by
  ext i
  fin_cases i <;> simp [eulerAngular]

/-- A genuine nonrotating rigid solution for every proper initial rotation. -/
theorem zero_spin_motion (a : Fin 3 → ℝ) (T : ℝ) (R₀ : Mat)
    (hR₀ : R₀ ∈ properRotations) :
    FreeRigidMotion a T R₀ 0 (fun _ => R₀) (fun _ => 0) := by
  refine ⟨rfl, rfl, fun t _ => ⟨hR₀, ?_, ?_⟩⟩
  · intro i j
    simpa [spin_zero] using hasDerivAt_const t (R₀ i j)
  · simpa [eulerAngular_zero] using hasDerivAt_const t (0 : Space)

/-- A smooth frame-indifferent hyperelastic material, relaxed on proper
rotations and coercive relative to distance from those rotations. These are
local constitutive assumptions, not assumed conservation laws. -/
structure HyperelasticEnergy where
  energy : Mat → ℝ
  smooth : ContDiff ℝ 2 energy
  frame_indifferent : ∀ R ∈ properRotations, ∀ F, energy (R * F) = energy F
  nonnegative : ∀ F, 0 ≤ energy F
  relaxed : ∀ R ∈ properRotations, energy R = 0
  coercive : ∃ c : ℝ, 0 < c ∧ ∀ F,
    c * Metric.infDist F properRotations ^ 2 ≤ energy F

/-- First Piola stress DW(F), in the material coordinate basis. -/
def stress (W : HyperelasticEnergy) (F : Mat) : Mat :=
  fun i j => fderiv ℝ W.energy F (Pi.single i (Pi.single j 1))

def stressDivergence (W : HyperelasticEnergy) (y : Space → Space) (x : Space) : Space :=
  WithLp.toLp 2 (fun i => ∑ j,
    Hilbert6Fluid.coordinateDerivative (fun z => stress W (gradient y z) i j) x j)

/-- Local equations for a free body of unit reference density, with stiffness
(N+1)²: y_tt=(N+1)² div DW(∇y). Each boundary face has zero traction, expressed
as the corresponding stress column being zero. Only the INITIAL energies are
uniformly bounded. Conservation of energy and momenta are not assumptions.
Smooth solutions on the chosen interval are the input to the limit theorem;
global existence for arbitrary elastic data is not asserted. -/
def LocalHyperelasticFamily (W : HyperelasticEnergy) (a : Fin 3 → ℝ) (T : ℝ)
    (y : ℕ → ℝ → Space → Space) : Prop :=
  (∀ i, 0 < a i) ∧ 0 < T ∧
  (∀ N, ContDiff ℝ 2 (fun p : ℝ × Space => y N p.1 p.2)) ∧
  (∀ N, ∀ t ∈ Set.Icc 0 T,
    (∀ x ∈ interior (body a),
      deriv (fun s => velocity (y N) s x) t =
        (((N + 1 : ℕ) : ℝ) ^ 2) • stressDivergence W (y N t) x) ∧
    (∀ j, ∀ x ∈ body a, |x j| = a j → ∀ i,
      stress W (gradient (y N t) x) i j = 0)) ∧
  ∃ C : ℝ, 0 < C ∧ ∀ N,
    (∫ x in body a, ‖velocity (y N) 0 x‖ ^ 2 / 2 +
      (((N + 1 : ℕ) : ℝ) ^ 2) * W.energy (gradient (y N 0) x)) ≤ C

/-- The local-to-global balance theorem that connects the material equations
to the compactness limit. Its proof must use the PDE, traction-free boundary,
and frame indifference to derive linear/angular momentum and energy balances.
Material existence is included. This is an obligation, not an assumed axiom. -/
def ElasticityToStiff : Prop :=
  Nonempty HyperelasticEnergy ∧
  ∀ (W : HyperelasticEnergy) (a : Fin 3 → ℝ) (T : ℝ)
    (y : ℕ → ℝ → Space → Space),
    LocalHyperelasticFamily W a T y → StiffContinuum a T y

/-- A concrete smooth candidate material, including the orientation penalty.
Its coercivity verification is a known finite-dimensional theorem obligation. -/
def polynomialStoredEnergy (F : Mat) : ℝ :=
  ‖F.transpose * F - 1‖ ^ 2 + (F.det - 1) ^ 2

def PolynomialMaterialWitness : Prop :=
  ∃ W : HyperelasticEnergy, W.energy = polynomialStoredEnergy

theorem gradient_identity (x : Space) : gradient (fun z => z) x = 1 := by
  ext i j
  fin_cases i <;> fin_cases j <;> simp [gradient]

theorem relaxed_stress_zero (W : HyperelasticEnergy) : stress W 1 = 0 := by
  have hmin : IsLocalMin W.energy (1 : Mat) := by
    filter_upwards [] with F
    rw [W.relaxed 1 identity_is_properRotation]
    exact W.nonnegative F
  have hz : fderiv ℝ W.energy (1 : Mat) = 0 := hmin.fderiv_eq_zero
  ext i j
  simp only [stress, hz]
  rfl

/-- An actual stationary continuum solution for every admitted material.
Rotating rigid motions are deliberately not asserted to solve elasticity. -/
theorem stationary_elastic_model (W : HyperelasticEnergy) (a : Fin 3 → ℝ) (T : ℝ)
    (ha : ∀ i, 0 < a i) (hT : 0 < T) :
    LocalHyperelasticFamily W a T (fun _ _ x => x) := by
  refine ⟨ha, hT, fun _ => by fun_prop, ?_, 1, by norm_num, ?_⟩
  · intro N t _
    constructor
    · intro x _
      simp only [velocity, stressDivergence, gradient_identity, relaxed_stress_zero,
        Matrix.zero_apply,
        Hilbert6Fluid.coordinateDerivative]
      simp only [deriv_const, Finset.sum_const_zero]
      change (0 : Space) = _ • (0 : Space)
      simp
    · intro j x _ _ i
      simp [gradient_identity, relaxed_stress_zero]
  · intro N
    simp [velocity, gradient_identity, W.relaxed 1 identity_is_properRotation]

/-- Selected classical specifications covering both directions of limiting
process requested in Hilbert's original text. This is a candidate collection,
not a proved equivalence to the entire physical-sciences research program. -/
def ClassicalLimitsProgram : Prop :=
  Hilbert6Fluid.ClassicalGasProgram ∧ PolynomialMaterialWitness ∧
    ElasticityToStiff ∧ ContinuumRigidLimit

end
end HilbertDrafts.Hilbert6Rigid
