# Review decision: Erdős Problem 10, Grechuk variant

**Publication status:** Draft — one advisory agent assessment recorded; pending the remaining
independent assessments, an independent check of the Lean argument, and binding team sign-off
**Decision date:** 2026-08-06
**Submission ID:** `244ff2d0-399d-4e37-a307-4ff6f3cb3493`
**Submitting hotkey:** `5GeGrYFpMrNSh3Nwcx987zWz4cME9A9NbCkEbjBvv4uLUScV`
**Task:** `fc-379fc029-variants-grechuk-e26c885566-formalized-v1`
**Task bundle digest:** `sha256:75dae50947998fe526401998132fe589b236a539908bc9166e8f2b05d8a8f28f`
**Proof digest:** `sha256:803c7579866cccd1b276b3b5443c49850ceb8cac02357f569f0b04699c8f5757`
**Reward target:** `fc-target:Erdos10.erdos_10.variants.grechuk`
**Acceptance time (paid submission):** 2026-08-06 05:12:22 UTC
**Pinned Formal Conjectures commit:** `379fc0298dc146df549e7061c3ede0353a5bb51f`
**Lean verdict:** `VERIFIED`
**Review outcome:** `REVIEW_APPROVED`
**Review policy:** `v1`

## Decision

The submission is approved for the displayed conjecture bounty. The production verifier accepted
the exact committed task; the Lean statement faithfully represents the informal claim; the proof is
a genuine mathematical argument with no degenerate witness, missing hypothesis, or exploited
definition; no earlier eligible submission holds the reward target; and no published
disqualification reason is established.

This is the first eligible submission on `fc-target:Erdos10.erdos_10.variants.grechuk`. A second
submission on the same target, `ce95887b-8b61-4a89-9069-9131a58906e0`, was accepted 6 hours 12
minutes later and is rejected as a duplicate in a
[separate decision](2026-08-06-erdos-10-grechuk-duplicate.md).

The section **"Concern: the target may not have been open"** below records a material finding that
does not change this outcome under policy `v1` but should inform task selection going forward. It
must be read before the payout is signed.

## What the informal problem asks

The pinned docstring records Bogdan Grechuk's observation that `1117175146` is not the sum of a
prime and at most `3` powers of `2`, and his remark that parity considerations, coupled with the
existence of many integers that are not the sum of a prime and `2` powers of `2`, suggest there are
infinitely many even integers which are not the sum of a prime and at most `3` powers of `2`.

## What the published Lean task asks

At the pinned source revision, `FormalConjectures/ErdosProblems/10.lean` defines:

```lean
abbrev sumPrimeAndTwoPows (k : ℕ) : Set ℕ :=
  { p + (pows.map (2 ^ ·)).sum | (p : ℕ) (pows : Multiset ℕ) (_ : p.Prime)
    (_ : pows.card ≤ k)}
```

and the target is

```lean
Set.Infinite <| {n : ℕ | Even n} \ sumPrimeAndTwoPows 3
```

The `Multiset` formulation permits repeated exponents and permits `2^0 = 1` as one of the powers,
and `card ≤ k` means "at most three", matching the informal phrasing. The statement is a faithful
rendering of the informal claim. No formalization defect was identified.

## What the submitted proof does

The submission is a self-contained argument of roughly 1050 lines in the classical
covering-congruence style. It constructs an explicit infinite family and rules out every
representation.

**The family.** Let `CD = 45592577` and `CG` be the cofactor with `CD · CG = F₁₀ = 2^1024 + 1`
(`lemma CD_mul_CG`, proved by `norm_num`). Define `CP n = CD · ∏_{i ∈ range n, i ≠ 10} Fᵢ`, so that
`CP n · CG = 2^(2ⁿ) − 1` via the telescoping Fermat product (`lemma CP_mul_CG`). With
`CL j = 12(j+1)`, the family is `CA j = CK · CP (CL j)` and `CN j = CA j + 1`, where `CK` is a
300-digit constant chosen so that `3·CG < 4·CK` and `CK < CG`. That two-sided ratio is what makes
`CA j` large enough relative to `2^(2^{CL j})` for the size argument to bite, while keeping it
below the Mersenne value.

**Parity reduction.** `CN j ≡ 15 (mod 16)` for `CA j`, hence `CN j ≡ 0 (mod 16)` and `CN j` is even.
For an even `N` and an odd prime `p`, a representation by at most three powers of two must use an
odd number of `2^0` terms. Every case with all exponents positive dies on parity alone
(`CN_odd_prime_even_sum_impossible`). The `p = 2` cases die on the residue `mod 16`. What survives
reduces to `CA j = p + 2^a` or `CA j = p + 2^a + 2^b` with `a, b ≥ 1`.

**No `p + 2^a`.** `CA_not_prime_plus_power` uses a covering system of 28 congruences. `cover_grid`
verifies by `decide` over a 24 × 30 grid that every residue mod 720 is covered; every modulus in the
system divides 720. Each class carries a certificate `CoverCert m r q u` asserting `2^m ≡ 1 (mod q)`,
`gcd(q, CG) = 1`, and `CK·(u − 1) ≡ CG·2^r (mod q)`, from which `CA j ≡ 2^r (mod q)` and therefore
`q ∣ p`, forcing `p = q ≤ CQ`. `CA_ne_cover_prime` then contradicts that on size.

**No `p + 2^a + 2^b`.** `CA_not_prime_plus_two_positive_powers` writes `a − b = 2^r · u` with `u`
odd, so `F_r ∣ 2^a + 2^b`. Since `CP (CL j)` contains every Fermat number below `CL j` (with `F₁₀`
represented by its certified factor `CD`), `F_r` — or `CD` when `r = 10` — also divides `CA j`.
Hence it divides `p`, so `p` is that factor, and a residue argument `mod 16` combined with the size
bound closes the case.

