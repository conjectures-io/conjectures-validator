import Mathlib

/-!
Hilbert 16: the uniform-bound component and an effective-classification proposal
covering real projective curves and surfaces, exact maxima, and affine and
projective cycle arrangements. The target propositions are not assumed or proved.
The effective reading needs semantic review; the 2026 full-bound proof claim
must be reviewed separately before open-bounty admission.
-/

namespace HilbertDrafts.Hilbert16

abbrev Plane := Fin 2 → ℝ

structure PolynomialVectorField where
  xComponent : MvPolynomial (Fin 2) ℝ
  yComponent : MvPolynomial (Fin 2) ℝ

noncomputable def PolynomialVectorField.eval (V : PolynomialVectorField) (z : Plane) : Plane :=
  ![MvPolynomial.eval z V.xComponent, MvPolynomial.eval z V.yComponent]

def PolynomialVectorField.DegreeAtMost (V : PolynomialVectorField) (d : ℕ) : Prop :=
  V.xComponent.totalDegree ≤ d ∧ V.yComponent.totalDegree ≤ d

/-- An orbit is its image, rather than a time parametrization. Stationary solutions
are explicitly excluded, and a positive period is required. -/
def IsPeriodicOrbit (V : PolynomialVectorField) (C : Set Plane) : Prop :=
  ∃ (γ : ℝ → Plane) (T : ℝ),
    0 < T ∧
    Function.Periodic γ T ∧
    (∃ s t, γ s ≠ γ t) ∧
    (∀ t, HasDerivAt γ (V.eval (γ t)) t) ∧
    Set.range γ = C

/-- Standard neighbourhood isolation: the neighbourhood contains no other
entire periodic orbit. It need not exclude trajectories that merely enter it.
This is the same isolation convention as the published QuinticLienard comparator. -/
def IsLimitCycle (V : PolynomialVectorField) (C : Set Plane) : Prop :=
  IsPeriodicOrbit V C ∧
    ∃ U : Set Plane, IsOpen U ∧ C ⊆ U ∧
      ∀ C' : Set Plane, IsPeriodicOrbit V C' →
        C' ⊆ U → C' = C

/-- The bound depends only on the polynomial degree, not on its coefficients.
The bound on every finite injective family avoids treating an infinite set's
`Nat.card` or `Set.ncard` (which can be zero) as a finite upper bound. -/
def UniformBound : Prop :=
  ∀ d : ℕ, ∃ N : ℕ, ∀ V : PolynomialVectorField,
    V.DegreeAtMost d →
    ∀ (m : ℕ) (cycles : Fin m → Set Plane),
      Function.Injective cycles →
      (∀ i, IsLimitCycle V (cycles i)) → m ≤ N

theorem periodicOrbit_nonempty {V : PolynomialVectorField} {C : Set Plane}
    (h : IsPeriodicOrbit V C) : C.Nonempty := by
  obtain ⟨γ, T, hT, hp, hn, hd, rfl⟩ := h
  exact ⟨γ 0, ⟨0, rfl⟩⟩

theorem periodicOrbit_not_singleton {V : PolynomialVectorField} {C : Set Plane}
    (h : IsPeriodicOrbit V C) (z : Plane) : C ≠ {z} := by
  obtain ⟨γ, T, hT, hp, ⟨s, t, hst⟩, hd, rfl⟩ := h
  intro heq
  have hs : γ s = z := by
    have hm : γ s ∈ ({z} : Set Plane) := heq ▸ Set.mem_range_self s
    simpa only [Set.mem_singleton_iff] using hm
  have ht : γ t = z := by
    have hm : γ t ∈ ({z} : Set Plane) := heq ▸ Set.mem_range_self t
    simpa only [Set.mem_singleton_iff] using hm
  exact hst (hs.trans ht.symm)

