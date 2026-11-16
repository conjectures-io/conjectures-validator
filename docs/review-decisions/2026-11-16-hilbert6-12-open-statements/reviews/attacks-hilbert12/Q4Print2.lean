import HilbertDrafts.Hilbert12Lambda

open HilbertDrafts.Hilbert12 HilbertDrafts.Hilbert12Lambda

-- Show the proof argument hidden as `⋯` in the degree: where does `CharZero K` come from?
set_option pp.proofs true in
set_option pp.explicit true in
example : True := by
  have h : BorgerDeSmitQuestion4 → True := by
    intro h
    unfold BorgerDeSmitQuestion4 at h
    obtain ⟨K, i, j, h1, -⟩ := h
    trace_state
    trivial
  trivial
