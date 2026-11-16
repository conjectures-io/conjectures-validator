import Review.Line

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda NumberField AlgebraicGeometry CategoryTheory
open scoped NumberField
open Opposite Polynomial

namespace Review

variable (K : Type) [Field K] [NumberField K]

/-- The structure map from the integers to the algebraic closure. -/
noncomputable def baseHom : 𝓞 K →+* Ambient K :=
  (algebraMap K (Ambient K)).comp (algebraMap (𝓞 K) K)

/-- The geometric point `t = z` of the toric line. -/
noncomputable def linePoint (z : Ambient K) : GeometricPoint (specScheme K (𝓞 K)[X]) :=
  ⟨Spec.map (CommRingCat.ofHom (eval₂RingHom (baseHom K) z)), by
    change Spec.map _ ≫ Spec.map _ = Spec.map _
    rw [← Spec.map_comp]
    congr 1
    refine CommRingCat.hom_ext (RingHom.ext fun r => ?_)
    change eval₂ (baseHom K) z (algebraMap (𝓞 K) (𝓞 K)[X] r) = baseHom K r
    rw [← C_eq_algebraMap, eval₂_C]⟩

variable {K}

theorem linePoint_psi (p : Prime K) (z : Ambient K) :
    (linePoint K z).1 ≫ (toricLine K).psi p =
      (linePoint K (z ^ Ideal.absNorm p.asIdeal)).1 := by
  change Spec.map _ ≫ Spec.map _ = Spec.map _
  rw [← Spec.map_comp]
  congr 1
  refine CommRingCat.hom_ext (Polynomial.ringHom_ext (fun r => ?_) ?_)
  · change eval₂ (baseHom K) z (expand (𝓞 K) _ (C r)) = eval₂ (baseHom K) _ (C r)
    rw [expand_C, eval₂_C, eval₂_C]
  · change eval₂ (baseHom K) z (expand (𝓞 K) _ X) = eval₂ (baseHom K) _ X
    rw [expand_X, eval₂_pow, eval₂_X, eval₂_X]

/-- The operator of an ideal acts on the point `z` as `z ↦ z ^ N(a)`. -/
theorem linePoint_psiList (L : List (Prime K)) (z : Ambient K) :
    (linePoint K z).1 ≫ (toricLine K).psiList L =
      (linePoint K (z ^ Ideal.absNorm (idealOfList L))).1 := by
  induction L generalizing z with
  | nil =>
    have h : Ideal.absNorm (idealOfList ([] : List (Prime K))) = 1 := by
      simp [idealOfList]
    rw [h, pow_one]
    exact Category.comp_id _
  | cons p L ih =>
    have h : Ideal.absNorm (idealOfList (p :: L)) =
        Ideal.absNorm p.asIdeal * Ideal.absNorm (idealOfList L) := by
      simp [idealOfList]
    change (linePoint K z).1 ≫ ((toricLine K).psi p ≫ (toricLine K).psiList L) = _
    rw [← Category.assoc, linePoint_psi, ih, h, pow_mul]

/-- The coordinate function takes the value `z` at the point `z`. -/
theorem linePoint_value (z : Ambient K) :
    value (linePoint K z) ⊤ le_top
      ((Scheme.ΓSpecIso (CommRingCat.of (𝓞 K)[X])).inv.hom X) = z := by
  have := value_affine (CommRingCat.of (𝓞 K)[X]) (CommRingCat.of (Ambient K))
    (CommRingCat.ofHom (eval₂RingHom (baseHom K) z)) X
  refine this.trans ?_
  change eval₂ (baseHom K) z X = z
  exact eval₂_X _ _

/-- Periodicity of a point of the toric line is the expected arithmetic condition. -/
theorem linePoint_periodic (f : HilbertDrafts.Hilbert12Lambda.Cycle K) (z : Ambient K)
    (h : ∀ L₁ L₂ : List (Prime K), f.Equivalent (idealOfList L₁) (idealOfList L₂) →
      z ^ Ideal.absNorm (idealOfList L₁) = z ^ Ideal.absNorm (idealOfList L₂)) :
    IsPeriodic (toricLine K) f (linePoint K z) := by
  intro L₁ L₂ he
  rw [linePoint_psiList, linePoint_psiList, h L₁ L₂ he]

/-- Conversely a periodic point satisfies the arithmetic condition. -/
theorem linePoint_periodic_iff (f : HilbertDrafts.Hilbert12Lambda.Cycle K) (z : Ambient K) :
    IsPeriodic (toricLine K) f (linePoint K z) ↔
      ∀ L₁ L₂ : List (Prime K), f.Equivalent (idealOfList L₁) (idealOfList L₂) →
        z ^ Ideal.absNorm (idealOfList L₁) = z ^ Ideal.absNorm (idealOfList L₂) := by
  refine ⟨fun hP L₁ L₂ he => ?_, linePoint_periodic f z⟩
  have h1 := hP L₁ L₂ he
  rw [linePoint_psiList, linePoint_psiList] at h1
  have h2 := linePoint_value (K := K) (z ^ Ideal.absNorm (idealOfList L₁))
  have h3 := linePoint_value (K := K) (z ^ Ideal.absNorm (idealOfList L₂))
  rw [← h2, ← h3]
  have h4 : linePoint K (z ^ Ideal.absNorm (idealOfList L₁)) =
      linePoint K (z ^ Ideal.absNorm (idealOfList L₂)) := Subtype.ext h1
  rw [h4]

/-- The points `0` and `1` are periodic for every cycle; their values are rational. -/
theorem one_periodic (f : HilbertDrafts.Hilbert12Lambda.Cycle K) :
    IsPeriodic (toricLine K) f (linePoint K 1) :=
  linePoint_periodic f 1 (fun _ _ _ => by simp)

#print axioms linePoint_periodic_iff

end Review
