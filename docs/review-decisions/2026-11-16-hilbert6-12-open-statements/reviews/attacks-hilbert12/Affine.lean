import Review.Sanity1

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda NumberField AlgebraicGeometry CategoryTheory
open scoped NumberField
open Opposite

namespace Review

variable {K : Type} [Field K]

theorem res_res {X : Scheme.{0}} {A B C : X.Opens} (h1 : A ≤ B) (h2 : B ≤ C) (a : Γ(X, C)) :
    (X.presheaf.map (homOfLE h1).op).hom ((X.presheaf.map (homOfLE h2).op).hom a) =
      (X.presheaf.map (homOfLE (h1.trans h2)).op).hom a := by
  have e : (homOfLE (h1.trans h2)).op = (homOfLE h2).op ≫ (homOfLE h1).op := rfl
  rw [e, Functor.map_comp]
  rfl

/-- Restrictions of multiples of `p` are multiples of `p`. -/
theorem res_mem_map (M : IntegralScheme K) (p : Prime K) {V W : M.X.Opens} (h : V ≤ W)
    {a : Γ(M.X, W)} (ha : a ∈ Ideal.map (M.scalar W) p.asIdeal) :
    (M.X.presheaf.map (homOfLE h).op).hom a ∈ Ideal.map (M.scalar V) p.asIdeal := by
  have h1 := Ideal.mem_map_of_mem (M.X.presheaf.map (homOfLE h).op).hom ha
  rw [Ideal.map_map] at h1
  have h2 : (M.X.presheaf.map (homOfLE h).op).hom.comp (M.scalar W) = M.scalar V := by
    ext r; exact scalar_res M h r
  rwa [h2] at h1

/-- Multiples of `p` are not units at a point of the fibre over `p`. -/
theorem germ_mem_maximalIdeal (M : IntegralScheme K) (p : Prime K) (V : M.X.Opens)
    (x : M.X) (hx : x ∈ V) (hp : M.OverPrime p x) {a : Γ(M.X, V)}
    (ha : a ∈ Ideal.map (M.scalar V) p.asIdeal) :
    (M.X.presheaf.germ V x hx).hom a ∈ IsLocalRing.maximalIdeal (M.X.presheaf.stalk x) := by
  have hJ : Ideal.map (M.scalar V) p.asIdeal ≤
      Ideal.comap (M.X.presheaf.germ V x hx).hom (IsLocalRing.maximalIdeal _) := by
    rw [Ideal.map_le_iff_le_comap]
    intro r hr
    have h1 : x ∉ M.X.basicOpen (M.scalar V r) := by
      rw [← scalar_res M (le_top : V ≤ ⊤) r, Scheme.basicOpen_res]
      exact fun h => hp r hr h.2
    rw [Scheme.mem_basicOpen _ _ _ hx] at h1
    exact h1
  exact hJ ha

/-- Membership in an ideal of the sections over an affine open is a local question. -/
theorem mem_of_local {X : Scheme.{0}} {V : X.Opens} (hV : IsAffineOpen V) (I : Ideal Γ(X, V))
    (e : Γ(X, V)) (h : ∀ x ∈ V, ∃ h : Γ(X, V), x ∈ X.basicOpen h ∧ h * e ∈ I) : e ∈ I := by
  by_contra he
  let J : Ideal Γ(X, V) := Submodule.comap (LinearMap.mulRight Γ(X, V) e) I
  have hJ : ∀ a, a ∈ J ↔ a * e ∈ I := fun a => Iff.rfl
  have hne : J ≠ ⊤ := by
    intro htop
    have : (1 : Γ(X, V)) ∈ J := htop ▸ Submodule.mem_top
    rw [hJ, one_mul] at this
    exact he this
  obtain ⟨m, hm, hle⟩ := Ideal.exists_le_maximal _ hne
  let y : Spec Γ(X, V) := (⟨m, hm.isPrime⟩ : PrimeSpectrum Γ(X, V))
  have hxV : hV.fromSpec.base y ∈ V := by
    have : hV.fromSpec.base y ∈ Set.range hV.fromSpec.base := ⟨y, rfl⟩
    rw [hV.range_fromSpec] at this
    exact this
  obtain ⟨h0, hx0, hmem⟩ := h _ hxV
  have h2 : y ∈ hV.fromSpec ⁻¹ᵁ X.basicOpen h0 := hx0
  rw [hV.fromSpec_preimage_basicOpen] at h2
  exact h2 (hle ((hJ h0).mpr hmem))

