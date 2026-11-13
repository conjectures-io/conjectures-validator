import Mathlib

/-!
Hilbert 12: arithmetic classification and class-field theory specifications,
rational and imaginary-quadratic special-value benchmarks, free contracts for
proposed general constructions, and the rank-one abelian Stark conjecture.
Hilbert's original text supplies the scope; specialist sources supply the
precise mathematical interpretations. The free contracts do not certify
explicitness, and the Stark route is not identified with the entire problem.
-/

namespace HilbertDrafts.Hilbert12

noncomputable section

/- These algebraic contracts work over any field. Their intended application
is to number fields; no closed Hilbert-12 proposition is declared here. -/
variable (K : Type) [Field K]

abbrev Ambient := AlgebraicClosure K

/-- The extension is finite, normal, separable, and has commuting automorphisms. -/
def IsFiniteAbelian (E : IntermediateField K (Ambient K)) : Prop :=
  FiniteDimensional K E ∧ IsGalois K E ∧
    ∀ σ τ : E ≃ₐ[K] E, σ * τ = τ * σ

def IsAbelianElement (α : Ambient K) : Prop :=
  ∃ E : IntermediateField K (Ambient K), IsFiniteAbelian K E ∧ α ∈ E

/-- Fixed values must be abelian, and their generated field must contain every
finite abelian extension. Containment allows taking subfields, as is necessary
even for the cyclotomic description over the rationals. This contract alone
does not say that the values are analytic, explicit, or effectively computable. -/
def FixedValueCoverage (values : Set (Ambient K)) : Prop :=
  (∀ α ∈ values, IsAbelianElement K α) ∧
  ∀ E : IntermediateField K (Ambient K), IsFiniteAbelian K E →
    E ≤ IntermediateField.adjoin K values

/-- A deliberately too-permissive notion of analytic presentation. The missing
restriction is NOT replaced by an opaque admissibility axiom. -/
structure LooseAnalyticPresentation (ι : Ambient K →+* ℂ) where
  Parameter : Type
  function : Parameter → ℂ → ℂ
  argument : Parameter → ℂ
  value : Parameter → Ambient K
  analytic : ∀ p, Differentiable ℂ (function p)
  evaluation : ∀ p, function p (argument p) = ι (value p)

/-- A regression check, not a solution: naming all desired algebraic elements
as the allowed values makes the algebraic coverage contract automatic. -/
theorem unrestrictedValues_cover :
    FixedValueCoverage K {α | IsAbelianElement K α} := by
  refine ⟨fun α h => h, ?_⟩
  intro E hE α hα
  exact IntermediateField.subset_adjoin K _ ⟨E, hE, hα⟩

