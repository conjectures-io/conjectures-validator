import HilbertDrafts.Hilbert12

/-!
Hilbert 12: a closed general statement using the explicitness criterion of
Borger and de Smit, "Explicit class field theory and the algebraic geometry of
Λ-rings" (arXiv:1809.02295, an unrefereed preprint). Their abstract describes
the framework as "allowing for some precise questions in the spirit of
Hilbert's 12th Problem". It is algebraic-geometric rather than Hilbert's
analytic formulation, and it is not the only modern reading of the problem.

A Λ-structure on a flat separated scheme of finite type over the ring of
integers of a number field is a family of commuting endomorphisms, one for each
prime, each reducing to the Frobenius map on the fibre over its prime. The
periodic points of such a scheme generate abelian extensions (their Theorem
8.4). The maximal abelian extension is called Λ-geometric when one such scheme
generates all of it (their Section 12.1). The multiplicative group with its
power maps does this for the rational numbers. The Lattès scheme of complex
multiplication does it for imaginary quadratic fields; that case rests on
Gurney's thesis (arXiv:1710.08674).

* `BorgerDeSmitQuestion4` is their Question 4: some number field other than the
  rational numbers and the imaginary quadratic fields has a Λ-geometric maximal
  abelian extension.
* `Hilbert12LambdaGeometric` asserts the property for every number field. This
  universal form is not posed in the paper; it is the reading of Hilbert's
  request for all number fields within their framework.

Finite type is the explicitness requirement: without it the ray class algebras
themselves would qualify. Neither statement is known in either direction, and
the authors state no expectation. One number field with no such scheme would
refute the universal form. Neither statement is assumed or proved here.
-/

namespace HilbertDrafts.Hilbert12Lambda

open HilbertDrafts.Hilbert12 NumberField AlgebraicGeometry CategoryTheory
open scoped NumberField

noncomputable section

variable (K : Type) [Field K] [NumberField K]

/-- The maximal ideals of the ring of integers. -/
abbrev Prime := IsDedekindDomain.HeightOneSpectrum (𝓞 K)

/-- The spectrum of the ring of integers. -/
abbrev Base : Scheme.{0} := Spec (CommRingCat.of (𝓞 K))

/-- A flat separated scheme of finite type over the ring of integers. Finite
type means locally of finite type and quasi-compact, so an infinite disjoint
union is excluded. -/
structure IntegralScheme where
  X : Scheme.{0}
  toBase : X ⟶ Base K
  flat : Flat toBase
  separated : IsSeparated toBase
  locallyOfFiniteType : LocallyOfFiniteType toBase
  quasiCompact : QuasiCompact toBase

variable {K}

/-- An integer, viewed as a regular function on an open subset. -/
def IntegralScheme.scalar (M : IntegralScheme K) (V : M.X.Opens) : 𝓞 K →+* Γ(M.X, V) :=
  (M.toBase.appLE ⊤ V le_top).hom.comp (Scheme.ΓSpecIso (CommRingCat.of (𝓞 K))).inv.hom

/-- The fibre over a prime: the points at which every element of the prime vanishes. -/
def IntegralScheme.OverPrime (M : IntegralScheme K) (p : Prime K) (x : M.X) : Prop :=
  ∀ r ∈ p.asIdeal, x ∉ M.X.basicOpen (M.scalar ⊤ r)

/-- A Frobenius lift at `p`: an endomorphism over the base whose restriction to
the fibre over `p` is the Frobenius map `s ↦ s ^ N(p)`. It fixes every point of
the fibre, and on every affine open `V` inside `U` and inside the preimage of
`U` it agrees with the `N(p)`-th power modulo `p`. Away from the fibre the
congruence is empty, because `p` generates the unit ideal there. -/
def IntegralScheme.IsFrobeniusLift (M : IntegralScheme K) (p : Prime K)
    (ψ : M.X ⟶ M.X) : Prop :=
  ψ ≫ M.toBase = M.toBase ∧
  (∀ x : M.X, M.OverPrime p x → ψ.base x = x) ∧
  ∀ (U V : M.X.Opens) (hU : V ≤ U) (hψ : V ≤ ψ ⁻¹ᵁ U), IsAffineOpen V →
    ∀ s : Γ(M.X, U),
      (ψ.appLE U V hψ).hom s -
          (M.X.presheaf.map (homOfLE hU).op).hom s ^ Ideal.absNorm p.asIdeal ∈
        Ideal.map (M.scalar V) p.asIdeal

/-- A Λ-structure: commuting Frobenius lifts at all primes. -/
structure LambdaStructure (M : IntegralScheme K) where
  psi : Prime K → (M.X ⟶ M.X)
  frobenius : ∀ p, M.IsFrobeniusLift p (psi p)
  commute : ∀ p q, psi p ≫ psi q = psi q ≫ psi p

