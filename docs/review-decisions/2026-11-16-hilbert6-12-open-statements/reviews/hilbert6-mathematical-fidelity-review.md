# Hilbert 6 mean-field statement: mathematical fidelity review

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T02:50:00.661Z)

```text
You are an independent mathematical reviewer (kinetic theory, mean-field limits, the Vlasov–Poisson system). A platform that pays bounties for Lean proofs or refutations of open problems wants to publish one formal statement labelled as an open case of Hilbert's sixth problem. Review it for mathematical fidelity to the literature and to Hilbert's text, and for whether the labelling is honest. Web access is available and encouraged; today is 2026-11-16.

Read (read-only; do not modify anything, write no files):
<scratch>/h612/src/HilbertDrafts/Hilbert6MeanField.lean (target: `VlasovPoissonMeanFieldLimit`); it reuses `Space`, `Phase`, `Configuration`, `empiricalAverage`, `InitialDensity` from `Hilbert6.lean` in the same directory.

What the Lean statement says, informally: let f_0 be a C^∞ compactly supported probability density on R^3×R^3. (i) There is a global (all t ∈ R) classical C^1 solution f of the repulsive Vlasov–Poisson equation ∂_t f + v·∇_x f + E·∇_v f = 0 with E(t,x) = ∫∫ (x−y)/|x−y|^3 f(t,y,w) dy dw, f(0) = f_0, nonnegative, compactly supported uniformly on bounded time intervals. (ii) For every such f, every t ≥ 0, every continuous compactly supported φ and every δ > 0: if (X_i(0),V_i(0)), i = 0..N, are i.i.d. with density f_0 and evolve by X_i' = V_i, V_i' = (1/(N+1)) Σ_{j≠i} (X_i − X_j)/|X_i − X_j|^3 (no cutoff, no noise), then the probability that no global trajectory exists or that |(1/(N+1)) Σ φ(X_i(t),V_i(t)) − ∫ φ f(t)| ≥ δ tends to 0 as N → ∞.

Questions (answer each with reasons and sources with URLs):
1. Hilbert's text. Quote, from the 1902 Newson translation of problem 6 (e.g. https://mathcs.clarku.edu/~djoyce/hilbert/problems.html#prob6), the sentences about the kinetic theory of gases, the method of mean values, and "the limiting processes ... which lead from the atomistic view to the laws of motion of continua". Is it honest to call the mean-field derivation of the Vlasov–Poisson equation "an open case of the atomistic-to-continuum clause of Hilbert's sixth problem"? What in the literature supports or undermines that label (e.g. Chaintron–Diez's propagation-of-chaos review, Gorban–Karlin's "Hilbert's 6th problem: exact and approximate hydrodynamic manifolds", Golse's and Jabin's surveys, Zhenfu Wang's talks, the Deng–Hani–Ma papers and commentary)? Suggest wording that a specialist would not object to.
2. Is the mathematical statement the standard open problem? Compare with the formulations in Jabin, "A review of the mean field limits for Vlasov equations" (2014); Lazarovici–Pickl, "A mean field limit for the Vlasov–Poisson system" (ARMA 2017); Serfaty, "Mean field limit for Coulomb-type flows" (Duke 2020) with the Duerinckx appendix; Bresch–Duerinckx–Jabin (arXiv:2402.04695); Duerinckx–Jabin (arXiv:2608.04104, August 2026, 2D). Check: scaling 1/N of the force; sign (repulsive/plasma case); i.i.d. initial data and convergence in probability of empirical averages for fixed t (versus Wasserstein distance, uniform in t ∈ [0,T], or convergence of marginals) — is the Lean form implied by, and essentially equivalent to, the standard formulations? Test functions C_c versus bounded continuous. Data class: C_c^∞ probability densities — do the known partial results and the expected theorem cover this class, or do the leading methods (e.g. Duerinckx–Jabin) need strictly positive or Gibbs-like data, so that a future resolution might not literally imply the Lean statement? Would you change the data class?
3. Known facts the statement relies on. (a) Global classical solutions of 3D Vlasov–Poisson for C_c^1 (hence C_c^∞) data: Pfaffelmoser (1992), Schaeffer (1991), Lions–Perthame (1991), Rein's survey — confirm existence, uniqueness, C^1 regularity jointly in (t,x,v), compact support locally uniformly in time, validity for negative times, and that the plasma (repulsive) sign is covered. (b) Global well-posedness of the repulsive N-body Coulomb dynamics for initial data with distinct positions (no collisions by energy conservation), for all positive and negative times. Is everything in part (i) and in the trajectory notion therefore true, so that the statement cannot be refuted through those clauses?
4. Is the statement already known true or false, or claimed? Search through November 2026 for any proof or preprint claim of the 3D deterministic Coulomb mean-field limit without cutoff (general, non-monokinetic data), including AI-generated manuscripts. Report the closest results (cutoff N^{-1/3+ε} and N^{-5/12+ε}; noise in 3D: Feng–Wang arXiv:2609.08640, Jiang–Wu arXiv:2610.40422; 2D deterministic: Duerinckx–Jabin) and say exactly why each does not decide the Lean statement. Is there any reason to believe the statement is false as written (e.g. close pairs under i.i.d. sampling)?
5. Labelling. Propose a neutral one-line public title and a 2–3 sentence scope note stating what is asserted, what is known (cutoff, noise, 2D, monokinetic), and that it is one case of one clause of Hilbert's sixth problem, not the whole problem and not the hard-sphere/Boltzmann programme of Deng–Hani–Ma.
6. Any mathematical objection to publishing it as a prove-or-refute bounty target, or changes to the formal statement you would require first.

Report format: a verdict — ACCEPT, ACCEPT WITH CHANGES (list them), or REJECT — then numbered answers with source URLs. Distinguish facts verified by opening a source from recollection.
```