/-- Increasing an allowed degree does not silently restrict the coefficient class. -/
theorem degreeAtMost_mono {V : PolynomialVectorField} {d e : ℕ}
    (h : V.DegreeAtMost d) (hde : d ≤ e) : V.DegreeAtMost e :=
  ⟨h.1.trans hde, h.2.trans hde⟩

/-- The zero differential equation cannot manufacture periodic orbits by
choosing an arbitrary nonconstant parametrization. -/
theorem zeroField_no_periodicOrbit (C : Set Plane) :
    ¬ IsPeriodicOrbit ⟨0, 0⟩ C := by
  rintro ⟨γ, T, hT, hp, ⟨s, t, hst⟩, hd, hr⟩
  have heval (z : Plane) : PolynomialVectorField.eval ⟨0, 0⟩ z = 0 := by
    funext i
    fin_cases i <;> simp [PolynomialVectorField.eval]
  have hz : ∀ t, HasDerivAt γ 0 t := by
    intro t
    simpa only [heval] using hd t
  exact hst (is_const_of_deriv_eq_zero
    (fun t => (hz t).differentiableAt) (fun t => (hz t).deriv) s t)

/-- A bounded family really has finitely many limit cycles. This explicitly
rules out the infinite-cardinality-evaluates-to-zero encoding error. -/
theorem uniformBound_implies_finite (h : UniformBound)
    (d : ℕ) (V : PolynomialVectorField) (hd : V.DegreeAtMost d) :
    {C : Set Plane | IsLimitCycle V C}.Finite := by
  classical
  obtain ⟨N, hN⟩ := h d
  by_contra hinf
  let : Infinite {C : Set Plane // IsLimitCycle V C} := Set.Infinite.to_subtype hinf
  let f := Infinite.natEmbedding {C : Set Plane // IsLimitCycle V C}
  have hi : Function.Injective (fun i : Fin (N + 1) => (f i).val) := by
    intro i j hij
    apply Fin.ext
    exact f.injective (Subtype.ext hij)
  have hbad := hN V hd (N + 1) (fun i => (f i).val) hi (fun i => (f i).property)
  omega

end HilbertDrafts.Hilbert16

/-!
Classification specifications for both parts of Hilbert 16. These are candidate
solution requirements, not constructed classifications or proved conjectures.
The output-type interpretation and geometry conventions are documented in
HILBERT16_SPECIFICATION.md; computability is an optional refinement.
-/


namespace HilbertDrafts.Hilbert16Classification

noncomputable section

abbrev Vector (n : ℕ) := Fin (n + 1) → ℝ
abbrev Projective (n : ℕ) := Projectivization ℝ (Vector n)

instance projectiveTopology (n : ℕ) : TopologicalSpace (Projective n) :=
  inferInstanceAs (TopologicalSpace (Quotient (projectivizationSetoid ℝ (Vector n))))

abbrev Interval := Set.Icc (0 : ℝ) 1

/-- Ambient isotopy starting at the identity. Both the maps and inverses vary
jointly continuously; intrinsic homeomorphism of the subsets is insufficient. -/
def AmbientIsotopic {X : Type} [TopologicalSpace X] (A B : Set X) : Prop :=
  ∃ H : Interval → X ≃ₜ X,
    Continuous (fun p : Interval × X => H p.1 p.2) ∧
    Continuous (fun p : Interval × X => (H p.1).symm p.2) ∧
    H ⟨0, by norm_num⟩ = Homeomorph.refl X ∧
    (H ⟨1, by norm_num⟩) '' A = B

/-- One common ambient isotopy moves all the members of an unlabelled family;
members may not be moved independently, which would forget nesting. -/
def FamilyAmbientIsotopic {X : Type} [TopologicalSpace X]
    (A B : Set (Set X)) : Prop :=
  ∃ H : Interval → X ≃ₜ X,
    Continuous (fun p : Interval × X => H p.1 p.2) ∧
    Continuous (fun p : Interval × X => (H p.1).symm p.2) ∧
    H ⟨0, by norm_num⟩ = Homeomorph.refl X ∧
    (fun C : Set X => (H ⟨1, by norm_num⟩) '' C) '' A = B

/-- A finite union of projected convex hulls of finite rational vertex lists.
These are explicit finite geometric codes with the standard Encodable instance. -/
abbrev ProjectivePolyhedron (n : ℕ) := List (List (Fin (n + 1) → ℚ))

def polyhedronLocus (n : ℕ) (c : ProjectivePolyhedron n) : Set (Projective n) :=
  {p | ∃ vertices ∈ c, ∃ x : Vector n, ∃ hx : x ≠ 0,
    x ∈ convexHull ℝ {v | ∃ q ∈ vertices, v = fun i => (q i : ℝ)} ∧
    Projectivization.mk ℝ x hx = p}

/-- Nonzero homogeneous representatives, including points at infinity. -/
def zeroLocus (n : ℕ) (p : MvPolynomial (Fin (n + 1)) ℝ) : Set (Projective n) :=
  {q | ∃ x : Vector n, ∃ hx : x ≠ 0,
    Projectivization.mk ℝ x hx = q ∧ MvPolynomial.eval x p = 0}

def ComplexNonsingular (n : ℕ) (p : MvPolynomial (Fin (n + 1)) ℝ) : Prop :=
  ∀ z : Fin (n + 1) → ℂ, z ≠ 0 →
    MvPolynomial.eval₂ (algebraMap ℝ ℂ) z p = 0 →
      ∃ i, MvPolynomial.eval₂ (algebraMap ℝ ℂ) z (MvPolynomial.pderiv i p) ≠ 0

/-- `true` is the usual complex-nonsingular class. `false` also includes
singular and reducible hypersurfaces, so that broader reading is not discarded. -/
def Admissible (n d : ℕ) (smooth : Bool)
    (p : MvPolynomial (Fin (n + 1)) ℝ) : Prop :=
  p ≠ 0 ∧ p.IsHomogeneous d ∧ (smooth = true → ComplexNonsingular n p)

/-- A bijective enumeration of the actual connected components; infinite
component families cannot be mistaken for a zero natural cardinal. -/
def ExactComponents {X : Type} [TopologicalSpace X] (S : Set X) (m : ℕ) : Prop :=
  ∃ components : Fin m → Set X, Function.Injective components ∧
    ∀ C : Set X, (∃ x ∈ S, connectedComponentIn S x = C) ↔ C ∈ Set.range components

/-- Required output of a classification, with exhaustive,
irredundant catalogues, realization in the prescribed degree, and sharp
component maxima. This is an output type, not a claim that its mere
nonemptiness expresses the remaining open classification work. -/
structure AlgebraicClassification (n : ℕ) (smooth : Bool) where
  catalogue : ℕ → List (ProjectivePolyhedron n)
  realized : ∀ d c, c ∈ catalogue d →
    ∃ p, Admissible n d smooth p ∧ AmbientIsotopic (zeroLocus n p) (polyhedronLocus n c)
  exhaustive : ∀ d p, Admissible n d smooth p →
    ∃ c ∈ catalogue d, AmbientIsotopic (zeroLocus n p) (polyhedronLocus n c)
  irredundant : ∀ d (i j : Fin (catalogue d).length),
    AmbientIsotopic (polyhedronLocus n ((catalogue d).get i))
      (polyhedronLocus n ((catalogue d).get j)) → i = j
  maximumComponents : ℕ → ℕ
  componentBound : ∀ d p, Admissible n d smooth p →
    ∃ m ≤ maximumComponents d, ExactComponents (zeroLocus n p) m
  maximumAttained : ∀ d, ∃ p, Admissible n d smooth p ∧
    ExactComponents (zeroLocus n p) (maximumComponents d)

/-- Optional algorithmic refinement of the classification output. -/
structure EffectiveAlgebraicClassification (n : ℕ) (smooth : Bool)
    extends AlgebraicClassification n smooth where
  catalogueProgram : Nat.Partrec.Code
  catalogueComputed : ∀ d, Encodable.encode (catalogue d) ∈ catalogueProgram.eval d
  maximumProgram : Nat.Partrec.Code
  maximumComputed : ∀ d, maximumComponents d ∈ maximumProgram.eval d

/-- Harnack's bound and its sharpness, for nonsingular real projective curves.
This classical benchmark is stated here, not claimed as a new open conjecture. -/
def HarnackSharpBound : Prop :=
  ∀ d : ℕ, 0 < d →
    (∀ p, Admissible 2 d true p →
      ∃ m ≤ (d - 1) * (d - 2) / 2 + 1, ExactComponents (zeroLocus 2 p) m) ∧
    ∃ p, Admissible 2 d true p ∧
      ExactComponents (zeroLocus 2 p) ((d - 1) * (d - 2) / 2 + 1)

/-- The sharp maximum of ten components for a nonsingular real quartic
surface. This supplies a concrete answer statement for the degree singled
out by Hilbert, rather than merely asserting that a catalogue exists. -/
def QuarticSurfaceMaximum : Prop :=
  (∀ p, Admissible 3 4 true p → ∃ m ≤ 10, ExactComponents (zeroLocus 3 p) m) ∧
  ∃ p, Admissible 3 4 true p ∧ ExactComponents (zeroLocus 3 p) 10

end
end HilbertDrafts.Hilbert16Classification

namespace HilbertDrafts.Hilbert16Classification

noncomputable section

/-- Finite rational polyhedra in the affine plane, with their actual placement.
Realizability below forces the catalogue entries to represent cycle families;
invalid or higher-dimensional polyhedra cannot pass that requirement. -/
abbrev AffinePolyhedron := List (List (Fin 2 → ℚ))

def affineLocus (c : AffinePolyhedron) : Set Hilbert16.Plane :=
  {x | ∃ vertices ∈ c,
    x ∈ convexHull ℝ {v | ∃ q ∈ vertices, v = fun i => (q i : ℝ)}}

abbrev AffineArrangement := List AffinePolyhedron

def affineArrangementLocus (c : AffineArrangement) : Set (Set Hilbert16.Plane) :=
  {C | ∃ p ∈ c, affineLocus p = C}

abbrev ProjectiveArrangement := List (ProjectivePolyhedron 2)

def projectiveArrangementLocus (c : ProjectiveArrangement) : Set (Set (Projective 2)) :=
  {C | ∃ p ∈ c, polyhedronLocus 2 p = C}

/-- A general output specification, instantiated below with fully defined
polynomial equations. Computability is a separate optional refinement. -/
structure CycleClassification (System X Code : Type) [TopologicalSpace X]
    [Encodable Code] (allowed : ℕ → System → Prop)
    (cycles : System → Set (Set X)) (decode : Code → Set (Set X)) where
  maximum : ℕ → ℕ
  bounded : ∀ d V, allowed d V → ∀ (m : ℕ) (cs : Fin m → Set X),
    Function.Injective cs → (∀ i, cs i ∈ cycles V) → m ≤ maximum d
  attained : ∀ d, ∃ V, allowed d V ∧ ∃ cs : Fin (maximum d) → Set X,
    Function.Injective cs ∧ Set.range cs = cycles V
  catalogue : ℕ → List Code
  realized : ∀ d c, c ∈ catalogue d →
    ∃ V, allowed d V ∧ FamilyAmbientIsotopic (cycles V) (decode c)
  exhaustive : ∀ d V, allowed d V →
    ∃ c ∈ catalogue d, FamilyAmbientIsotopic (cycles V) (decode c)
  irredundant : ∀ d (i j : Fin (catalogue d).length),
    FamilyAmbientIsotopic (decode ((catalogue d).get i))
      (decode ((catalogue d).get j)) → i = j

/-- Optional uniform algorithms for the maxima and arrangement catalogues;
the historical cycle-bound question does not impose this extra requirement. -/
structure EffectiveCycleClassification (System X Code : Type) [TopologicalSpace X]
    [Encodable Code] (allowed : ℕ → System → Prop)
    (cycles : System → Set (Set X)) (decode : Code → Set (Set X))
    extends CycleClassification System X Code allowed cycles decode where
  maximumProgram : Nat.Partrec.Code
  maximumComputed : ∀ d, maximum d ∈ maximumProgram.eval d
  catalogueProgram : Nat.Partrec.Code
  catalogueComputed : ∀ d, Encodable.encode (catalogue d) ∈ catalogueProgram.eval d

abbrev AffineCycleClassification :=
  CycleClassification Hilbert16.PolynomialVectorField Hilbert16.Plane AffineArrangement
    (fun d V => V.DegreeAtMost d) (fun V => {C | Hilbert16.IsLimitCycle V C})
    affineArrangementLocus

/-- Homogeneous polynomial vector fields on nonzero triples. The induced
projective direction is unchanged by adding a radial component. -/
structure HomogeneousVectorField where
  component : Fin 3 → MvPolynomial (Fin 3) ℝ

def HomogeneousVectorField.Degree (V : HomogeneousVectorField) (d : ℕ) : Prop :=
  ∀ i, (V.component i).IsHomogeneous d

def HomogeneousVectorField.eval (V : HomogeneousVectorField) (x : Vector 2) : Vector 2 :=
  fun i => MvPolynomial.eval x (V.component i)

/-- Projective trajectories are defined by nonzero lifts. The nonvanishing
continuous speed permits reparametrization, and the radial term removes the
choice of representative. Periodicity and nonconstancy concern projective
points, so radial motion does not count as an orbit. Lifts need not be periodic. -/
def IsProjectivePeriodicOrbit (V : HomogeneousVectorField)
    (C : Set (Projective 2)) : Prop :=
  ∃ (γ : ℝ → Vector 2) (hγ : ∀ t, γ t ≠ 0) (a b : ℝ → ℝ) (T : ℝ),
    0 < T ∧ Continuous a ∧ Continuous b ∧ (∀ t, a t ≠ 0) ∧
    (∀ t, HasDerivAt γ (a t • V.eval (γ t) + b t • γ t) t) ∧
    Function.Periodic (fun t => Projectivization.mk ℝ (γ t) (hγ t)) T ∧
    (∃ s t, Projectivization.mk ℝ (γ s) (hγ s) ≠
      Projectivization.mk ℝ (γ t) (hγ t)) ∧
    Set.range (fun t => Projectivization.mk ℝ (γ t) (hγ t)) = C

def IsProjectiveLimitCycle (V : HomogeneousVectorField)
    (C : Set (Projective 2)) : Prop :=
  IsProjectivePeriodicOrbit V C ∧
    ∃ U : Set (Projective 2), IsOpen U ∧ C ⊆ U ∧
      ∀ C', IsProjectivePeriodicOrbit V C' → C' ⊆ U → C' = C

abbrev ProjectiveCycleClassification :=
  CycleClassification HomogeneousVectorField (Projective 2) ProjectiveArrangement
    (fun d V => V.Degree d) (fun V => {C | IsProjectiveLimitCycle V C})
    projectiveArrangementLocus

/-- The full chosen classification task is to supply this output, including
its actual catalogues and sharp maxima. A nonemptiness theorem alone is not
advertised as a completed classification. The primary surface class is
nonsingular; lower-dimensional singular loci remain an optional extension. -/
structure ClassificationOutput where
  curves : AlgebraicClassification 2 true
  surfaces : AlgebraicClassification 3 true
  affineCycles : AffineCycleClassification
  projectiveCycles : ProjectiveCycleClassification

/-- Optional strengthening: uniform terminating programs for the nonsingular
curve, nonsingular surface, affine-cycle and projective-cycle outputs.
Not the primary target; see ClassificationOutput. -/
def EffectiveHilbert16 : Prop :=
  Nonempty (EffectiveAlgebraicClassification 2 true) ∧
  Nonempty (EffectiveAlgebraicClassification 3 true) ∧
  Nonempty (EffectiveCycleClassification Hilbert16.PolynomialVectorField
    Hilbert16.Plane AffineArrangement (fun d V => V.DegreeAtMost d)
      (fun V => {C | Hilbert16.IsLimitCycle V C}) affineArrangementLocus) ∧
  Nonempty (EffectiveCycleClassification HomogeneousVectorField
    (Projective 2) ProjectiveArrangement (fun d V => V.Degree d)
      (fun V => {C | IsProjectiveLimitCycle V C}) projectiveArrangementLocus)

/-- The new affine specification entails the earlier bound question. -/
theorem effectiveHilbert16_implies_uniformBound (h : EffectiveHilbert16) :
    Hilbert16.UniformBound := by
  obtain ⟨classification⟩ := h.2.2.1
  intro d
  exact ⟨classification.maximum d, classification.bounded d⟩

/-- A degree is the actual homogeneous degree, not a permissive label. -/
theorem admissible_totalDegree {n d : ℕ} {smooth : Bool}
    {p : MvPolynomial (Fin (n + 1)) ℝ} (h : Admissible n d smooth p) :
    p.totalDegree = d := h.2.1.totalDegree h.1

/-- Excluding the zero polynomial prevents the whole ambient space from being
counted as a hypersurface of every possible degree. -/
theorem zero_not_admissible (n d : ℕ) (smooth : Bool) :
    ¬ Admissible n d smooth 0 := by
  intro h
  exact h.1 rfl

/-- The two smoothness modes cannot silently omit nonsingular examples. -/
theorem admissible_forget_smooth {n d : ℕ}
    {p : MvPolynomial (Fin (n + 1)) ℝ} (h : Admissible n d true p) :
    Admissible n d false p := by
  exact ⟨h.1, h.2.1, by simp⟩

/-- An empty cycle image cannot be made periodic by a choice of lift. -/
theorem projectivePeriodicOrbit_nonempty {V : HomogeneousVectorField}
    {C : Set (Projective 2)} (h : IsProjectivePeriodicOrbit V C) : C.Nonempty := by
  obtain ⟨γ, hγ, a, b, T, hT, ha, hb, hane, hd, hp, hn, rfl⟩ := h
  exact ⟨_, Set.mem_range_self 0⟩

/-- A maximum is required to be sharp; it is not an arbitrary upper bound. -/
theorem affine_maximum_attained (s : AffineCycleClassification) (d : ℕ) :
    ∃ V : Hilbert16.PolynomialVectorField, V.DegreeAtMost d ∧
      ∃ cs : Fin (s.maximum d) → Set Hilbert16.Plane,
        Function.Injective cs ∧
        ∀ C, Hilbert16.IsLimitCycle V C ↔ C ∈ Set.range cs := by
  obtain ⟨V, hV, cs, hi, hr⟩ := s.attained d
  exact ⟨V, hV, cs, hi, fun C => by rw [hr]; rfl⟩

/-- Ambient equivalence cannot collapse an infinite family to a finite one:
the induced map on sets is injective as well as the map on points. -/
theorem familyAmbientIsotopic_finite_iff {X : Type} [TopologicalSpace X]
    {A B : Set (Set X)} (h : FamilyAmbientIsotopic A B) : A.Finite ↔ B.Finite := by
  obtain ⟨H, hc, hci, hzero, hAB⟩ := h
  rw [← hAB]
  exact (Set.finite_image_iff
    (H ⟨1, by norm_num⟩).injective.image_injective.injOn).symm

/-- Exact component counts are unique, including the empty-locus case. -/
theorem exactComponents_unique {X : Type} [TopologicalSpace X]
    {S : Set X} {m n : ℕ} (hm : ExactComponents S m) (hn : ExactComponents S n) :
    m = n := by
  obtain ⟨f, hf, hfm⟩ := hm
  obtain ⟨g, hg, hgn⟩ := hn
  have hr : Set.range f = Set.range g := by
    ext C
    exact (hfm C).symm.trans (hgn C)
  have hc := congrArg Set.ncard hr
  simpa only [Set.ncard_range_of_injective hf, Set.ncard_range_of_injective hg,
    Nat.card_fin] using hc

/-- Counting zero components cannot silently conceal a nonempty locus. -/
theorem exactComponents_zero_iff {X : Type} [TopologicalSpace X] (S : Set X) :
    ExactComponents S 0 ↔ S = ∅ := by
  constructor
  · rintro ⟨f, hf, hcover⟩
    apply Set.eq_empty_iff_forall_notMem.mpr
    intro x hx
    obtain ⟨i, hi⟩ := (hcover (connectedComponentIn S x)).mp ⟨x, hx, rfl⟩
    exact Fin.elim0 i
  · intro hS
    subst S
    refine ⟨Fin.elim0, (fun i => Fin.elim0 i), ?_⟩
    intro C
    simp

/-- Every degree has at least the zero-field configuration to classify. Even
when there are no cycles, an empty catalogue is not an exhaustive answer. -/
theorem affine_catalogue_nonempty (s : AffineCycleClassification) (d : ℕ) :
    s.catalogue d ≠ [] := by
  have hz : (Hilbert16.PolynomialVectorField.mk 0 0).DegreeAtMost d := by
    simp [Hilbert16.PolynomialVectorField.DegreeAtMost]
  obtain ⟨c, hc, hiso⟩ := s.exhaustive d ⟨0, 0⟩ hz
  intro he
  simp [he] at hc

end
end HilbertDrafts.Hilbert16Classification


namespace HilbertDrafts.Hilbert16Classification

noncomputable section

/-- Evaluation scales by the actual homogeneous degree. -/
theorem homogeneous_eval_smul {n d : ℕ} {p : MvPolynomial (Fin (n + 1)) ℝ}
    (hp : p.IsHomogeneous d) (a : ℝ) (x : Vector n) :
    MvPolynomial.eval (a • x) p = a ^ d * MvPolynomial.eval x p := by
  classical
  simp only [MvPolynomial.eval_eq, Pi.smul_apply, smul_eq_mul, mul_pow,
    Finset.prod_mul_distrib, Finset.mul_sum]
  apply Finset.sum_congr rfl
  intro s hs
  rw [Finset.prod_pow_eq_pow_sum, ← hp.degree_eq_sum_deg_support hs]
  ring

/-- Projective zero-locus membership agrees with evaluation at any nonzero
representative; the existential representative cannot manufacture extra points. -/
theorem mem_zeroLocus_mk_iff {n d : ℕ} {p : MvPolynomial (Fin (n + 1)) ℝ}
    (hp : p.IsHomogeneous d) (x : Vector n) (hx : x ≠ 0) :
    Projectivization.mk ℝ x hx ∈ zeroLocus n p ↔ MvPolynomial.eval x p = 0 := by
  constructor
  · rintro ⟨y, hy, he, hz⟩
    obtain ⟨a, ha⟩ := (Projectivization.mk_eq_mk_iff ℝ y x hy hx).mp he
    have ha' : (a : ℝ) • x = y := ha
    rw [← ha', homogeneous_eval_smul hp] at hz
    exact (mul_eq_zero.mp hz).resolve_left (pow_ne_zero _ a.ne_zero)
  · intro h
    exact ⟨x, hx, rfl, h⟩

end
end HilbertDrafts.Hilbert16Classification