/-- Even requiring entire functions does not fix the problem: constants can
encode the unknown answers. A real Hilbert-12 candidate must exclude this
circular presentation by specifying its analytic family independently. -/
def constantOraclePresentation (ι : Ambient K →+* ℂ) :
    LooseAnalyticPresentation K ι where
  Parameter := {α : Ambient K // IsAbelianElement K α}
  function := fun p _ => ι p.val
  argument := fun _ => 0
  value := Subtype.val
  analytic := fun _ => differentiable_const _
  evaluation := fun _ => rfl

theorem constantOracle_covers (ι : Ambient K →+* ℂ) :
    FixedValueCoverage K (Set.range (constantOraclePresentation K ι).value) := by
  have hr : Set.range (constantOraclePresentation K ι).value =
      {α | IsAbelianElement K α} := by
    ext α
    constructor
    · rintro ⟨p, rfl⟩
      exact p.property
    · intro h
      exact ⟨⟨α, h⟩, rfl⟩
  rw [hr]
  exact unrestrictedValues_cover K

end
end HilbertDrafts.Hilbert12

namespace HilbertDrafts.Hilbert12Stark

open NumberField
open scoped NumberField Topology

noncomputable section

variable (K L : Type) [Field K] [NumberField K] [Field L] [NumberField L]
  [Algebra K L]

abbrev FinitePrime (K : Type) [Field K] := IsDedekindDomain.HeightOneSpectrum (𝓞 K)
abbrev Place (K : Type) [Field K] := FinitePrime K ⊕ InfinitePlace K
abbrev GaloisGroup := L ≃ₐ[K] L

/-- Product-formula normalization: N(p)^(-ord_p), |.| at real places,
and |.|^2 at complex places. -/
def placeAbs (w : Place L) (x : L) : ℝ :=
  match w with
  | .inl p => NumberField.HeightOneSpectrum.adicAbv L p x
  | .inr v => v x ^ v.mult

/-- Restriction of places, expressed without selecting a prime above a prime. -/
def LiesAbove (w : Place L) (v : Place K) : Prop :=
  match w, v with
  | .inl P, .inl p =>
      P.asIdeal.comap (RingOfIntegers.mapRingHom (algebraMap K L)) = p.asIdeal
  | .inr W, .inr V => W.comap (algebraMap K L) = V
  | _, _ => False

/-- For a finite Galois extension, trivial stabilizer of one place over v is
equivalent to complete splitting. -/
def SplitsAt (v : Place K) (w : Place L) : Prop :=
  LiesAbove K L w v ∧
    ∀ σ : GaloisGroup K L, (∀ x : L, placeAbs L w (σ x) = placeAbs L w x) → σ = 1

def AdmissiblePlaces (S : Finset (Place K)) (v : Place K) (w : Place L) : Prop :=
  2 ≤ S.card ∧ v ∈ S ∧
  (∀ V : InfinitePlace K, Sum.inr V ∈ S) ∧
  (∀ p : FinitePrime K, Sum.inl p ∉ S →
    ∀ P : FinitePrime L, LiesAbove K L (.inl P) (.inl p) →
      p.asIdeal.ramificationIdx' P.asIdeal = 1) ∧
  SplitsAt K L v w

/-- The arithmetic Frobenius convention is x ↦ x^(N p) modulo every prime
above p. Multiplicativity fixes Artin symbols on ideals coprime to S. -/
def IsArtinMap (S : Finset (Place K)) (A : Ideal (𝓞 K) → GaloisGroup K L) : Prop :=
  A ⊤ = 1 ∧
  (∀ I J, I ≠ ⊥ → J ≠ ⊥ → A (I * J) = A I * A J) ∧
  ∀ p : FinitePrime K, Sum.inl p ∉ S →
    ∀ P : FinitePrime L, LiesAbove K L (.inl P) (.inl p) → ∀ x : 𝓞 L,
      RingOfIntegers.mapRingHom (A p.asIdeal).toRingHom x -
        x ^ Ideal.absNorm p.asIdeal ∈ P.asIdeal

def CoprimeToS (S : Finset (Place K)) (I : Ideal (𝓞 K)) : Prop :=
  I ≠ ⊥ ∧ ∀ p : FinitePrime K, Sum.inl p ∈ S → ¬ I ≤ p.asIdeal

/-- The convergent partial-zeta series, used only on Re(s)>1. -/
def zetaTerm (S : Finset (Place K)) (A : Ideal (𝓞 K) → GaloisGroup K L)
    (σ : GaloisGroup K L) (s : ℂ) (I : Ideal (𝓞 K)) : ℂ := by
  classical
  exact if CoprimeToS K S I ∧ A I = σ then (Ideal.absNorm I : ℂ) ^ (-s) else 0

/-- Analytic continuation is specified by agreement with a convergent series
on an open half-plane and holomorphy on the connected domain C \ {1}.
Existence is a conclusion of the closed target, not an assumed oracle. -/
def IsPartialZeta (S : Finset (Place K)) (A : Ideal (𝓞 K) → GaloisGroup K L)
    (Z : GaloisGroup K L → ℂ → ℂ) : Prop :=
  ∀ σ, DifferentiableOn ℂ (Z σ) ({1}ᶜ : Set ℂ) ∧
    ∀ s : ℂ, 1 < s.re → HasSum (zetaTerm K L S A σ s) (Z σ s)

/-- The exact U_{v,S} condition, including the exceptional two-place case. -/
def StarkUnitCondition (S : Finset (Place K)) (v : Place K) (u : L) : Prop :=
  u ≠ 0 ∧
  (∀ W : Place L, (¬ ∃ V ∈ S, LiesAbove K L W V) → placeAbs L W u = 1) ∧
  (3 ≤ S.card → ∀ W : Place L, ¬ LiesAbove K L W v → placeAbs L W u = 1) ∧
  (S.card = 2 → ∀ V ∈ S, V ≠ v → ∀ W₁ W₂ : Place L,
    LiesAbove K L W₁ V → LiesAbove K L W₂ V → placeAbs L W₁ u = placeAbs L W₂ u)

/-- Adjoining an e_L-th root must still give a finite abelian extension of K;
the copy of L is explicitly included in the generated field. -/
def AbelianRootExtension (u : L) : Prop :=
  ∃ α : AlgebraicClosure L,
    α ^ NumberField.Units.torsionOrder L = algebraMap L (AlgebraicClosure L) u ∧
    let E := IntermediateField.adjoin K
      (Set.range (algebraMap L (AlgebraicClosure L)) ∪ {α})
    FiniteDimensional K E ∧ IsGalois K E ∧
      ∀ σ τ : E ≃ₐ[K] E, σ * τ = τ * σ

/-- A complete closed proposition for the rank-one abelian Stark conjecture.
Known Artin-map/continuation existence is included to prevent empty-interface
vacuity. This is a statement to prove, not a theorem assumed by this file. -/
def RankOneAbelianStark : Prop :=
  ∀ (K L : Type) [Field K] [NumberField K] [Field L] [NumberField L]
    [Algebra K L] [FiniteDimensional K L] [IsGalois K L],
    (∀ σ τ : L ≃ₐ[K] L, σ * τ = τ * σ) →
    ∀ (S : Finset (Place K)) (v : Place K) (w : Place L),
      AdmissiblePlaces K L S v w →
      (∃ A : Ideal (𝓞 K) → GaloisGroup K L, IsArtinMap K L S A) ∧
      ∀ A : Ideal (𝓞 K) → GaloisGroup K L, IsArtinMap K L S A →
        (∃ Z : GaloisGroup K L → ℂ → ℂ, IsPartialZeta K L S A Z) ∧
        ∀ Z : GaloisGroup K L → ℂ → ℂ, IsPartialZeta K L S A Z →
          ∃ u : L, StarkUnitCondition K L S v u ∧ AbelianRootExtension K L u ∧
            ∀ σ : GaloisGroup K L,
              HasDerivAt (Z σ)
                ((-(Real.log (placeAbs L w (σ u))) /
                  (NumberField.Units.torsionOrder L : ℝ) : ℝ) : ℂ) 0

/-- The logarithmic denominator cannot vanish in a number field. -/
theorem rootsOfUnity_order_pos : 0 < NumberField.Units.torsionOrder L :=
  NumberField.Units.torsionOrder_pos L

omit [NumberField L] in
/-- The divergent zero-ideal term is never included. -/
theorem zetaTerm_zeroIdeal (S : Finset (Place K))
    (A : Ideal (𝓞 K) → GaloisGroup K L) (σ : GaloisGroup K L) (s : ℂ) :
    zetaTerm K L S A σ s ⊥ = 0 := by
  simp [zetaTerm, CoprimeToS]

omit [NumberField L] in
/-- The analytic continuation is fixed by its defining half-plane series.
Its values near zero cannot be independently chosen to encode desired units. -/
theorem partialZeta_unique (S : Finset (Place K))
    (A : Ideal (𝓞 K) → GaloisGroup K L)
    (Z₁ Z₂ : GaloisGroup K L → ℂ → ℂ)
    (h₁ : IsPartialZeta K L S A Z₁) (h₂ : IsPartialZeta K L S A Z₂)
    (σ : GaloisGroup K L) : Set.EqOn (Z₁ σ) (Z₂ σ) ({1}ᶜ : Set ℂ) := by
  have hU : IsOpen ({1}ᶜ : Set ℂ) := isClosed_singleton.isOpen_compl
  apply ((h₁ σ).1.analyticOnNhd hU).eqOn_of_preconnected_of_eventuallyEq
    ((h₂ σ).1.analyticOnNhd hU)
    (isConnected_compl_singleton_of_one_lt_rank (by simp) (1 : ℂ)).isPreconnected
    (z₀ := (2 : ℂ)) (by norm_num)
  have hn : ∀ᶠ s : ℂ in 𝓝 (2 : ℂ), 1 < s.re :=
    (isOpen_lt continuous_const Complex.continuous_re).mem_nhds (by norm_num)
  filter_upwards [hn] with s hs
  exact ((h₁ σ).2 s hs).unique ((h₂ σ).2 s hs)

end
end HilbertDrafts.Hilbert12Stark

namespace HilbertDrafts.Hilbert12Universal

open HilbertDrafts.Hilbert12

noncomputable section

/-- A fixed, countably indexed analytic family in finitely many complex
variables. Domains are open and the functions are analytic on them. This is
input to the problem, not an existentially chosen list of desired answers. -/
structure AnalyticFamily where
  arity : ℕ → ℕ
  domain : (i : ℕ) → Set (Fin (arity i) → ℂ)
  function : (i : ℕ) → (Fin (arity i) → ℂ) → ℂ
  domain_open : ∀ i, IsOpen (domain i)
  analytic : ∀ i, AnalyticOnNhd ℂ (function i) (domain i)

/-- A countable list of evaluation instructions for a fixed analytic family.
No target extension, automorphism group, or desired generator is an argument. -/
structure Parameters (F : AnalyticFamily) where
  index : ℕ → ℕ
  argument : (n : ℕ) → Fin (F.arity (index n)) → ℂ
  in_domain : ∀ n, argument n ∈ F.domain (index n)

def Parameters.evaluate {F : AnalyticFamily} (p : Parameters F) (n : ℕ) : ℂ :=
  F.function (p.index n) (p.argument n)

/-- A proposed recipe must supply one analytic family and instructions from
only the base field and its complex realization. This structure does not by
itself certify that a recipe is explicit or historically appropriate. In
particular, arbitrary functions in a Lean structure can still hide an oracle. -/
structure Recipe where
  family : AnalyticFamily
  parameters : (K : Type) → [Field K] → [NumberField K] →
    (Ambient K →+* ℂ) → Parameters family

variable (K : Type) [Field K] (ι : Ambient K →+* ℂ)
  {F : AnalyticFamily} (p : Parameters F)

/-- Algebraic preimages of the actual evaluations. Desired algebraic values
are not supplied as an extra, freely chosen field of the recipe. -/
def specialValues : Set (Ambient K) :=
  {α | ∃ n : ℕ, p.evaluate n = ι α}

/-- A finite stage adjoins only the evaluations with indices in s. -/
def stage (s : Finset ℕ) : IntermediateField K (Ambient K) :=
  IntermediateField.adjoin K {α | ∃ n ∈ s, p.evaluate n = ι α}

/-- The maximal abelian extension as a subfield of the chosen algebraic
closure, characterized by all of its finite abelian subextensions. -/
def maximalAbelian : IntermediateField K (Ambient K) :=
  ⨆ E : IntermediateField K (Ambient K), ⨆ (_ : IsFiniteAbelian K E), E

/-- The algebraic and analytic evaluation obligations for a specified recipe:
all evaluations are algebraic; every finite stage is abelian; every finite
abelian extension occurs in a finite stage. The last clause is deliberately
containment, since subfields are essential already in Kronecker--Weber. -/
def GenerationContract : Prop :=
  (∀ n : ℕ, ∃ α : Ambient K, p.evaluate n = ι α) ∧
  (∀ s : Finset ℕ, IsFiniteAbelian K (stage K ι p s)) ∧
  ∀ E : IntermediateField K (Ambient K), IsFiniteAbelian K E →
    ∃ s : Finset ℕ, E ≤ stage K ι p s

/-- A source-derived generation contract for a FIXED proposed recipe. It ranges
over every number field and every embedding of its algebraic closure in C.
Existentially quantifying over Recipe alone would discard the essential
"explicit analogue" requirement and is intentionally not called Hilbert 12. -/
def Hilbert12For (R : Recipe) : Prop :=
  ∀ (K : Type) [Field K] [NumberField K] (ι : Ambient K →+* ℂ),
    GenerationContract K ι (R.parameters K ι)

/-- Monotonicity: allowing more evaluation instructions cannot remove values. -/
theorem stage_mono {s t : Finset ℕ} (h : s ⊆ t) :
    stage K ι p s ≤ stage K ι p t := by
  apply IntermediateField.adjoin.mono
  rintro α ⟨n, hn, he⟩
  exact ⟨n, h hn, he⟩

theorem stage_le_generated (s : Finset ℕ) :
    stage K ι p s ≤ IntermediateField.adjoin K (specialValues K ι p) := by
  apply IntermediateField.adjoin.mono
  rintro α ⟨n, _, he⟩
  exact ⟨n, he⟩

/-- Each evaluation has one algebraic preimage, since the complex embedding
is injective. This prevents unrelated algebraic labels for one special value. -/
theorem evaluation_unique (n : ℕ) {α β : Ambient K}
    (hα : p.evaluate n = ι α) (hβ : p.evaluate n = ι β) : α = β :=
  ι.injective (hα.symm.trans hβ)

/-- The universal contract implies the earlier coverage contract, with actual
analytic evaluations rather than a supplied list of algebraic generators. -/
theorem generation_implies_coverage (h : GenerationContract K ι p) :
    FixedValueCoverage K (specialValues K ι p) := by
  constructor
  · rintro α ⟨n, he⟩
    refine ⟨stage K ι p {n}, h.2.1 {n}, ?_⟩
    exact IntermediateField.subset_adjoin K _ ⟨n, Finset.mem_singleton_self n, he⟩
  · intro E hE
    obtain ⟨s, hs⟩ := h.2.2 E hE
    exact hs.trans (stage_le_generated K ι p s)

/-- A faithful recipe generates exactly K^ab, not a larger nonabelian field. -/
theorem generated_eq_maximalAbelian (h : GenerationContract K ι p) :
    IntermediateField.adjoin K (specialValues K ι p) = maximalAbelian K := by
  apply le_antisymm
  · apply IntermediateField.adjoin_le_iff.mpr
    intro α hα
    obtain ⟨E, hE, ha⟩ := (generation_implies_coverage K ι p h).1 α hα
    have hle : E ≤ maximalAbelian K := le_iSup_of_le E (le_iSup_of_le hE le_rfl)
    exact hle ha
  · apply iSup_le
    intro E
    apply iSup_le
    intro hE
    exact (generation_implies_coverage K ι p h).2 E hE

end
end HilbertDrafts.Hilbert12Universal

namespace HilbertDrafts.Hilbert12Universal

open HilbertDrafts.Hilbert12

noncomputable section

/-- The normalized exponential accepts rational arguments. Repeating it in
the index does not introduce arbitrary functions or select target extensions. -/
def exponentialFamily : AnalyticFamily where
  arity := fun _ => 1
  domain := fun _ => Set.univ
  function := fun _ z => Complex.exp (2 * (Real.pi : ℂ) * Complex.I * z 0)
  domain_open := fun _ => isOpen_univ
  analytic := fun _ z _ => by
    have h : AnalyticAt ℂ (fun w : Fin 1 → ℂ => w 0) z :=
      (ContinuousLinearMap.proj (0 : Fin 1) : (Fin 1 → ℂ) →L[ℂ] ℂ).analyticAt z
    exact (analyticAt_const.mul h).cexp

/-- The instructions are explicit: exp(2πi/(n+1)). -/
def cyclotomicParameters : Parameters exponentialFamily where
  index := fun _ => 0
  argument := fun n _ => 1 / ((n + 1 : ℕ) : ℂ)
  in_domain := fun _ => Set.mem_univ _

/-- A closed benchmark statement for the already known rational case.
It is a formulation, not a claim to have formalized a proof of Kronecker--Weber. -/
def RationalBenchmark : Prop :=
  ∀ ι : Ambient ℚ →+* ℂ, GenerationContract ℚ ι cyclotomicParameters

/-- Complex realizations exist for every base number field: universal
quantification over these embeddings is not an empty-interface loophole. -/
theorem complex_embedding_exists (K : Type) [Field K] [NumberField K] :
    Nonempty (Ambient K →+* ℂ) := by
  have : Algebra.IsAlgebraic ℚ (Ambient K) :=
    Algebra.IsAlgebraic.trans ℚ K (Ambient K)
  exact ⟨(IsAlgClosed.lift (R := ℚ) (S := Ambient K) (M := ℂ)).toRingHom⟩

/-- The benchmark really evaluates to primitive roots of the required order. -/
theorem cyclotomic_evaluation_primitive (n : ℕ) :
    IsPrimitiveRoot (cyclotomicParameters.evaluate n) (n + 1) := by
  simpa [Parameters.evaluate, cyclotomicParameters, exponentialFamily, div_eq_mul_inv] using
    Complex.isPrimitiveRoot_exp (n + 1) (Nat.succ_ne_zero n)

/-- The family and every argument of the displayed sequence are fixed over Q. -/
theorem cyclotomic_argument_rational (n : ℕ) :
    ∃ q : ℚ, ∀ i, cyclotomicParameters.argument n i = (q : ℂ) := by
  exact ⟨1 / ((n + 1 : ℕ) : ℚ), by simp [cyclotomicParameters]⟩

/-- All rational inputs, with no target-dependent selection rule. -/
def rationalValues (ι : Ambient ℚ →+* ℂ) : Set (Ambient ℚ) :=
  {α | ∃ q : ℚ, Complex.exp (2 * (Real.pi : ℂ) * Complex.I * q) = ι α}

/-- The all-rational-arguments version of the known benchmark. Every actual
evaluation must be algebraic and abelian, and their field covers all abelian
extensions. This is a theorem statement, not a proof of Kronecker--Weber. -/
def RationalAllArguments : Prop :=
  ∀ ι : Ambient ℚ →+* ℂ,
    (∀ q : ℚ, ∃ α : Ambient ℚ,
      Complex.exp (2 * (Real.pi : ℂ) * Complex.I * q) = ι α) ∧
    FixedValueCoverage ℚ (rationalValues ι)

end
end HilbertDrafts.Hilbert12Universal

namespace HilbertDrafts.Hilbert12Arithmetic

open HilbertDrafts.Hilbert12 NumberField
open scoped NumberField

noncomputable section

variable (K : Type) [Field K] [NumberField K]

/-- Relative discriminant via determinants of trace pairings of integral
n-tuples, n = [E:K]. Taking the ideal generated by all such determinants avoids
assuming that the ring of integers has a global relative integral basis.
Only finite extensions are used in the arithmetic specification below. -/
def relativeDiscriminant (E : IntermediateField K (Ambient K)) : Ideal (𝓞 K) :=
  Ideal.span {d : 𝓞 K | ∃ b : Fin (Module.finrank K E) → 𝓞 E,
    (d : K) = Matrix.det (fun i j => Algebra.trace K E ((b i : E) * (b j : E)))}

/-- The arithmetic fibre singled out in Hilbert's first clause: specified
relative degree, abelian automorphism group, and relative discriminant ideal. -/
def HasArithmeticData (E : IntermediateField K (Ambient K))
    (n : ℕ) (G : Type) [CommGroup G] [Finite G] (D : Ideal (𝓞 K)) : Prop :=
  IsFiniteAbelian K E ∧ Module.finrank K E = n ∧
    Nonempty ((E ≃ₐ[K] E) ≃* G) ∧ relativeDiscriminant K E = D

/-- Exact finite classification output for the prescribed arithmetic fibre.
This is an output contract. Merely asserting that such a finite set exists
is weaker than supplying an effective class-field construction. -/
structure ArithmeticCatalogue (n : ℕ) (G : Type) [CommGroup G] [Finite G]
    (D : Ideal (𝓞 K)) where
  entries : Finset (IntermediateField K (Ambient K))
  exact : ∀ E, E ∈ entries ↔ HasArithmeticData K E n G D

/-- The number and existence questions are both fixed by an exact catalogue. -/
def ArithmeticCatalogue.count {n : ℕ} {G : Type} [CommGroup G] [Finite G]
    {D : Ideal (𝓞 K)} (C : ArithmeticCatalogue K n G D) : ℕ := C.entries.card

omit [NumberField K] in
theorem ArithmeticCatalogue.exists_iff_count_pos {n : ℕ} {G : Type}
    [CommGroup G] [Finite G] {D : Ideal (𝓞 K)} (C : ArithmeticCatalogue K n G D) :
    (∃ E, HasArithmeticData K E n G D) ↔ 0 < C.count := by
  change (∃ E, HasArithmeticData K E n G D) ↔ 0 < C.entries.card
  rw [Finset.card_pos]
  constructor
  · rintro ⟨E, hE⟩
    exact ⟨E, (C.exact E).mpr hE⟩
  · rintro ⟨E, hE⟩
    exact ⟨E, (C.exact E).mp hE⟩

omit [NumberField K] in
/-- Exact catalogues cannot disagree on the answer, even though they may be
constructed by different methods. Intermediate fields are actual subfields
of the fixed algebraic closure, rather than arbitrary presentation labels. -/
theorem ArithmeticCatalogue.entries_unique {n : ℕ} {G : Type}
    [CommGroup G] [Finite G] {D : Ideal (𝓞 K)}
    (C₁ C₂ : ArithmeticCatalogue K n G D) : C₁.entries = C₂.entries := by
  ext E
  exact (C₁.exact E).trans (C₂.exact E).symm

end
end HilbertDrafts.Hilbert12Arithmetic

namespace HilbertDrafts.Hilbert12CM

open HilbertDrafts.Hilbert12
open scoped NumberField

noncomputable section

/-- Degree two with no real embedding. This concerns the base field alone. -/
def ImaginaryQuadratic (K : Type) [Field K] [NumberField K] : Prop :=
  Module.finrank ℚ K = 2 ∧ ∀ _ : K →+* ℝ, False

/-- The period lattice is exactly the image of the full ring of integers.
It is selected from the base field, independently of any target extension. -/
def FullIntegerLattice (K : Type) [Field K] [NumberField K]
    (ι : Ambient K →+* ℂ) (L : PeriodPair) : Prop :=
  (L.lattice : Set ℂ) =
    Set.range (fun x : 𝓞 K => ι (algebraMap K (Ambient K) (x : K)))

/-- The classical lattice discriminant, with the Weierstrass normalization
g₂=60G₄ and g₃=140G₆ already defined by convergent series in Mathlib. -/
def discriminant (L : PeriodPair) : ℂ := L.g₂ ^ 3 - 27 * L.g₃ ^ 2

def jInvariant (L : PeriodPair) : ℂ := 1728 * L.g₂ ^ 3 / discriminant L

/-- Silverman, Arithmetic of Elliptic Curves, second edition, C.11.5--11.6.
The two exceptional automorphism groups require powers two and three.
Only values away from the period lattice are used below. -/
def weber (L : PeriodPair) (z : ℂ) : ℂ := by
  classical
  exact if L.g₃ = 0 then L.g₂ ^ 2 / discriminant L * L.weierstrassP z ^ 2
    else if L.g₂ = 0 then L.g₃ / discriminant L * L.weierstrassP z ^ 3
    else L.g₂ * L.g₃ / discriminant L * L.weierstrassP z

/-- Nonidentity torsion classes in C/L, represented by complex numbers.
Excluding the lattice removes the pole at the identity; it cannot become a
spurious generator through Lean's totalized division at zero. -/
def NonzeroTorsion (L : PeriodPair) (z : ℂ) : Prop :=
  z ∉ L.lattice ∧ ∃ n : ℕ, 0 < n ∧ (n : ℂ) * z ∈ L.lattice

def complexValues (L : PeriodPair) : Set ℂ :=
  {jInvariant L} ∪ weber L '' {z | NonzeroTorsion L z}

def algebraicValues (K : Type) [Field K] (ι : Ambient K →+* ℂ)
    (L : PeriodPair) : Set (Ambient K) := ι ⁻¹' complexValues L

/-- Closed specification of the corrected imaginary-quadratic benchmark.
Lattice existence, a nonzero discriminant, and algebraicity of every actual
evaluation are conclusions, followed by generation of all finite abelian
extensions. This specifies the known CM theorem; it does not prove it here
or identify it with the general all-number-fields Hilbert-12 problem. -/
def ImaginaryQuadraticBenchmark : Prop :=
  ∀ (K : Type) [Field K] [NumberField K], ImaginaryQuadratic K →
    ∀ ι : Ambient K →+* ℂ,
      (∃ L : PeriodPair, FullIntegerLattice K ι L) ∧
      ∀ L : PeriodPair, FullIntegerLattice K ι L →
        discriminant L ≠ 0 ∧
        (∀ z ∈ complexValues L, ∃ α : Ambient K, ι α = z) ∧
        FixedValueCoverage K (algebraicValues K ι L)

/-- The chosen Weber function is analytic at every permitted evaluation. -/
theorem weber_analyticAt (L : PeriodPair) (z : ℂ) (hz : z ∉ L.lattice) :
    AnalyticAt ℂ (weber L) z := by
  have hp := L.analyticOnNhd_weierstrassP z hz
  unfold weber
  split_ifs <;> fun_prop

/-- Period translations give the same Weber value. -/
theorem weber_add_period (L : PeriodPair) (z : ℂ) (l : L.lattice) :
    weber L (z + l) = weber L z := by
  unfold weber
  rw [L.weierstrassP_add_coe]

/-- There is a permitted torsion evaluation for every genuine period pair. -/
theorem half_period_is_nonzero_torsion (L : PeriodPair) :
    NonzeroTorsion L (L.ω₁ / 2) := by
  refine ⟨L.ω₁_div_two_notMem_lattice, 2, by norm_num, ?_⟩
  convert L.ω₁_mem_lattice using 1
  push_cast
  ring

/-- The benchmark's coverage contract gives exactly K^ab. In particular it
cannot be satisfied by adjoining every algebraic number indiscriminately. -/
theorem benchmark_generated_eq_maximalAbelian (h : ImaginaryQuadraticBenchmark)
    (K : Type) [Field K] [NumberField K] (hK : ImaginaryQuadratic K)
    (ι : Ambient K →+* ℂ) (L : PeriodPair) (hL : FullIntegerLattice K ι L) :
    IntermediateField.adjoin K (algebraicValues K ι L) =
      Hilbert12Universal.maximalAbelian K := by
  have hc := ((h K hK ι).2 L hL).2.2
  apply le_antisymm
  · apply IntermediateField.adjoin_le_iff.mpr
    intro α hα
    obtain ⟨E, hE, ha⟩ := hc.1 α hα
    have hle : E ≤ Hilbert12Universal.maximalAbelian K :=
      le_iSup_of_le E (le_iSup_of_le hE le_rfl)
    exact hle ha
  · apply iSup_le
    intro E
    apply iSup_le
    intro hE
    exact hc.2 E hE

end
end HilbertDrafts.Hilbert12CM

namespace HilbertDrafts.Hilbert12Universal

noncomputable section

/-- A fixed, nonconstant entire function still does not prevent answer
encodings: arbitrary instructions can move every desired output into its input. -/
def identityFamily : AnalyticFamily where
  arity := fun _ => 1
  domain := fun _ => Set.univ
  function := fun _ z => z 0
  domain_open := fun _ => isOpen_univ
  analytic := fun _ z _ =>
    (ContinuousLinearMap.proj (0 : Fin 1) : (Fin 1 → ℂ) →L[ℂ] ℂ).analyticAt z

def identityParameters {F : AnalyticFamily} (p : Parameters F) :
    Parameters identityFamily where
  index := fun _ => 0
  argument := fun n _ => p.evaluate n
  in_domain := fun _ => Set.mem_univ _

def identityReencode (R : Recipe) : Recipe where
  family := identityFamily
  parameters := fun K _ _ ι => identityParameters (R.parameters K ι)

/-- Checked trivialisation of the unrestricted interface: replacing every
analytic function by the identity and putting its outputs in the arguments
preserves the entire contract. This is not an impossibility theorem about
every conceivable restriction on recipes. -/
theorem identity_reencoding_preserves_contract (R : Recipe) :
    Hilbert12For (identityReencode R) ↔ Hilbert12For R := Iff.rfl

end
end HilbertDrafts.Hilbert12Universal

namespace HilbertDrafts.Hilbert12Universal

open HilbertDrafts.Hilbert12

noncomputable section

/-- The actual embedded base field; equivalent abstract presentations give
identical inputs to a field-attached recipe. -/
def embeddedField (K : Type) [Field K] (ι : Ambient K →+* ℂ) : Subfield ℂ :=
  (ι.comp (algebraMap K (Ambient K))).fieldRange

/-- Field-attached analytic data. Unlike the older global-family interface,
this allows different analytic families for different embedded base fields.
It is an interface for proposed recipes, not a sufficient explicitness test. -/
structure FieldRecipe where
  family : Subfield ℂ → AnalyticFamily
  parameters : (F : Subfield ℂ) → Parameters (family F)

def FieldHilbert12For (R : FieldRecipe) : Prop :=
  ∀ (K : Type) [Field K] [NumberField K] (ι : Ambient K →+* ℂ),
    GenerationContract K ι (R.parameters (embeddedField K ι))

/-- All base-field points of the specified domain, with no selected subset. -/
def RationalPoints (F : Subfield ℂ) {a : ℕ} (D : Set (Fin a → ℂ)) : Set (Fin a → ℂ) :=
  {x | x ∈ D ∧ ∀ j, x j ∈ F}

/-- An optional profile matching RationalAllArguments. The analogous relation
to the CM benchmark is expected but not stated here. This is stronger than the
general source wording and still does not certify explicitness. -/
def RationalPointContract (K : Type) [Field K] (ι : Ambient K →+* ℂ)
    (F : AnalyticFamily) : Prop :=
  (∀ i, ∀ x ∈ RationalPoints (embeddedField K ι) (F.domain i),
    ∃ α : Ambient K, IsAbelianElement K α ∧ ι α = F.function i x) ∧
  ∀ E : IntermediateField K (Ambient K), IsFiniteAbelian K E →
    E ≤ IntermediateField.adjoin K
      {α | ∃ i, ∃ x ∈ RationalPoints (embeddedField K ι) (F.domain i),
        ι α = F.function i x}

end
end HilbertDrafts.Hilbert12Universal

namespace HilbertDrafts.Hilbert12ClassField

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Arithmetic NumberField
open scoped NumberField

noncomputable section

variable (K : Type) [Field K] [NumberField K]

abbrev Prime := IsDedekindDomain.HeightOneSpectrum (𝓞 K)
abbrev Divisor := Prime K →₀ ℤ

/-- A finite modulus and a set of real embeddings at which positivity is
required. Complex places do not occur in the infinite part of a modulus. -/
structure Modulus where
  exponent : Prime K →₀ ℕ
  realPlaces : Finset (K →+* ℝ)

def Modulus.Divides (m n : Modulus K) : Prop :=
  (∀ p, m.exponent p ≤ n.exponent p) ∧ m.realPlaces ⊆ n.realPlaces

/-- The group of fractional ideals coprime to the finite modulus, represented
canonically by their finite prime-exponent divisors. -/
def divisorsAway (m : Modulus K) : AddSubgroup (Divisor K) where
  carrier := {D | ∀ p, m.exponent p ≠ 0 → D p = 0}
  zero_mem' := by simp
  add_mem' := by
    intro D E hD hE p hp
    simp [hD p hp, hE p hp]
  neg_mem' := by
    intro D hD p hp
    simp [hD p hp]

/-- The principal divisor is pinned by all normalized finite-place absolute
values. Finite support is part of the divisor type, not an implicit oracle. -/
def IsPrincipalDivisor (x : K) (D : Divisor K) : Prop :=
  x ≠ 0 ∧ ∀ p : Prime K,
    NumberField.HeightOneSpectrum.adicAbv K p x =
      (Ideal.absNorm p.asIdeal : ℝ) ^ (-(D p))

def CongruentOne (m : Modulus K) (x : K) : Prop :=
  (∀ p : Prime K, m.exponent p ≠ 0 →
    NumberField.HeightOneSpectrum.adicAbv K p (x - 1) ≤
      (Ideal.absNorm p.asIdeal : ℝ) ^ (-(m.exponent p : ℤ))) ∧
  ∀ σ ∈ m.realPlaces, 0 < σ x

/-- Principal ray relations; additive subgroup closure is explicit. -/
def principalRay (m : Modulus K) : AddSubgroup (divisorsAway K m) :=
  AddSubgroup.closure {D | ∃ x : K, IsPrincipalDivisor K x D.val ∧ CongruentOne K m x}

abbrev RayClass (m : Modulus K) := divisorsAway K m ⧸ principalRay K m

def primeDivisor (m : Modulus K) (p : Prime K) (hp : m.exponent p = 0) :
    divisorsAway K m :=
  ⟨Finsupp.single p 1, by
    intro q hq
    by_cases h : p = q
    · subst q
      exact (hq hp).elim
    · simp [h]⟩

def primeClass (m : Modulus K) (p : Prime K) (hp : m.exponent p = 0) : RayClass K m :=
  QuotientAddGroup.mk' (principalRay K m) (primeDivisor K m p hp)

/-- Arithmetic Frobenius at an unramified prime, with the base-field norm. -/
def FrobeniusAt (E : IntermediateField K (Ambient K)) (p : Prime K)
    (σ : E ≃ₐ[K] E) : Prop :=
  ∀ P : IsDedekindDomain.HeightOneSpectrum (𝓞 E),
    P.asIdeal.comap (RingOfIntegers.mapRingHom (algebraMap K E)) = p.asIdeal →
      p.asIdeal.ramificationIdx' P.asIdeal = 1 ∧
      ∀ x : 𝓞 E, RingOfIntegers.mapRingHom σ.toRingHom x -
        x ^ Ideal.absNorm p.asIdeal ∈ P.asIdeal

/-- Real places absent from the modulus must remain real in the extension. -/
def UnramifiedAtRealPlaces (m : Modulus K) (E : IntermediateField K (Ambient K)) : Prop :=
  ∀ σ : K →+* ℝ, σ ∉ m.realPlaces → ∀ τ : E →+* ℂ,
    τ.comp (algebraMap K E) = Complex.ofRealHom.comp σ → ∀ x, (τ x).im = 0

/-- The class field of a subgroup is defined by a surjective Artin map with
that exact kernel, and its specified Frobenius values. No field is chosen
from an unexplained class-field oracle. -/
def IsClassField (m : Modulus K) (H : AddSubgroup (RayClass K m))
    (E : IntermediateField K (Ambient K)) : Prop :=
  IsFiniteAbelian K E ∧ UnramifiedAtRealPlaces K m E ∧
  ∃ A : RayClass K m →+ Additive (E ≃ₐ[K] E),
    Function.Surjective A ∧ A.ker = H ∧
    ∀ (p : Prime K) (hp : m.exponent p = 0),
      FrobeniusAt K E p (Additive.toMul (A (primeClass K m p hp)))

/-- Artin reciprocity and the existence theorem, including all finite abelian
extensions and the order-reversing subgroup/field correspondence. This is
known class field theory stated as an arithmetic obligation, not proved here. -/
def ClassFieldClassification : Prop :=
  ∀ (K : Type) [Field K] [NumberField K],
    (∀ m : Modulus K, Finite (RayClass K m)) ∧
    (∀ (m : Modulus K) (H : AddSubgroup (RayClass K m)),
      ∃! E : IntermediateField K (Ambient K), IsClassField K m H E) ∧
    (∀ E : IntermediateField K (Ambient K), IsFiniteAbelian K E →
      ∃ (m : Modulus K) (H : AddSubgroup (RayClass K m)), IsClassField K m H E) ∧
    ∀ (m : Modulus K) (H J : AddSubgroup (RayClass K m))
      (E F : IntermediateField K (Ambient K)),
      IsClassField K m H E → IsClassField K m J F → (H ≤ J ↔ F ≤ E)

/-- The least ray modulus for the specified extension, including real signs. -/
def IsConductor (E : IntermediateField K (Ambient K)) (m : Modulus K) : Prop :=
  (∃ H, IsClassField K m H E) ∧
    ∀ (n : Modulus K) (H : AddSubgroup (RayClass K n)),
      IsClassField K n H E → m.Divides K n

def finiteModulusIdeal (m : Modulus K) : Ideal (𝓞 K) :=
  m.exponent.support.prod (fun p => p.asIdeal ^ m.exponent p)

abbrev Character (E : IntermediateField K (Ambient K)) := (E ≃ₐ[K] E) →* ℂˣ

/-- The character field is the fixed field of its kernel, embedded back into
our common algebraic closure. Its conductor is the character conductor. -/
def characterField (E : IntermediateField K (Ambient K)) (χ : Character K E) :
    IntermediateField K (Ambient K) :=
  (IntermediateField.fixedField χ.ker).map E.val

/-- Existence of least conductors and the conductor-discriminant formula.
The finite list must contain EVERY character, preventing an empty-product
encoding of the discriminant. Infinite conductor factors are correctly
omitted from the ideal discriminant. -/
def ConductorDiscriminant : Prop :=
  ∀ (K : Type) [Field K] [NumberField K]
    (E : IntermediateField K (Ambient K)), IsFiniteAbelian K E →
    (∃! m : Modulus K, IsConductor K E m) ∧
    ∃ (characters : Finset (Character K E)) (conductors : Character K E → Modulus K),
      (∀ χ : Character K E, χ ∈ characters) ∧
      (∀ χ, IsConductor K (characterField K E χ) (conductors χ)) ∧
      relativeDiscriminant K E =
        ∏ χ ∈ characters, finiteModulusIdeal K (conductors χ)

end
end HilbertDrafts.Hilbert12ClassField

namespace HilbertDrafts.Hilbert12ClassField

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Arithmetic
open scoped NumberField

noncomputable section

/-- The arithmetic task asks for the actual exact catalogues, alongside the
reciprocity and conductor-discriminant theorems that organize them. It is an
output type; no algorithm or numerical classification is claimed supplied. -/
structure ArithmeticOutput where
  reciprocity : ClassFieldClassification
  conductor_discriminant : ConductorDiscriminant
  catalogue : (K : Type) → [Field K] → [NumberField K] →
    (n : ℕ) → (G : Type) → [CommGroup G] → [Finite G] →
    (D : Ideal (𝓞 K)) → ArithmeticCatalogue K n G D

/-- The explicit cyclotomic action, including its Frobenius normalization.
The generator is the actual preimage of exp(2πi/n); automorphisms act by
coprime powers. This is a classical theorem obligation. -/
def CyclotomicReciprocity : Prop :=
  ∀ (ι : Ambient ℚ →+* ℂ) (n : ℕ), 0 < n → ∀ ζ : Ambient ℚ,
    ι ζ = Complex.exp (2 * (Real.pi : ℂ) * Complex.I / (n : ℂ)) →
    let E := IntermediateField.adjoin ℚ ({ζ} : Set (Ambient ℚ))
    let z : E := ⟨ζ, IntermediateField.subset_adjoin ℚ _ (Set.mem_singleton ζ)⟩
    IsFiniteAbelian ℚ E ∧
    (∀ a : ℕ, Nat.Coprime a n → ∃! σ : E ≃ₐ[ℚ] E, (σ z : Ambient ℚ) = ζ ^ a) ∧
    (∀ σ : E ≃ₐ[ℚ] E, ∃ a : ℕ, a < n ∧ Nat.Coprime a n ∧
      (σ z : Ambient ℚ) = ζ ^ a) ∧
    ∀ (p : ℕ), p.Prime → ¬ p ∣ n → ∀ σ : E ≃ₐ[ℚ] E,
      (σ z : Ambient ℚ) = ζ ^ p → ∀ P : Prime ℚ,
        P.asIdeal = Ideal.span ({(p : 𝓞 ℚ)} : Set (𝓞 ℚ)) → FrobeniusAt ℚ E P σ

end
end HilbertDrafts.Hilbert12ClassField