/-- On an affine scheme, an endomorphism whose effect on global functions is
the `N(p)`-th power modulo `p` preserves the non-vanishing loci at points of the fibre. -/
theorem mem_basicOpen_app_iff [NumberField K] (M : IntegralScheme K) (p : Prime K)
    (ψ : M.X ⟶ M.X)
    (hfrob : ∀ a : Γ(M.X, ⊤), ψ.appTop.hom a - a ^ Ideal.absNorm p.asIdeal ∈
      Ideal.map (M.scalar ⊤) p.asIdeal)
    (x : M.X) (hx : M.OverPrime p x) (g : Γ(M.X, ⊤)) :
    x ∈ M.X.basicOpen (ψ.appTop.hom g) ↔ x ∈ M.X.basicOpen g := by
  have hq : Ideal.absNorm p.asIdeal ≠ 0 := by
    rw [Ne, Ideal.absNorm_eq_zero_iff]
    exact p.ne_bot
  have hd := germ_mem_maximalIdeal M p ⊤ x trivial hx (hfrob g)
  rw [map_sub, map_pow] at hd
  rw [Scheme.mem_basicOpen_top, Scheme.mem_basicOpen_top]
  set G := (M.X.presheaf.germ ⊤ x trivial).hom g with hG
  set G' := (M.X.presheaf.germ ⊤ x trivial).hom (ψ.appTop.hom g) with hG'
  change IsUnit G' ↔ IsUnit G
  have hprime : (IsLocalRing.maximalIdeal (M.X.presheaf.stalk x)).IsPrime := inferInstance
  constructor
  · intro hu
    by_contra hnu
    have h1 : G ∈ IsLocalRing.maximalIdeal (M.X.presheaf.stalk x) := hnu
    have h2 : G ^ Ideal.absNorm p.asIdeal ∈ IsLocalRing.maximalIdeal (M.X.presheaf.stalk x) :=
      Ideal.pow_mem_of_mem _ h1 _ (Nat.pos_of_ne_zero hq)
    have h3 : G' ∈ IsLocalRing.maximalIdeal (M.X.presheaf.stalk x) := by
      have := Ideal.add_mem _ hd h2
      simpa using this
    exact h3 hu
  · intro hu
    by_contra hnu
    have h1 : G' ∈ IsLocalRing.maximalIdeal (M.X.presheaf.stalk x) := hnu
    have h2 : G ^ Ideal.absNorm p.asIdeal ∈ IsLocalRing.maximalIdeal (M.X.presheaf.stalk x) := by
      have := Ideal.sub_mem _ h1 hd
      simpa using this
    exact (hprime.mem_of_pow_mem _ h2) hu

/-- The points clause of `IsFrobeniusLift` for an affine scheme. -/
theorem fixes_fibre_of_affine [NumberField K] (M : IntegralScheme K) [IsAffine M.X] (p : Prime K)
    (ψ : M.X ⟶ M.X)
    (hfrob : ∀ a : Γ(M.X, ⊤), ψ.appTop.hom a - a ^ Ideal.absNorm p.asIdeal ∈
      Ideal.map (M.scalar ⊤) p.asIdeal)
    (x : M.X) (hx : M.OverPrime p x) : ψ.base x = x := by
  apply Inseparable.eq
  rw [(isBasis_basicOpen M.X).inseparable_iff]
  rintro _ ⟨_, ⟨g, rfl⟩, rfl⟩
  have h1 : ψ.base x ∈ M.X.basicOpen g ↔ x ∈ ψ ⁻¹ᵁ M.X.basicOpen g := Iff.rfl
  have h2 := mem_basicOpen_app_iff M p ψ hfrob x hx g
  rw [Scheme.preimage_basicOpen_top] at h1
  exact h1.trans h2

end Review
