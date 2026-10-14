# Collatz additions and packing audit — 12 October 2026

Three separate hard Lean questions are ready for question-only review in isolated validator and frontend branches. C12 is a named published conjecture, C13 is an explicitly newly proposed stochastic-model strengthening, and C14 is Tao’s predicted exponential mixing strengthening. The September preprint was obtained in full through HAL’s public metadata API and compared; C14’s temporary overlap hold is resolved. This is a dated, bounded openness review, not a proof that no publication exists anywhere.

The packing audit independently matched P15/P16/P18 to a published computer-assisted optimality result and replayed all 47 distributed certificate stages. Both square candidates remain held. Nothing was deployed, and no live registry, submission, reward commitment, original packet, proof-review decision, or Taostats file was changed.

## Proposed questions

| ID | Standalone Lean proposition | Partial target and difficulty | Readiness |
|---|---|---|---|
| C12 | `IndependentCollatz.ModEightEscape` in [C12.lean](C12.lean) | Every positive ordinary orbit reaches 1 or 5/7 modulo 8. Exact Yolcu–Aaronson–Heule Conjecture 4.14; recent finite-certificate weakenings still need another exit residue. | Question-only review ready |
| C13 | `IndependentCollatz.SqrtPassageGaussian` in [C13.lean](C13.lean) | Natural-density Gaussian law for the first shortcut passage below the square root of its own start, with explicit centering and variance. Requires fluctuations of order sqrt(log n), beyond coarse trajectory approximation and the initial parity-uniformity window. | Question-only review ready; proposed stochastic-model strengthening, not a named published conjecture |
| C14 | `IndependentCollatz.ExponentialSyracuseMixing` in [C14.lean](C14.lean) | Uniform exponential L1 mixing of exact rational Syracuse offset laws above a coarse ternary scale. Strengthens every fixed inverse-power estimate to a single exponential bound. | Question-only review ready; September overlap check resolved |

Each file imports Mathlib and defines its own proposition and dependencies. No unresolved external declaration is assumed; there is no `sorry`, added axiom, or claimed proof of the target. “Separate” means self-contained statements, not a claim of logical independence. C12 permits an orbit to leave via a residue and then diverge; C13 stops at sqrt(n) with all positive starts in its denominator, including nonpassage starts; C14 is an analytic distributional subproblem. None asserts universal convergence to 1.

### Precise statements and source comparison

**C12.** Ordinary C(n) is n/2 on even n and 3n+1 on odd n. The statement is `∀ n>0, ∃k≥0, C^[k](n)=1 ∨ C^[k](n)%8=5 ∨ C^[k](n)%8=7`. The original nonconvergent-orbit formulation is equivalent: convergent starts supply the exit 1; a nonconvergent orbit cannot use it. The map is defined directly, so no shortcut-clock conversion is hidden.

