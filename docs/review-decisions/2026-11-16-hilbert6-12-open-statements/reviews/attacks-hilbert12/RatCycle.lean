import HilbertDrafts.Hilbert12Lambda

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda NumberField
open scoped NumberField

namespace Review

theorem exists_int (z : 𝓞 ℚ) : ∃ j : ℤ, algebraMap (𝓞 ℚ) ℚ z = j :=
  ⟨Rat.ringOfIntegersEquiv z, (Rat.ringOfIntegersEquiv_apply_coe z).symm⟩

theorem mem_span_iff (k : ℕ) (a' : 𝓞 ℚ) :
    a' ∈ Ideal.span {(k : 𝓞 ℚ)} ↔ ∃ j : ℤ, algebraMap (𝓞 ℚ) ℚ a' = k * j := by
  rw [Ideal.mem_span_singleton']
  constructor
  · rintro ⟨m, rfl⟩
    obtain ⟨j, hj⟩ := exists_int m
    exact ⟨j, by rw [map_mul, map_natCast, hj]; ring⟩
  · rintro ⟨j, hj⟩
    refine ⟨(j : 𝓞 ℚ), ?_⟩
    apply RingOfIntegers.coe_injective
    rw [hj, map_mul, map_natCast, map_intCast]; ring

/-- For the rationals, with modulus `n` and the real place: `k ≡ l mod n` gives `(k) ∼ (l)`. -/
theorem equivalent_of_modEq (f : HilbertDrafts.Hilbert12Lambda.Cycle ℚ) (n k l : ℕ)
    (hl : l ≠ 0) (hk : k ≠ 0)
    (hf : f.finite = Ideal.span {(n : 𝓞 ℚ)}) (h : (k : ℤ) ≡ l [ZMOD n]) :
    f.Equivalent (Ideal.span {(k : 𝓞 ℚ)}) (Ideal.span {(l : 𝓞 ℚ)}) := by
  have hl' : (l : ℚ) ≠ 0 := by exact_mod_cast hl
  have hk' : (k : ℚ) ≠ 0 := by exact_mod_cast hk
  refine ⟨(k : ℚ) / l, fun σ _ => ?_, fun α => ?_, fun b' hb' => ?_⟩
  · rw [map_div₀, map_natCast, map_natCast]; positivity
  · constructor
    · rintro ⟨a', ha', rfl⟩
      obtain ⟨j, hj⟩ := (mem_span_iff k a').mp ha'
      refine ⟨(l : 𝓞 ℚ) * (j : 𝓞 ℚ), (mem_span_iff l _).mpr ⟨j, by
        rw [map_mul, map_natCast, map_intCast]⟩, ?_⟩
      change (k : ℚ) / l * algebraMap (𝓞 ℚ) ℚ ((l : 𝓞 ℚ) * (j : 𝓞 ℚ)) = algebraMap (𝓞 ℚ) ℚ a'
      rw [hj, map_mul, map_natCast, map_intCast]; field_simp
    · rintro ⟨b', hb', rfl⟩
      obtain ⟨j, hj⟩ := (mem_span_iff l b').mp hb'
      refine ⟨(k : 𝓞 ℚ) * (j : 𝓞 ℚ), (mem_span_iff k _).mpr ⟨j, by
        rw [map_mul, map_natCast, map_intCast]⟩, ?_⟩
      change algebraMap (𝓞 ℚ) ℚ ((k : 𝓞 ℚ) * (j : 𝓞 ℚ)) = (k : ℚ) / l * algebraMap (𝓞 ℚ) ℚ b'
      rw [hj, map_mul, map_natCast, map_intCast]; field_simp
  · obtain ⟨j, hj⟩ := (mem_span_iff l b').mp hb'
    obtain ⟨t, ht⟩ := h.symm.dvd
    have ht' : ((k : ℚ) - l) = n * t := by exact_mod_cast ht
    refine ⟨(n : 𝓞 ℚ) * ((t * j : ℤ) : 𝓞 ℚ), ?_, ?_⟩
    · rw [hf]
      exact (mem_span_iff n _).mpr ⟨t * j, by rw [map_mul, map_natCast, map_intCast]⟩
    · change algebraMap (𝓞 ℚ) ℚ ((n : 𝓞 ℚ) * ((t * j : ℤ) : 𝓞 ℚ)) =
        ((k : ℚ) / l - 1) * algebraMap (𝓞 ℚ) ℚ b'
      rw [hj, map_mul, map_natCast, map_intCast]
      push_cast
      have : ((k : ℚ) / l - 1) * (l * j) = ((k : ℚ) - l) * j := by field_simp
      rw [this, ht']; ring

/-- Conversely, with the real place in the cycle, `(k) ∼ (l)` forces `k ≡ l mod n`. -/
theorem modEq_of_equivalent (f : HilbertDrafts.Hilbert12Lambda.Cycle ℚ) (n k l : ℕ)
    (hl : l ≠ 0) (hk : k ≠ 0)
    (hf : f.finite = Ideal.span {(n : 𝓞 ℚ)}) (hσ : algebraMap ℚ ℝ ∈ f.real)
    (h : f.Equivalent (Ideal.span {(k : 𝓞 ℚ)}) (Ideal.span {(l : 𝓞 ℚ)})) :
    (k : ℤ) ≡ l [ZMOD n] := by
  have hl' : (0 : ℚ) < l := by exact_mod_cast Nat.pos_of_ne_zero hl
  have hk' : (0 : ℚ) < k := by exact_mod_cast Nat.pos_of_ne_zero hk
  obtain ⟨x, hpos, hset, hfin⟩ := h
  have hx : 0 < x := by
    have := hpos _ hσ
    rw [eq_ratCast] at this
    exact_mod_cast this
  obtain ⟨b', hb', hb'k⟩ := (hset k).mp ⟨(k : 𝓞 ℚ), Ideal.mem_span_singleton_self _,
    map_natCast (algebraMap (𝓞 ℚ) ℚ) k⟩
  obtain ⟨j, hj⟩ := (mem_span_iff l b').mp hb'
  obtain ⟨a', ha', ha'l⟩ := (hset (x * l)).mpr ⟨(l : 𝓞 ℚ), Ideal.mem_span_singleton_self _,
    congrArg (x * ·) (map_natCast (algebraMap (𝓞 ℚ) ℚ) l)⟩
  obtain ⟨i, hi⟩ := (mem_span_iff k a').mp ha'
  have hb'k' : x * ((l : ℚ) * j) = k := by rw [← hj]; exact hb'k
  have ha'l' : (k : ℚ) * i = x * l := by rw [← hi]; exact ha'l
  have hij : i * j = 1 := by
    have h1 : (k : ℚ) * (i * j) = k * 1 := by
      calc (k : ℚ) * (i * j) = (k * i) * j := by ring
        _ = (x * l) * j := by rw [ha'l']
        _ = x * (l * j) := by ring
        _ = k * 1 := by rw [hb'k', mul_one]
    have h2 := mul_left_cancel₀ hk'.ne' h1
    exact_mod_cast h2
  have hi_pos : 0 < i := by
    have h1 : (0 : ℚ) < k * i := by rw [ha'l']; positivity
    have h2 : (0 : ℚ) < i := (mul_pos_iff_of_pos_left hk').mp h1
    exact_mod_cast h2
  have hi1 : i = 1 := Int.eq_one_of_mul_eq_one_right hi_pos.le hij
  have hxl : x * l = k := by rw [← ha'l', hi1]; simp
  obtain ⟨c, hc, hcl⟩ := hfin (l : 𝓞 ℚ) (Ideal.mem_span_singleton_self _)
  rw [hf] at hc
  obtain ⟨t, ht⟩ := (mem_span_iff n c).mp hc
  have hcl' : algebraMap (𝓞 ℚ) ℚ c = (x - 1) * l := by
    rw [← map_natCast (algebraMap (𝓞 ℚ) ℚ) l]; exact hcl
  have h3 : ((l : ℚ) - k) = n * (-t : ℤ) := by
    have : (n : ℚ) * t = (x - 1) * l := by rw [← ht, hcl']
    push_cast
    linarith
  rw [Int.modEq_iff_dvd]
  exact ⟨-t, by exact_mod_cast h3⟩

/-- Without the real place the relation is coarser: `k ≡ -l mod n` also gives `(k) ∼ (l)`. -/
theorem equivalent_of_modEq_neg (f : HilbertDrafts.Hilbert12Lambda.Cycle ℚ) (n k l : ℕ)
    (hl : l ≠ 0) (hk : k ≠ 0)
    (hf : f.finite = Ideal.span {(n : 𝓞 ℚ)}) (hreal : f.real = ∅)
    (h : (k : ℤ) ≡ -l [ZMOD n]) :
    f.Equivalent (Ideal.span {(k : 𝓞 ℚ)}) (Ideal.span {(l : 𝓞 ℚ)}) := by
  have hl' : (l : ℚ) ≠ 0 := by exact_mod_cast hl
  have hk' : (k : ℚ) ≠ 0 := by exact_mod_cast hk
  refine ⟨-((k : ℚ) / l), fun σ hσ => ?_, fun α => ?_, fun b' hb' => ?_⟩
  · rw [hreal] at hσ; simp at hσ
  · constructor
    · rintro ⟨a', ha', rfl⟩
      obtain ⟨j, hj⟩ := (mem_span_iff k a').mp ha'
      refine ⟨(l : 𝓞 ℚ) * ((-j : ℤ) : 𝓞 ℚ), (mem_span_iff l _).mpr ⟨-j, by
        rw [map_mul, map_natCast, map_intCast]⟩, ?_⟩
      change -((k : ℚ) / l) * algebraMap (𝓞 ℚ) ℚ ((l : 𝓞 ℚ) * ((-j : ℤ) : 𝓞 ℚ)) =
        algebraMap (𝓞 ℚ) ℚ a'
      rw [hj, map_mul, map_natCast, map_intCast]; push_cast; field_simp
    · rintro ⟨b', hb', rfl⟩
      obtain ⟨j, hj⟩ := (mem_span_iff l b').mp hb'
      refine ⟨(k : 𝓞 ℚ) * ((-j : ℤ) : 𝓞 ℚ), (mem_span_iff k _).mpr ⟨-j, by
        rw [map_mul, map_natCast, map_intCast]⟩, ?_⟩
      change algebraMap (𝓞 ℚ) ℚ ((k : 𝓞 ℚ) * ((-j : ℤ) : 𝓞 ℚ)) =
        -((k : ℚ) / l) * algebraMap (𝓞 ℚ) ℚ b'
      rw [hj, map_mul, map_natCast, map_intCast]; push_cast; field_simp
  · obtain ⟨j, hj⟩ := (mem_span_iff l b').mp hb'
    obtain ⟨t, ht⟩ := h.symm.dvd
    have ht' : ((k : ℚ) - (-(l : ℚ))) = n * t := by exact_mod_cast ht
    refine ⟨(n : 𝓞 ℚ) * ((-(t * j) : ℤ) : 𝓞 ℚ), ?_, ?_⟩
    · rw [hf]
      exact (mem_span_iff n _).mpr ⟨-(t * j), by rw [map_mul, map_natCast, map_intCast]⟩
    · change algebraMap (𝓞 ℚ) ℚ ((n : 𝓞 ℚ) * ((-(t * j) : ℤ) : 𝓞 ℚ)) =
        (-((k : ℚ) / l) - 1) * algebraMap (𝓞 ℚ) ℚ b'
      rw [hj, map_mul, map_natCast, map_intCast]
      push_cast
      have : (-((k : ℚ) / l) - 1) * (l * j) = -(((k : ℚ) - (-(l : ℚ))) * j) := by field_simp; ring
      rw [this, ht']; ring

end Review
