import FormalConjectures.Wikipedia.InverseGalois
import Mathlib

namespace Audit

theorem realization_finite_dimensional {G : Type*} [Fintype G] [Group G]
    (h : InverseGalois.GaloisRealization ℚ G) :
    letI := h.to_field
    letI := h.to_algebra
    FiniteDimensional ℚ h.L := by
  letI := h.to_field
  letI := h.to_algebra
  letI := h.to_isGalois
  letI : Finite (h.L ≃ₐ[ℚ] h.L) := Finite.of_equiv G h.iso.toEquiv
  exact IsGalois.finiteDimensional_of_finite ℚ h.L

theorem twin_prime_local_factor (q : ℝ) (h0 : q ≠ 0) (h1 : q ≠ 1) :
    (1 - 2 / q) / (1 - 1 / q)^2 = 1 - 1 / (q - 1)^2 := by
  have hm : q - 1 ≠ 0 := sub_ne_zero.mpr h1
  have hf : 1 - 1 / q ≠ 0 := by
    intro h
    have : q = 1 := by
      field_simp at h
      nlinarith
    exact h1 this
  field_simp
  <;> ring

#print axioms realization_finite_dimensional
#print axioms twin_prime_local_factor

end Audit
