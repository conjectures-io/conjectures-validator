import HilbertDrafts.Hilbert16

/-!
Hilbert 16: two specific open cases stated with the reviewed definitions of
`HilbertDrafts.Hilbert16` and `HilbertDrafts.Hilbert16Classification`. Each is
one case of one clause of the problem, not the whole problem.

* `QuadraticFourCycles`: every planar polynomial vector field of degree at most
  two has at most four limit cycles. Limit cycles are isolated periodic orbits,
  counted as point sets in the whole affine plane, without multiplicity. This is
  the upper half of the conjecture H(2) = 4 for the second part of the problem.
  The lower bound, a quadratic field with four limit cycles, is known in the
  literature and is not part of the statement. An existence statement for some
  finite bound, such as `Hilbert16.UniformBound`, does not decide it.
* `QuinticSurfaceComponents`: every nonsingular real projective quintic surface
  has at most twenty-three connected components. Hilbert asks about the number,
  form and position of the sheets of an algebraic surface and names the unknown
  maximum only for degree four, where it is now known to be ten. Degree five is
  the first degree in which the maximum is unknown: it is twenty-three,
  twenty-four or twenty-five. The statement may therefore be true or false. A
  quintic with twenty-three components is known in the literature and is not
  part of the statement.

Neither proposition is assumed or proved here. The lemmas are consistency
checks on the statements, not progress on the conjectures.
-/

namespace HilbertDrafts.Hilbert16Open

open HilbertDrafts.Hilbert16 HilbertDrafts.Hilbert16Classification

/-- Every polynomial vector field of degree at most `d` has at most `N` limit
cycles. Bounding every finite injective family also excludes infinitely many
cycles, exactly as in `Hilbert16.UniformBound`. -/
def CycleBound (d N : ℕ) : Prop :=
  ∀ V : PolynomialVectorField, V.DegreeAtMost d →
    ∀ (m : ℕ) (cycles : Fin m → Set Plane),
      Function.Injective cycles →
      (∀ i, IsLimitCycle V (cycles i)) → m ≤ N

/-- The quadratic case of the second part of Hilbert 16: at most four limit
cycles, in the whole affine plane and without multiplicity, for every vector
field of degree at most two. Only this upper bound is asserted. -/
def QuadraticFourCycles : Prop := CycleBound 2 4

/-- Every nonsingular real projective surface of degree `d` has at most `N`
connected components. The count is an exact enumeration of the components. -/
def SurfaceComponentBound (d N : ℕ) : Prop :=
  ∀ p : MvPolynomial (Fin 4) ℝ, Admissible 3 d true p →
    ∃ m ≤ N, ExactComponents (zeroLocus 3 p) m

/-- The quintic case of the question about the number of sheets in the first
part of Hilbert 16: at most twenty-three connected components for every real
projective quintic surface with nonsingular complexification. Only this upper
bound is asserted; whether it holds is not known. -/
def QuinticSurfaceComponents : Prop := SurfaceComponentBound 5 23

/-- The reviewed uniform-bound proposition is exactly the existence of some
bound in every degree. It gives no particular value in degree two. -/
theorem uniformBound_iff_exists_cycleBound :
    UniformBound ↔ ∀ d : ℕ, ∃ N : ℕ, CycleBound d N := Iff.rfl

/-- A larger number is a weaker bound. -/
theorem cycleBound_mono_right {d N M : ℕ} (h : CycleBound d N) (hNM : N ≤ M) :
    CycleBound d M :=
  fun V hV m cycles hi hc => (h V hV m cycles hi hc).trans hNM

/-- A bound for a degree covers every smaller degree. -/
theorem cycleBound_mono_left {d e N : ℕ} (h : CycleBound e N) (hde : d ≤ e) :
    CycleBound d N :=
  fun V hV => h V (degreeAtMost_mono hV hde)

/-- The quadratic bound really makes the set of limit cycles finite. -/
theorem quadraticFourCycles_finite (h : QuadraticFourCycles)
    (V : PolynomialVectorField) (hV : V.DegreeAtMost 2) :
    {C : Set Plane | IsLimitCycle V C}.Finite := by
  classical
  by_contra hinf
  let : Infinite {C : Set Plane // IsLimitCycle V C} := Set.Infinite.to_subtype hinf
  let f := Infinite.natEmbedding {C : Set Plane // IsLimitCycle V C}
  have hi : Function.Injective (fun i : Fin 5 => (f i).val) := by
    intro i j hij
    apply Fin.ext
    exact f.injective (Subtype.ext hij)
  have hbad := h V hV 5 (fun i => (f i).val) hi (fun i => (f i).property)
  omega

/-- A sharp classification with maximum four in degree two would give the
quadratic bound; the statement asks for no more than that upper bound. -/
theorem quadraticFourCycles_of_classification (s : AffineCycleClassification)
    (h : s.maximum 2 ≤ 4) : QuadraticFourCycles :=
  fun V hV m cycles hi hc => (s.bounded 2 V hV m cycles hi hc).trans h

/-- The known quartic answer has the same shape as the quintic statement. -/
theorem quarticSurfaceMaximum_bound (h : QuarticSurfaceMaximum) :
    SurfaceComponentBound 4 10 := h.1

/-- A larger number is a weaker bound. -/
theorem surfaceComponentBound_mono {d N M : ℕ} (h : SurfaceComponentBound d N)
    (hNM : N ≤ M) : SurfaceComponentBound d M := by
  intro p hp
  obtain ⟨m, hm, he⟩ := h p hp
  exact ⟨m, hm.trans hNM, he⟩

/-- A sharp surface classification with maximum twenty-three in degree five
would give the quintic bound. -/
theorem quinticSurfaceComponents_of_classification
    (s : AlgebraicClassification 3 true) (h : s.maximumComponents 5 ≤ 23) :
    QuinticSurfaceComponents := by
  intro p hp
  obtain ⟨m, hm, he⟩ := s.componentBound 5 p hp
  exact ⟨m, hm.trans h, he⟩

end HilbertDrafts.Hilbert16Open
