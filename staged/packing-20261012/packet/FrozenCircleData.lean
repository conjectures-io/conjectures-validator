import Mathlib

namespace Candidate11.Frozen

/- This tuple is defined by a literal six-polynomial system and a certified
rational isolating box, not by an optimal-packing predicate or solver-supplied data.
Exact rational Krawczyk and geometry certificates are checked by verify_circle16.py.
The root-existence and construction certificates have not yet been replayed in Lean. -/
def goldbergCenter : Fin 6 → ℚ := ![(69148152393043066237485871276097278487902187 / 250000000000000000000000000000000000000000000 : ℚ),
  (66450494022049122111374581444199117881137433 / 125000000000000000000000000000000000000000000 : ℚ),
  (-14243710377103965127923359591426959795197077 / 40000000000000000000000000000000000000000000 : ℚ),
  (444034376185274659410524874727858410792674553 / 1000000000000000000000000000000000000000000000 : ℚ),
  (171142616351814814266493634923544798954913927 / 1000000000000000000000000000000000000000000000 : ℚ),
  (501062848295129363996210674277230159471802987 / 1000000000000000000000000000000000000000000000 : ℚ)]
def goldbergHalfWidth : ℚ := (1 / 10000000000000000000000000 : ℚ)
def goldbergBox (z : Fin 6 → ℝ) : Prop := ∀ i,
  ((goldbergCenter i-goldbergHalfWidth : ℚ):ℝ) ≤ z i ∧
  z i ≤ ((goldbergCenter i+goldbergHalfWidth : ℚ):ℝ)
def goldbergSystem (z : Fin 6 → ℝ) : Prop :=
  let h := z 0; let s := z 1; let u := z 2
  let v := z 3; let w := z 4; let t := z 5
  let c := 1-2*h^2
  let S3 := s*(4*c^2-1); let C3 := 4*c^3-3*c
  let S5 := s*(16*c^4-12*c^2+1); let C5 := 16*c^5-20*c^3+5*c
  s^2+c^2=1 ∧
  (s-h)^2+(u+c)^2=4*h^2 ∧
  (v-h)^2+(w-u)^2=4*h^2 ∧
  (v-S3)^2+(w+C3)^2=4*h^2 ∧
  v^2+(t-w)^2=4*h^2 ∧
  S5^2+(t+C5)^2=4*h^2
noncomputable def goldbergRoot : Fin 6 → ℝ :=
  Classical.epsilon fun z => goldbergBox z ∧ goldbergSystem z
noncomputable def goldbergRotate (p : ℝ × ℝ) : ℝ × ℝ :=
  let c := 1-2*(goldbergRoot 0)^2
  let s := goldbergRoot 1
  (c*p.1-s*p.2,s*p.1+c*p.2)
noncomputable def goldbergOuter (k : ℕ) : ℝ × ℝ := goldbergRotate^[k] (0,-1)
def reflectX (p : ℝ × ℝ) : ℝ × ℝ := (-p.1,p.2)
noncomputable def goldbergRaw : Fin 16 → ℝ × ℝ := ![
  (0,-1),goldbergOuter 1,reflectX (goldbergOuter 1),
  goldbergOuter 2,reflectX (goldbergOuter 2),
  goldbergOuter 3,reflectX (goldbergOuter 3),
  goldbergOuter 4,reflectX (goldbergOuter 4),
  goldbergOuter 5,reflectX (goldbergOuter 5),
  (goldbergRoot 0,goldbergRoot 2),(-goldbergRoot 0,goldbergRoot 2),
  (goldbergRoot 3,goldbergRoot 4),(-goldbergRoot 3,goldbergRoot 4),
  (0,goldbergRoot 5)]

end Candidate11.Frozen