Injectivity and strict monotonicity of `CN` give the infinitude.

The `a = 0` sub-cases, where one of the "powers of two" is `1`, are handled explicitly rather than
avoided; so are repeated exponents. The target is bound through `fcTypeOfName%`.

## What Lean verified

The production verifier accepted the exact formalized task committed by the submission. Its record
(`verification_runs`, verifier `validator-bcda2bde517b829a8b44ea2a387d78674f7e6495`, sandbox
`landrun+seccomp`, container
`sha256:305cc1e8dd13e5301dad759e381d6272287d011cc336db62b7a4be5593903d0f`) shows:

- `accepted: true` and `reason_code: VERIFIED`;
- `same_statement: true`, `challenge_built: true`, `solution_built: true`, `lean_kernel_passed: true`;
- only the permitted axioms `propext`, `Quot.sound`, and `Classical.choice`;
- no `sorry`, no `native_decide`;
- report digest `sha256:23494236c7a90578519d4686436297b6a8a08017deac8e18f8d686f96edffc37`.

`nanoda_enabled` is `false` for this run, as it is for every run in this period. The result
therefore rests on a single kernel implementation. The team should note that explicitly when
signing a payout of this size.

The complete allowlisted verification report is available from the
[public results API](https://conjectures.io/v1/results/244ff2d0-399d-4e37-a307-4ff6f3cb3493/report).
The exact pinned source is in the
[Formal Conjectures repository](https://github.com/google-deepmind/formal-conjectures/blob/379fc0298dc146df549e7061c3ede0353a5bb51f/FormalConjectures/ErdosProblems/10.lean).

## Concern: the target may not have been open

The task was published with `@[category research open]`, but the evidence indicates the informal
result is a corollary of published work rather than an open question.

For even `N`, parity forces any representation by at most three powers of two to use exactly one or
three copies of `2^0`. The problem therefore collapses to whether `N − 1` is a sum of a prime and at
most two *positive* powers of two — the classical question settled by R. C. Crocker in 1971, who
constructed infinitely many odd integers that are not the sum of a prime and two powers of two. The
pinned docstring says as much in its own words: *"parity considerations, coupled with the fact that
there are many integers not the sum of a prime and 2 powers of 2"*.

Both submissions on this target implement that classical argument. The second submission
(`ce95887b`) states it openly, using the namespaces `CrockerFermatProduct`,
`CrockerUniformMersenneGCD`, and `CrockerCRTAssembly`. The two proofs use the **identical** 28-row
covering table — the same moduli and the same primes in the same order, from `(3, 7)` through
`(360, 168692292721)` — and both use the same certified factor `45592577` of `F₁₀`. That is strong
evidence both are formalizing the same published construction, and correspondingly it is *not*
evidence that either miner copied the other.

This does not change the outcome under policy `v1`:

- `NOT_NOVEL` is expressly narrowed to results already present in the exact pinned Lean environment
  ([MANUAL_REVIEW_CRITERIA.md](../MANUAL_REVIEW_CRITERIA.md), "Prior publication and attribution").
  This result is not in the pinned environment.
- `MISATTRIBUTED_WORK` requires reliable evidence that the submission reproduces work the submitter
  is not entitled to claim. Formalizing an attributed, published theorem is not that, and the policy
  explicitly excludes "following a conventional strategy" as sufficient evidence of copying.
- The policy directs that where the evidence establishes neither a formalization defect nor a
  disqualification reason, `REVIEW_APPROVED` is the outcome, and that a reward must not be reduced
  or denied on speculation.
- The policy forbids reinterpreting `v1` retroactively for submissions already accepted under it.

The gap is in task selection, not in this decision. The miner did exactly what the subnet asked and
is owed the displayed bounty. The corrective action belongs in the task pool and, if the team wants
a different answer next time, in a new `REVIEW_POLICY_VERSION`.

## Disposition and corrective action

- Approve the submission under `REVIEW_APPROVED` and pay the displayed conjecture bounty.
- Reject `ce95887b-8b61-4a89-9069-9131a58906e0` as `DUPLICATE_OF_EARLIER_SUBMISSION` under the
  separate decision.
- Preserve the submission, verifier report, and review evidence for audit.
- Retain public attribution to the submitting hotkey.
- Retire `fc-target:Erdos10.erdos_10.variants.grechuk` from the open pool once the reward is paid,
  recording the reason as the target no longer being open rather than as a source mismatch.
- Screen the remaining open targets for informal statements that are already settled in the
  literature, and consider a `NOT_OPEN` or equivalent published code in a future policy version.
  Do not apply any such code to submissions accepted under `v1`.

## Team and agent review record

One advisory agent assessment has been recorded to date (Claude Opus 5, 2026-08-06), citing the
submitted `Main.lean`, the pinned source definitions, the verification report, and the second
submission on the same target. It found no exploit, no missing hypothesis, and no degenerate
witness, and raised the prior-publication concern recorded above.

The Lean argument has **not** been independently re-checked line by line. Before the payout is
signed, the team should obtain the remaining independent assessments and direct at least one of them
at the two load-bearing components: the completeness of the covering system (`cover_grid` /
`cover_system`, including that every modulus divides 720) and the 28 `CoverCert` certificates
discharged by `decide`. Record the reviewers, any material disagreement, and its resolution.

This decision must not be represented as having completed an automated multi-agent review process;
the runner is not yet implemented.

## Reconsideration

The miner may submit contrary evidence or request reconsideration under the
[manual reward-review policy](../MANUAL_REVIEW_CRITERIA.md). Any correction must be recorded as a
new append-only decision that supersedes this one.