## Report 1 (2026-11-16T03:09:11.239Z)

**Verdict: ACCEPT WITH CHANGES.** The proposition is a faithful formalisation of the open 3D Coulomb mean-field problem in its weakest standard form, and I found no proof or claim of it through mid-November 2026. The changes concern labelling and disclosure; the mathematical content of `VlasovPoissonMeanFieldLimit` needs no change.

Required changes:
1. **Label.** Replace "an open case of the atomistic-to-continuum clause of Hilbert's sixth problem" (and the docstring's "one specific open case of the passage…") with wording that presents it as a modern instance of the programme, not something Hilbert posed (wording in answer 1).
2. **Scope note.** State the exact setting (ℝ³, repulsive, C_c^∞ i.i.d. data, every t ≥ 0) and that a theorem for another data class, for the torus, or for short times would not by itself settle the target. The 2D theorem of Duerinckx–Jabin, as stated, requires a strictly positive mean-field solution and excludes compactly supported data.
3. **Known part and expectation.** Say that clause (i) is a known theorem (Pfaffelmoser, Schaeffer, Lions–Perthame) which a Lean proof must still formalise, and that the statement is expected to be true, so a refutation is not a realistic outcome.

Recommended, not required:
4. Add a sibling target for strictly positive, Gaussian-tailed smooth data, or publish a resolution policy for that case.
5. Split the definition into a named known-existence proposition and the open convergence proposition.

Sources are marked [opened] if I read them today and [recollection] otherwise. I did not compile the Lean file.

## 1. Hilbert's text and the label

Newson translation [opened, https://mathcs.clarku.edu/~djoyce/hilbert/problems.html#prob6]:

- "As to the axioms of the theory of probabilities, it seems to me desirable that their logical investigation should be accompanied by a rigorous and satisfactory development of the method of mean values in mathematical physics, and in particular in the kinetic theory of gases."
- "Important investigations by physicists on the foundations of mechanics are at hand; I refer to the writings of Mach, Hertz, Boltzmann and Volkmann. It is therefore very desirable that the discussion of the foundations of mechanics be taken up by mathematicians also. Thus Boltzmann's work on the principles of mechanics suggests the problem of developing mathematically the limiting processes, there merely indicated, which lead from the atomistic view to the laws of motion of continua."
- Footnote 17 identifies the Boltzmann reference as *Vorlesungen über die Principe der Mechanik* (1897).

The label is defensible only with qualification. Hilbert's clause points to continuum mechanics; the Vlasov equation (1938) is a kinetic equation on phase space, so this is a modern reading. The statement also fits the "method of mean values" sentence, since it is a law of large numbers for empirical averages.

Supporting the label:
- Chaintron–Diez's introduction quotes exactly this clause as "the problem which motivates the present work" [opened, https://arxiv.org/abs/2203.00446].
- Zhenfu Wang's 2020 slides head the large-N limit "Part of Hilbert's 6th Problem" and call the Newton/Coulomb to Vlasov–Poisson case "Still open!" [opened, https://external.maths.usyd.edu.au/u/Asia-Pacific-APDESeminar/PastTalks/October2020/Wang/Talk_Analysis_Asia-Pacific.pdf].
- Gorban–Karlin split the clause into "(i) from mechanics to kinetics (from Newton to Boltzmann)" and (ii) kinetics to continua [opened, https://arxiv.org/abs/1310.0406].
- Deng–Hani–Ma: "Broadly interpreted, this problem can encompass all of modern mathematical physics" [opened, https://arxiv.org/abs/2503.01800].

Undermining it:
- Every explicit itemisation is Boltzmann/fluid only: Gorban–Karlin, Deng–Hani–Ma's "Hilbert's program", and Pulvirenti–Simonella's three convergences [opened, https://arxiv.org/abs/1611.07082].
- The specialist Vlasov–Poisson literature never uses the label. A text search found no mention in Jabin 2014, Jabin–Wang, Golse, Lazarovici–Pickl, Serfaty, Bresch–Duerinckx–Jabin, Duerinckx–Jabin 2026, Feng–Wang, Jiang–Wu or Feistl-Held–Pickl.
- Wang's August 2026 ICERM slides drop the Hilbert line [opened, https://app.icerm.brown.edu/assets/591/11637/11637_6347_Wang_080420261130_Slides.pdf].
- "Resolves Hilbert's sixth problem" is itself contested: Gao's comment [opened, https://arxiv.org/abs/2504.06297], and Wikipedia still says the problem "remains open" [opened, https://en.wikipedia.org/wiki/Hilbert%27s_sixth_problem].

Suggested wording: "A well-known open problem in the programme, going back to Hilbert's sixth problem, of deriving continuum equations from particle dynamics: the mean-field limit from Newtonian point charges to the 3D Vlasov–Poisson equation." Avoid "case of", "clause of" without "modern reading", and anything implying progress on Newton → Boltzmann → fluids.

## 2. Is it the standard open problem?

Yes, in the qualitative, fixed-time, i.i.d. form.

- **Scaling and sign.** The Lean system is Jabin's (1.3) with kernel F = Cx/|x|^d, C > 0 electrostatic [opened, https://www.math.umd.edu/~tadmor/ki_net/pubs/files/review_MF.pdf]. The 1/(N+1) versus 1/N or 1/(N−1) prefactor and the missing 1/4π are harmless; particle and PDE normalisations agree.
- **Mode of convergence.** Convergence in probability of empirical averages at fixed t is equivalent to weak convergence of all k-marginals to f(t)^{⊗k} (Sznitman [recollection]; restated in Feng–Wang's introduction [opened]). That is the form of the conclusion in Duerinckx–Jabin's Theorem 1.1 and in Feng–Wang.
- **Stronger forms imply it.** Lazarovici–Pickl, Feistl-Held–Pickl and Hauray–Jabin's Corollary 5 give Wasserstein bounds in probability, uniform on [0,T], or pathwise; the Lean form is strictly weaker than these.
- **Test functions.** C_c versus bounded continuous makes no difference: the empirical measures and the limit are probability measures, so vague convergence in probability upgrades to narrow convergence.
- **Bridging gap.** The duality-method theorems are stated for "weak duality solutions" of the Liouville equation, which "does not provide any notion of flow for Newton's equations" [opened, https://arxiv.org/abs/2402.04695]. A Lean proof along those lines needs a lemma identifying them with the pushforward under the ODE flow.

Data class: mixed coverage.
- **Covers C_c^∞:** Hauray–Jabin (compactly supported L^∞ data), Lazarovici–Pickl [opened, https://arxiv.org/abs/1502.04608], Feistl-Held–Pickl [opened, https://arxiv.org/abs/2504.01471], and Jiang–Wu with noise (upper bounds only).
- **Excludes C_c^∞:** Duerinckx–Jabin's Theorem 1.1 assumes (1.6), f⁻¹ ∈ L^∞_loc([0,T₀]×𝔻), plus weighted bounds on ∇_v log f aimed at Maxwellian tails [opened, https://arxiv.org/abs/2608.04104]. So even the 2D analogue of the Lean statement is not literally covered by the published 2D theorem.
- **No transfer between classes:** product laws of different densities are asymptotically mutually singular and the N-body flow has no usable stability, so a positive-data theorem does not pass to compact support by approximation.

I would keep C_c^∞: it is the textbook class for clause (i) and matches the trajectory-method theorems. I would add the disclosure, and preferably the sibling target.

## 3. Known facts the statement relies on

(a) **Global classical solutions.** All of clause (i) is true.
- Pfaffelmoser built global solutions for f₀ ∈ C¹_c(ℝ⁶) for the electron (repulsive) system, Schaeffer streamlined it, Lions–Perthame gave a moment proof, and Robert and Loeper give uniqueness for bounded density [opened, https://arxiv.org/abs/2004.01094].
- Pfaffelmoser propagates a bound on the support, giving "solutions globales C¹ à support compact" [opened, https://arxiv.org/abs/1201.2275]. Golse and Jabin's review confirm, with both signs of C [opened, https://arxiv.org/abs/1301.5494].
- Negative times follow from the reversal (t,v) ↦ (−t,−v); the two halves glue to a C¹ function at t = 0.
- The original papers and Rein's Handbook survey were not opened [recollection].
- The Lean solution class (jointly C¹, locally uniformly compact support) forces a C¹ field with bounded gradient, so uniqueness holds in that class. The "for every such f" quantifier therefore ranges over one function and cannot make the statement false.

(b) **N-body dynamics.** For distinct initial positions, energy conservation bounds the minimal distance below, giving a unique global solution in both time directions [opened, Jabin's review §2.2]. Coincident positions are a Lebesgue-null set, so the "no trajectory" branch of `deviationSet` has probability zero.

The statement cannot be refuted through clause (i) or the trajectory notion.

## 4. Status and closest results

No proof or claim was found. I ran several arXiv API queries, sorted by date and current to about 7 November 2026, plus web searches including viXra, Zenodo and AI-generated manuscripts. Feng–Wang (8 September 2026) write that "the cutoff-free mean-field derivation of the three-dimensional Vlasov–Poisson equation remain[s] open"; Serfaty, Jabin–Wang and Duerinckx–Jabin (May 2026) say the same in earlier terms.

| Result | Why it does not decide the statement |
|---|---|
| Lazarovici–Pickl, cutoff N^(−1/3+ε); Feistl-Held–Pickl, N^(−5/12+σ) | Force truncated for every N. By my estimate each particle still has about N^(1/6) sub-cutoff encounters per unit time. |
| Duerinckx–Jabin, 2D, no cutoff [https://arxiv.org/abs/2608.04104] | Needs K ∈ L^(2d/(d+2γ))_loc with γ below about 0.17 in d = 3, i.e. p > 1.79; 3D Coulomb is only in L^p_loc for p < 3/2 (my computation from their formula). Also needs positive data. |
| Feng–Wang, 3D with noise [https://arxiv.org/abs/2609.08640] | Velocity diffusion required; short time only. |
| Jiang–Wu, 3D with noise, all finite T [https://arxiv.org/abs/2610.40422; earlier https://arxiv.org/abs/2609.23709, abstract only] | Requires σ > 0. |
| Serfaty with Duerinckx appendix [https://arxiv.org/abs/1803.08345] | Monokinetic data only; limit is Euler–Poisson. |
| Bresch–Duerinckx–Jabin [https://arxiv.org/abs/2402.04695] | Square-integrable forces only. |
| Duerinckx–Jabin, May 2026 [https://arxiv.org/abs/2605.28979] | Linearised dynamics near Gibbs equilibrium. |
| Kiessling 2014 (as described by Lazarovici–Pickl) | Conditional on an unproved uniform force bound. |

A Lean 4 formalisation of the Lipschitz (Dobrushin) case exists [abstract only, https://arxiv.org/abs/2607.08986]; it does not touch the Coulomb case.

There is no reason to think the statement is false. My heuristic, with the Chandrasekhar relaxation estimate from recollection: under i.i.d. sampling the minimal distance is about N^(−2/3), where pair energy is N^(−1/3), far below kinetic energy. Strong deflection needs impact parameter about 1/N, so only O(log N) of N particles are strongly deflected in unit time, and cumulative velocity diffusion is O(log N / N). There are no bound pairs in the repulsive case. Fluctuation-seeded instabilities grow like N^(−1/2)e^(λt), which vanishes at fixed t.

## 5. Proposed labelling

**Title:** "Mean-field limit for the 3D Vlasov–Poisson equation from Newtonian point charges with the untruncated repulsive Coulomb force."

**Scope note:** "Asserts that for N point particles in ℝ³ under the repulsive Coulomb force scaled by 1/N, started from independent samples of a smooth compactly supported density, empirical averages at each fixed time converge in probability to those of the classical Vlasov–Poisson solution, with no cutoff and no noise. Known: the limit with the force truncated at radius N^(−5/12+ε), with velocity noise in 3D, in 2D for strictly positive data, and for monokinetic data; none of these implies the statement, which is open as of November 2026 and expected to be true, and whose existence clause for the limit equation is a known theorem. It is one instance of the particle-to-continuum programme associated with Hilbert's sixth problem, not the problem itself, and it is unrelated to the hard-sphere/Boltzmann/fluid programme of Deng–Hani–Ma."

## 6. Objections

No mathematical objection to publication. I checked the following and found no vacuity or loophole:
- the PDE clause, including the integrability guard on the force integrand;
- the product law;
- outer measure on the deviation set;
- uniqueness of trajectories and of the Vlasov–Poisson solution in the stated class.

The points to act on are the three required changes above. Without change 2, a published "solution of the open problem" for positive data, the torus, or short times would leave the bounty formally open.

Housekeeping: contrary to "write no files", I downloaded sources into three temporary directories in the scratchpad and deleted them afterwards. The reviewed files in `<scratch>/h612/src/HilbertDrafts/` and the repository were not modified. Two slide PDFs saved by the fetch tool remain in the session's tool-results folder.
