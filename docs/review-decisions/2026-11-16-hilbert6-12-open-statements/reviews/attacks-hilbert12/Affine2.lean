import Review.Affine

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda NumberField AlgebraicGeometry CategoryTheory
open scoped NumberField
open Opposite

namespace Review

variable {K : Type} [Field K]

/-- The congruence clause of `IsFrobeniusLift` for an affine scheme: it follows
from the congruence on global functions, for ALL opens `U` and ALL affine opens
`V` inside `U` and its preimage. -/
theorem congruence_of_affine [NumberField K] (M : IntegralScheme K) [IsAffine M.X] (p : Prime K)
    (ψ : M.X ⟶ M.X)
    (hfrob : ∀ a : Γ(M.X, ⊤), ψ.appTop.hom a - a ^ Ideal.absNorm p.asIdeal ∈
      Ideal.map (M.scalar ⊤) p.asIdeal)
    (U V : M.X.Opens) (hU : V ≤ U) (hψ : V ≤ ψ ⁻¹ᵁ U) (hV : IsAffineOpen V) (s : Γ(M.X, U)) :
    (ψ.appLE U V hψ).hom s -
        (M.X.presheaf.map (homOfLE hU).op).hom s ^ Ideal.absNorm p.asIdeal ∈
      Ideal.map (M.scalar V) p.asIdeal := by
  apply mem_of_local hV
  intro x hxV
  by_cases hx : M.OverPrime p x
  swap
  · unfold IntegralScheme.OverPrime at hx
    push Not at hx
    obtain ⟨r, hr, hxr⟩ := hx
    refine ⟨M.scalar V r, ?_, Ideal.mul_mem_right _ _ (Ideal.mem_map_of_mem _ hr)⟩
    rw [← scalar_res M (le_top : V ≤ ⊤) r, Scheme.basicOpen_res]
    exact ⟨hxV, hxr⟩
  · obtain ⟨_, ⟨g, rfl⟩, hxg, hgV⟩ :=
      TopologicalSpace.Opens.isBasis_iff_nbhd.mp (isBasis_basicOpen M.X) hxV
    have hloc : IsLocalization.Away g Γ(M.X, M.X.basicOpen g) :=
      (isAffineOpen_top M.X).isLocalization_basicOpen g
    obtain ⟨⟨r, ⟨_, m, rfl⟩⟩, hr⟩ := IsLocalization.surj (Submonoid.powers g)
      ((M.X.presheaf.map (homOfLE (hgV.trans hU)).op).hom s)
    have hr' : (M.X.presheaf.map (homOfLE (hgV.trans hU)).op).hom s *
        (M.X.presheaf.map (homOfLE (le_top : M.X.basicOpen g ≤ ⊤)).op).hom (g ^ m) =
        (M.X.presheaf.map (homOfLE (le_top : M.X.basicOpen g ≤ ⊤)).op).hom r := hr
    clear hr
    -- the smaller neighbourhood
    have hxg' : x ∈ M.X.basicOpen (ψ.appTop.hom g) :=
      (mem_basicOpen_app_iff M p ψ hfrob x hx g).mpr hxg
    let h0 : Γ(M.X, V) :=
      (M.X.presheaf.map (homOfLE (le_top : V ≤ ⊤)).op).hom (g * ψ.appTop.hom g)
    have hW_eq : M.X.basicOpen h0 =
        V ⊓ (M.X.basicOpen g ⊓ M.X.basicOpen (ψ.appTop.hom g)) := by
      rw [Scheme.basicOpen_res, Scheme.basicOpen_mul]
    have hxW : x ∈ M.X.basicOpen h0 := by
      rw [hW_eq]; exact ⟨hxV, hxg, hxg'⟩
    have hWV : M.X.basicOpen h0 ≤ V := M.X.basicOpen_le h0
    have hWg : M.X.basicOpen h0 ≤ M.X.basicOpen g := by
      rw [hW_eq]; exact inf_le_right.trans inf_le_left
    have hWg' : M.X.basicOpen h0 ≤ ψ ⁻¹ᵁ M.X.basicOpen g := by
      rw [Scheme.preimage_basicOpen_top, hW_eq]; exact inf_le_right.trans inf_le_right
    -- restriction maps into the sections over the small neighbourhood
    let ρV : Γ(M.X, V) →+* Γ(M.X, M.X.basicOpen h0) :=
      (M.X.presheaf.map (homOfLE hWV).op).hom
    let ρT : Γ(M.X, ⊤) →+* Γ(M.X, M.X.basicOpen h0) :=
      (M.X.presheaf.map (homOfLE (le_top : M.X.basicOpen h0 ≤ ⊤)).op).hom
    -- the two key restrictions
    have F1 : ρV ((ψ.appLE U V hψ).hom s) =
        (ψ.appLE (M.X.basicOpen g) (M.X.basicOpen h0) hWg').hom
          ((M.X.presheaf.map (homOfLE (hgV.trans hU)).op).hom s) := by
      have e1 := Scheme.Hom.appLE_map ψ (U := U) (V := V) (V' := M.X.basicOpen h0) hψ
        (homOfLE hWV).op
      have e2 := Scheme.Hom.map_appLE ψ (U := M.X.basicOpen g) (U' := U)
        (V := M.X.basicOpen h0) hWg' (homOfLE (hgV.trans hU)).op
      exact (congrArg (fun φ => φ.hom s) e1).trans (congrArg (fun φ => φ.hom s) e2).symm
    have F2 : ∀ a : Γ(M.X, ⊤),
        (ψ.appLE (M.X.basicOpen g) (M.X.basicOpen h0) hWg').hom
          ((M.X.presheaf.map (homOfLE (le_top : M.X.basicOpen g ≤ ⊤)).op).hom a) =
        ρT (ψ.appTop.hom a) := by
      intro a
      have e3 := Scheme.Hom.map_appLE ψ (U := M.X.basicOpen g) (U' := ⊤)
        (V := M.X.basicOpen h0) hWg' (homOfLE (le_top : M.X.basicOpen g ≤ ⊤)).op
      exact congrArg (fun φ => φ.hom a) e3
    have F3 : (M.X.presheaf.map (homOfLE hWg).op).hom
          ((M.X.presheaf.map (homOfLE (hgV.trans hU)).op).hom s) =
        ρV ((M.X.presheaf.map (homOfLE hU).op).hom s) := by
      rw [res_res]
      exact (res_res hWV hU s).symm
    have F4 : ∀ a : Γ(M.X, ⊤), (M.X.presheaf.map (homOfLE hWg).op).hom
          ((M.X.presheaf.map (homOfLE (le_top : M.X.basicOpen g ≤ ⊤)).op).hom a) = ρT a :=
      fun a => res_res hWg le_top a
    have F5 : ∀ a : Γ(M.X, ⊤),
        ρV ((M.X.presheaf.map (homOfLE (le_top : V ≤ ⊤)).op).hom a) = ρT a :=
      fun a => res_res hWV le_top a
    set q := Ideal.absNorm p.asIdeal with hq
    -- the two relations in the small neighbourhood
    have hA : ρV ((ψ.appLE U V hψ).hom s) * ρT (ψ.appTop.hom g) ^ m = ρT (ψ.appTop.hom r) := by
      have := congrArg (ψ.appLE (M.X.basicOpen g) (M.X.basicOpen h0) hWg').hom hr'
      rw [map_mul, F2, F2, ← F1, map_pow, map_pow] at this
      exact this
    have hB : ρV ((M.X.presheaf.map (homOfLE hU).op).hom s) * ρT g ^ m = ρT r := by
      have := congrArg (M.X.presheaf.map (homOfLE hWg).op).hom hr'
      rw [map_mul, F4, F4, F3, map_pow] at this
      exact this
    -- the numerator
    let u : Γ(M.X, ⊤) := g ^ (q * m) * ψ.appTop.hom g ^ m
    let c : Γ(M.X, ⊤) := ψ.appTop.hom r * g ^ (q * m) - r ^ q * ψ.appTop.hom g ^ m
    have hc : c ∈ Ideal.map (M.scalar ⊤) p.asIdeal := by
      have h1 : ψ.appTop.hom g ^ m - (g ^ q) ^ m ∈ Ideal.map (M.scalar ⊤) p.asIdeal := by
        obtain ⟨k, hk⟩ := sub_dvd_pow_sub_pow (ψ.appTop.hom g) (g ^ q) m
        rw [hk]
        exact Ideal.mul_mem_right _ _ (hfrob g)
      have h2 : c = (ψ.appTop.hom r - r ^ q) * g ^ (q * m) -
          r ^ q * (ψ.appTop.hom g ^ m - (g ^ q) ^ m) := by
        simp only [c]; ring
      rw [h2]
      exact Ideal.sub_mem _ (Ideal.mul_mem_right _ _ (hfrob r)) (Ideal.mul_mem_left _ _ h1)
    have hkey : ρV (((ψ.appLE U V hψ).hom s -
        (M.X.presheaf.map (homOfLE hU).op).hom s ^ q) *
        (M.X.presheaf.map (homOfLE (le_top : V ≤ ⊤)).op).hom u) =
        ρV ((M.X.presheaf.map (homOfLE (le_top : V ≤ ⊤)).op).hom c) := by
      rw [map_mul, map_sub, map_pow, F5, F5]
      simp only [u, c, map_mul, map_sub, map_pow]
      have hBq : ρV ((M.X.presheaf.map (homOfLE hU).op).hom s) ^ q * ρT g ^ (q * m) =
          ρT r ^ q := by
        rw [← hB]; ring
      linear_combination (ρT g ^ (q * m)) * hA - (ρT (ψ.appTop.hom g) ^ m) * hBq
    have hloc2 : IsLocalization.Away h0 Γ(M.X, M.X.basicOpen h0) :=
      hV.isLocalization_basicOpen h0
    obtain ⟨⟨_, k, rfl⟩, hk⟩ :=
      (IsLocalization.eq_iff_exists (Submonoid.powers h0) Γ(M.X, M.X.basicOpen h0)).mp hkey
    refine ⟨h0 ^ k * (M.X.presheaf.map (homOfLE (le_top : V ≤ ⊤)).op).hom u, ?_, ?_⟩
    · rw [Scheme.mem_basicOpen _ _ _ hxV, map_mul, map_pow]
      refine IsUnit.mul (IsUnit.pow _ ((Scheme.mem_basicOpen _ _ _ hxV).mp hxW)) ?_
      have hxu : x ∈ M.X.basicOpen ((M.X.presheaf.map (homOfLE (le_top : V ≤ ⊤)).op).hom u) := by
        rw [Scheme.basicOpen_res]
        refine ⟨hxV, (Scheme.mem_basicOpen_top _ u x).mpr ?_⟩
        simp only [u, map_mul, map_pow]
        exact IsUnit.mul (IsUnit.pow _ ((Scheme.mem_basicOpen_top _ _ _).mp hxg))
          (IsUnit.pow _ ((Scheme.mem_basicOpen_top _ _ _).mp hxg'))
      exact (Scheme.mem_basicOpen _ _ _ hxV).mp hxu
    · have : h0 ^ k * (M.X.presheaf.map (homOfLE (le_top : V ≤ ⊤)).op).hom u *
          ((ψ.appLE U V hψ).hom s - (M.X.presheaf.map (homOfLE hU).op).hom s ^ q) =
          h0 ^ k * (M.X.presheaf.map (homOfLE (le_top : V ≤ ⊤)).op).hom c := by
        rw [← hk]; ring
      rw [this]
      exact Ideal.mul_mem_left _ _ (res_mem_map M p le_top hc)

end Review