Primary sources: [Yolcu–Aaronson–Heule §4.5, Conjecture 4.14](https://emreyolcu.com/research/rewriting-collatz.pdf); [October 2, 2026 formalized weakenings and matrix-interpretation barriers](https://github.com/harappa/collatz-matrix-interpretation-barriers). The latter still identifies the exact endpoint as unresolved; the extra exits in its 4.14 weakenings (4089 or 10913 modulo 2^14) are absent here. Those repositories were inspected for statement overlap, not independently rebuilt in this task.

**C13.** For shortcut T(n)=n/2 or (3n+1)/2, let τ(n) be the first k with T^k(n)^2≤n. Put d=log(4/3), μ(n)=log(n)/d and σ(n)^2=(log 3)^2 log(n)/d^3. For each real z, count starts 1≤n≤N admitting a first passage with k≤μ(n)+zσ(n), divide by N, and ask for limit Φ(z). The Lean statement uses an existential first passage, so it never silently assigns a finite time to a nonpassage orbit. n=1 and N=0 are harmless finite-boundary conventions.

[Kontorovich–Lagarias §3.2, Theorem 3.2](https://arxiv.org/pdf/0910.1944) supplies the stochastic first-passage motivation; it is not a deterministic integer-orbit theorem. The shortcut drift is d/2, increment variance (log 3)^2/4, barrier distance log(n)/2, giving exactly the stated normalization. Typical passage is about 3.476 log(n), beyond the initial parity window about 1.443 log(n). [Inselmann Theorems 1.1/1.3](https://arxiv.org/html/2402.03276v3) allow ε log(n) comparison errors, which do not prove a sqrt(log(n)) CLT. The [September Gaussian history-law result §4](https://github.com/KokunoYumeto/collatz-workbench/blob/main/collatz_reconstruction/research_program/history_law_cutoff_20260914/RESEARCH_NOTE.md) concerns total variation of complete valuation words, not this first-passage limit. No exact proof was located in the bounded literature/formalization screen. This is not an exhaustive novelty certificate.

**Provenance and plausibility of C13.** The exact moving-threshold CLT was formulated in this review packet; it is not attributed to Kontorovich–Lagarias or Inselmann as their conjecture and no priority claim is made. The fixed-time distribution results surveyed in Kontorovich–Lagarias §5, Theorems 5.4–5.5, take the density limit for each fixed iterate count before taking time large; the subsequent remark warns against interchanging the limits. C13 couples time to log(n). Inselmann’s density-one transport machinery (§1.2, Lemma 2.16) does not directly transport events of intermediate density Φ(z), and its Theorem 1.6 parity error scale is ε log(n), not the requested Gaussian fluctuation law. No published counterexample or proof of this exact formulation was located.

We independently checked all starts through 10^6 with exact integer arithmetic ([data](evidence/c13-plausibility.json)); every square-root passage completed within 257 steps. The finite CDF is **not** close to Gaussian in all tails: at z=−1,0,1 it is approximately .0738,.5714,.8592. These values are neither a proof nor a counterexample to an asymptotic statement. In particular, T(n)≥n/2 implies τ(n)≥log(n)/(2log2); at these modest sizes the resulting standardized lower endpoint excludes much of the Gaussian left tail. That endpoint tends to −∞ and the normalized lattice spacing tends to zero, so neither finite obstruction persists asymptotically. The model derivation remains a plausibility argument, not a transfer theorem.

**Relation to full Collatz.** Full Collatz gives finite square-root passages but does not directly supply a Gaussian distribution or rate. Conversely, C13 only measures a prefix ending at sqrt(n), not subsequent arrival at 1. As a structural check, if two maps agree above a fixed M, their square-root first-passage observations agree for n>M²: every state strictly before passage exceeds sqrt(n)>M. Changing dynamics in a bounded region can therefore change eventual convergence while leaving this asymptotic observable unchanged. This does not prove logical independence for the fixed Collatz map. No implication in either direction, equivalence, or strict ordering of proof difficulty is asserted. C13 is classified as a quantitative partial-statistics target, with these limitations explicit.


**C14.** `syracuseMass n r` is an exact rational law modulo 3^n. Its recursion uses the affine update (3y+1)2^(-a) modulo 3^(n+1), with geometric weights summed over the exact period 2·3^n. The target compares p_n to 3^(m−n)p_m(r mod 3^m), not to a globally uniform distribution. Coarse marginals, support and the level-1/2 examples were checked exactly through level 4. The L1 norm is twice conventional total variation, absorbed into C.

[Tao v7, July 16, 2026](https://arxiv.org/html/1909.03562v7), Lemma 1.12 and equation (1.23), identify this law; Proposition 1.14 proves C_A m^(−A); Remark 1.15 predicts exponential improvement. This is Tao’s predicted strengthening, not Tao’s theorem.

The [al-Jammas–Kolpakov–Luo manuscript](https://hal.science/hal-05716421/file/collatz_novikov_paths.pdf), posted to SSRN in September 2026, was obtained in full using the file URL returned by HAL’s public metadata API. Section 2 defines conductor as n−v_3(ξ), an exponent rather than the modulus 3^n. Theorem 5.2, equation (63), bounds retained-weight L1 mixing by O_A(q^(−A)); with H=1 its separability hypotheses are trivial and the unweighted law is exactly the law used here. Thus it recovers inverse-power bounds at every fixed power, without uniform control of the power-dependent constants giving exp(−cq). Internal exponential tail estimates do not change the theorem’s final rate. Section 9 expressly assumes the analytic estimates in its Lean development. **C14’s overlap hold is resolved.** See [the source hash and theorem comparison](evidence/c14-preprint-comparison.json); this overlap audit does not certify every other claim of the preprint.

## Every packing candidate

| Candidate | Frozen answer | Exact published comparison | Decision |
|---|---|---|---|
| P15: circles in unit disk | 1/(1+sqrt(1+(2+c)^2)), c=sqrt(1+2/sqrt(5)) | [October 2026 proof package](https://nor-git.pages.dev/circle-packing/), Theorem 1.1: container radius 1+sqrt(6+2/sqrt(5)+4sqrt(1+2/sqrt(5))). Squaring and expanding gives the same reciprocal radius. | Withdraw as an open-problem proposal |
| P16: circles in unit disk | h/(1+h), h the unique frozen six-variable root | Same Theorem 1.1, §8.1 (8.5), Appendix A.1: radius 1/(1+b16). `match_p16.py` maps the complete published rational b16 interval through h=1/b16 and five coordinate expressions into all six frozen root intervals. Published contacts match the frozen equations, and the prior uniqueness certificate identifies the root. | Withdraw as an open-problem proposal |
| P18: circles in unit disk | 1/(1+sqrt(2)+sqrt(6)) | Same Theorem 1.1; literal reciprocal of the published container radius. | Withdraw as an open-problem proposal |
| P34: circles in unit square | 1/(4+2sqrt(2)+2sqrt(6)) | [Markót 2021 §8](https://link.springer.com/article/10.1007/s10898-021-01086-z) distinguishes solved 31–33 from unresolved 34/35. [Locatelli–Raber 2002](https://www.sciencedirect.com/science/article/pii/S0166218X01003596) reports tolerance-level optimality, not this exact radical upper bound. | Hold; retain artifacts |
| P35: circles in unit square | 1/(8+sqrt(2)+sqrt(6)) | Same comparison as P34. Construction feasibility is already Lean-proved; global exact optimality is a different claim. | Hold; retain artifacts |

The [author’s October 11 release](https://nor-blog.pages.dev/posts/2026-10-11-circle-in-circle-packing-15-20/) links the paper and certificate package. We downloaded that public package, inspected the scripts, and ran its verifier: **47/47 stages passed**, including 15, 16, 17, 18 and 20. This is a replay of distributed exact computer-assisted certificates; it is not a Lean proof of global optimality, an independent reconstruction of the entire proof, or a peer-review guarantee. The two radical matches are algebraic; the P16 interval match is a separate exact rational check, not merely a decimal comparison. See [p16-source-match.json](p16-source-match.json).

[Packomania’s September 2026 table](https://packomania.com/csq/csq.html) supports best-known square configurations but does not establish an exact global upper bound. The 2002 full text was not obtained; its author-uploaded abstract expressly limits the result to a tolerance. No later exact upper certificate for these two frozen statements was verified. Treating either as definitively solved, or as publication-ready open, would overstate this audit.

## Exact recovered inventory and reversible staging changes

The eleven-candidate packet contains C1–C6 and precisely P15/P16/P18/P34/P35. A later five-Collatz packet C7–C11 was also found. The four currently visible test previews are synchronous coalescence, strict adjacent coalescence, logarithmic-time adjacent coalescence, and equal-first-hit runs. These four are preserved verbatim; the new three are appended in the isolated frontend only. The deployed test page still has the original four proposals plus the original full Collatz catalog item.

Remote original artifacts:

- `/root/conjectures-validator/output/candidate11-held-fixes-20261008`
- `/root/conjectures-validator/output/candidate11-p16-lean-20261009`
- `/root/conjectures-validator/output/collatz-hard5-pool-20261009`
- `/tmp/conjectures-collatz-preview-20261007-opus55-8bc2cee4/web`, commit `3ffa2c4`

P15/P16/P18 had already been withdrawn on October 11 on an external proof claim. This work independently strengthens the basis; it does not pretend to remove live entries. Both API catalogs returned zero packing matches at baseline. Counts 17/20 were existing exclusions, not extra candidate rows.

`candidate11-status.json` is an isolated revised mirror; `evidence/*-status-before.json` preserves the source snapshots. `prepare_audit.py` changes only the five packing dispositions and candidate lists, asserts that all C1–C6 records are unchanged, and keeps exclusions. `packing-status.json` is the authoritative proposed-pool packing decision in this packet. There are zero active packing proposals. Restore the snapshot or revert the commit to undo these staging-only changes. No deployed registry, submission or reward state needs rollback.

Earlier Collatz advisory holds are not silently cleared: C4 retains its explicit proposed-target/novelty qualification; C6 still requires equivalence to formal Bernstein–Lagarias Phi. C7–C11 were not republished or edited. In particular, C8 density-one convergence requires renewed equivalence review in light of recent positive-density predecessor claims. Natural-density descent and predecessor-density formulations were rejected for the new selection because recent repositories claim stronger formalizations: [fixed-barrier power saving](https://github.com/harappa/collatz-fixed-barrier-power-saving), [ConjectureA](https://github.com/neljaouh/ConjectureA), and [ProofAtlas predecessor density](https://www.proofatlas.ai/formalizations/positive-lower-density-collatz-predecessors/). These claims were not independently replayed here and are not reported as verified theorems. A proposed “no paradoxical start above 4614” formulation was rejected because it would imply full Collatz.

The search included the pinned catalog (5231 rows), the original C1–C11 packets, the four current previews, and named primary papers/repos. No exact duplicate of C12/C13 was located. Current search is a dated bounded screen, not proof that no publication or formalization exists anywhere. Local Codex memory directories had no matching summaries; the remote memories database search returned no matching Collatz/packing stage summaries. No applicable checkout AGENTS.md was present; `.agents`/`.codex` were empty. These negative checks are recorded in baseline evidence.

## Validation and reproduction

Run `python3 check_semantics.py` for exact recurrence and normalization checks and `python3 check_c13_plausibility.py` for the finite first-passage screen through one million starts. Neither proves a research target.

The five Lean files compile with `--trust=0` in verifier image `sha256:3d8cebd8716a9bacc2b750222146bb0b92d8246ebce25218846f552915c7740e`, with network disabled. Mount this directory read-only at `/inputs`, a writable result directory at `/outputs`, and writable ephemeral storage at `/opt/fc-verifier/.work`; run `python3 /inputs/compile.py`. The compilation receipt records the exact source hashes and exit codes.

The retained packing receipt records the original independent 47-stage certificate replay. The primary-source comparison, exact containment checker and packing dispositions are preserved. Machine-specific build logs, deployment snapshots and one-off synchronization scripts are excluded from this source publication.

The companion frontend PR supplies gated question previews and tests for statement identity, no bounty, disabled submissions, fixture gating and non-shadowing of real targets. This packet does not register targets, change reward eligibility, repin production sources or deploy anything. P34/P35 remain held, and C13 retains its explicitly proposed provenance.