/-- The operator of an ideal, written as a list of its prime factors. The
operators commute, so the order of the list does not matter. -/
def LambdaStructure.psiList {M : IntegralScheme K} (Λ : LambdaStructure M) :
    List (Prime K) → (M.X ⟶ M.X)
  | [] => 𝟙 M.X
  | p :: L => Λ.psi p ≫ Λ.psiList L

/-- The ideal with a given list of prime factors. The empty list gives the unit ideal. -/
def idealOfList (L : List (Prime K)) : Ideal (𝓞 K) := (L.map (·.asIdeal)).prod

variable (K) in
/-- A cycle: a nonzero ideal together with a set of real places. -/
structure Cycle where
  finite : Ideal (𝓞 K)
  finite_ne_bot : finite ≠ ⊥
  real : Finset (K →+* ℝ)

/-- Equivalence of ideals modulo a cycle, in the form of Proposition 4.3 of
Borger and de Smit: `a = x b` for some `x` in `1 + f b⁻¹` that is positive at
the real places of the cycle. The classes form the ray class monoid of Deligne
and Ribet. -/
def Cycle.Equivalent (f : Cycle K) (a b : Ideal (𝓞 K)) : Prop :=
  ∃ x : K, (∀ σ ∈ f.real, 0 < σ x) ∧
    (∀ α : K, (∃ a' ∈ a, (a' : K) = α) ↔ ∃ b' ∈ b, x * (b' : K) = α) ∧
    ∀ b' ∈ b, ∃ c ∈ f.finite, (c : K) = (x - 1) * (b' : K)

/-- A point with coordinates in the algebraic closure, lying over the generic point. -/
abbrev GeometricPoint (M : IntegralScheme K) :=
  {P : Spec (CommRingCat.of (Ambient K)) ⟶ M.X //
    P ≫ M.toBase = Spec.map (CommRingCat.ofHom
      ((algebraMap K (Ambient K)).comp (algebraMap (𝓞 K) K)))}

/-- A point is periodic for a cycle when equivalent ideals act on it equally. -/
def IsPeriodic {M : IntegralScheme K} (Λ : LambdaStructure M) (f : Cycle K)
    (P : GeometricPoint M) : Prop :=
  ∀ L₁ L₂ : List (Prime K), f.Equivalent (idealOfList L₁) (idealOfList L₂) →
    P.1 ≫ Λ.psiList L₁ = P.1 ≫ Λ.psiList L₂

/-- The value at a geometric point of a regular function defined near its image. -/
def value {M : IntegralScheme K} (P : GeometricPoint M) (U : M.X.Opens)
    (h : ⊤ ≤ P.1 ⁻¹ᵁ U) (s : Γ(M.X, U)) : Ambient K :=
  (Scheme.ΓSpecIso (CommRingCat.of (Ambient K))).hom.hom ((P.1.appLE U ⊤ h).hom s)

/-- The coordinates of the periodic points, for all cycles. They generate the
field written `K(X)` by Borger and de Smit. -/
def periodicValues {M : IntegralScheme K} (Λ : LambdaStructure M) : Set (Ambient K) :=
  {α | ∃ (f : Cycle K) (P : GeometricPoint M), IsPeriodic Λ f P ∧
    ∃ (U : M.X.Opens) (h : ⊤ ≤ P.1 ⁻¹ᵁ U) (s : Γ(M.X, U)), value P U h s = α}

variable (K) in
/-- The maximal abelian extension is Λ-geometric: one flat separated scheme of
finite type with a Λ-structure has periodic points whose coordinates are
abelian over the field and generate every finite abelian extension. By Theorem
8.4(2) and Proposition 7.3 of Borger and de Smit the coordinates of periodic
points are always abelian; the clause is kept so that the statement checks
itself, and it does not change the question. -/
def LambdaGeometric : Prop :=
  ∃ (M : IntegralScheme K) (Λ : LambdaStructure M), FixedValueCoverage K (periodicValues Λ)

/-- Hilbert 12 in the Λ-geometric form: for every number field, the maximal
abelian extension is generated by the periodic points of one flat separated
scheme of finite type with a Λ-structure over its ring of integers. This is the
universal form of Question 4 of Borger and de Smit. -/
def Hilbert12LambdaGeometric : Prop :=
  ∀ (K : Type) [Field K] [NumberField K], LambdaGeometric K

/-- Question 4 of Borger and de Smit: "Are there number fields other than Q and
imaginary quadratic fields for which the maximal abelian extension is
Λ-geometric?" The proposition is the affirmative answer. A number field of
degree one is the rational field, and an imaginary quadratic field has degree
two and no real embedding. -/
def BorgerDeSmitQuestion4 : Prop :=
  ∃ (K : Type) (_ : Field K) (_ : NumberField K),
    Module.finrank ℚ K ≠ 1 ∧ ¬ (Module.finrank ℚ K = 2 ∧ IsEmpty (K →+* ℝ)) ∧
      LambdaGeometric K

end

end HilbertDrafts.Hilbert12Lambda
