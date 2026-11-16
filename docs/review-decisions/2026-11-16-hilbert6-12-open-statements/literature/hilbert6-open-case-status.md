# Hilbert 6: choice and status of the open statement

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T02:23:02.030Z)

```text
Research task with web access (today is 2026-11-16). A prize platform wants to attach a bounty to ONE precise, recognised, still-open mathematical statement that lies inside Hilbert's sixth problem, specifically inside this clause of Hilbert's 1900 text: "Boltzmann's work on the principles of mechanics suggests the problem of developing mathematically the limiting processes, there merely indicated, which lead from the atomistic view to the laws of motion of continua." The statement must (a) be crisp enough to state as a single closed mathematical proposition, (b) be widely recognised in the literature as open, (c) have NO published or preprint proof claim that would make it "claimed resolved", and (d) ideally be explicitly connected to Hilbert's sixth problem in the literature. Be skeptical and cite sources with URLs and dates. Separate what you verified by opening a source from what you saw only in snippets.

Background you should verify rather than trust: Deng, Hani and Ma (arXiv:2408.07818, "Long time derivation of the Boltzmann equation from hard sphere dynamics"; arXiv:2503.01800, "Hilbert's sixth problem: derivation of fluid equations via Boltzmann's kinetic theory") claim the hard-sphere → Boltzmann → Euler / Navier–Stokes–Fourier derivation. So that chain is "claimed" and cannot be used. Report their current publication/acceptance status and any published criticism, briefly.

Leading candidate A — the mean-field limit for Vlasov–Poisson: N point particles in R^3 with positions X_i and velocities V_i evolving by Newton's equations with the repulsive Coulomb force scaled by 1/N, i.e. X_i' = V_i, V_i' = (1/N) Σ_{j≠i} (X_i − X_j)/|X_i − X_j|^3, with initial data i.i.d. from a smooth compactly supported density f_0; claim: for each t in [0,T] the empirical measure converges (weakly, in probability) to the solution f(t) of the Vlasov–Poisson equation with initial datum f_0. No cutoff of the singularity, no added noise, general (not monokinetic) data. Please establish:
 1. Whether exactly this is stated as an open problem in authoritative sources, with quotes: e.g. P.-E. Jabin, "A review of the mean field limits for Vlasov equations" (Kinet. Relat. Models 2014); F. Golse's lecture notes on mean-field limits; S. Serfaty's ICM 2018 proceedings and "Mean field limit for Coulomb-type flows" (Duke 2020, with the Duerinckx appendix on the monokinetic case); Hauray–Jabin (2007, 2015); Lazarovici–Pickl (2017); Bresch–Jabin–Soler; Bresch–Duerinckx–Jabin (arXiv:2402.04695); Duerinckx–Serfaty; any 2023–2026 survey.
 2. The best partial results (which singularities/cutoffs/noise/dimensions are covered) so it is clear the stated problem is not covered. In particular: is the repulsive 3D Coulomb case with deterministic Newtonian dynamics and no cutoff covered by anything, including 2024–2026 papers or AI-generated manuscripts (e.g. the OpenAI "math" GitHub repository of October 2026, community index at https://mathcommunity.github.io/oai-math-revised/ if reachable)?
 3. The standard precise formulation: mode of convergence (weak convergence of empirical measures in probability / propagation of chaos / Wasserstein), assumptions on f_0, time interval, whether "attractive (gravitational)" is treated separately, and whether global well-posedness of the N-body repulsive Coulomb dynamics for distinct initial positions is standard (no collisions by energy conservation) — cite.
 4. Whether the literature explicitly ties mean-field limits / Vlasov derivation to Hilbert's sixth problem (quote if so).

Alternative candidates — assess each in a few lines with sources, and say whether it is crisper or better recognised than A:
 B. Rigorous derivation of the Landau equation from Newtonian particle dynamics in the weak-coupling limit (Bobylev–Pulvirenti–Saffirio 2013 consistency result; is even short-time derivation open?).
 C. Direct hydrodynamic limit from deterministic Hamiltonian particle systems to the compressible Euler equations without passing through Boltzmann (Morrey 1955; Olla–Varadhan–Yau 1993 needs noise).
 D. Boltzmann–Grad limit for long-range or general short-range potentials beyond short times.
 E. Any other crisp open statement you judge better.

Finish with a recommendation: which single statement is the best fit for criteria (a)–(d), its exact wording as you would state it mathematically, and the main risk that it is not actually open or not crisp. Do not write any files.
```

## Report 1 (2026-11-16T02:37:50.495Z)

**Recommendation: Candidate A, restricted to 3D, repulsive, deterministic, no cutoff. It is still open as of 2026-11-16, but the frontier moved sharply in the last three months and the Hilbert-6 link is the weakest criterion.**

No repo files were written. I extracted PDF text in the session scratchpad and deleted those temp files afterwards.

## Deng–Hani–Ma status

- **arXiv:2408.07818** (v1 14 Aug 2024, v3 18 Jul 2025): accepted by Annals of Mathematics. The Annals page shows received 2024-11-14, revised 2025-10-14, accepted 2025-10-31, "To appear in forthcoming issues" (https://annals.math.princeton.edu/articles/22284).
- **arXiv:2503.01800** (v1 only, 3 Mar 2025): no journal reference on arXiv. Kenig's ICM 2026 laudatio (arXiv:2609.14766, 13 Sep 2026) cites it as arXiv only, while citing the first paper as "to appear, Annals of Math." I found no acceptance record.
- **Endorsements:**
  - Séminaire Bourbaki exposé no. 1247 by Bodineau–Gallagher–Saint-Raymond–Simonella (arXiv:2602.04407, to appear in Astérisque).
  - Deng's 2026 Fields Medal (announced 23 Jul 2026), whose citation includes the hard-sphere Boltzmann derivation.
- **Criticism:** I found no mathematical refutation.
  - Shan Gao (arXiv:2504.06297, 7 Apr 2025) calls the work "mathematically rigorous" but argues it covers only a dilute gas.
  - Pulvirenti–Simonella (arXiv:2609.18550, MEMOCS 14 (2026) 143–154) say "Deng, Hani and Ma proved a significant extension", but stress it is conditional, "thus reducing validity to an existence problem". They add that "Hilbert's Sixth Problem should be further specified."
  - A Preprints.org critique (202607.1991) returned 403; I saw it in a snippet only.

## Candidate A

### 1. Stated as open

Quotes I read in the full text:

- **Jabin, KRM 2014** (https://www.math.umd.edu/~tadmor/ki_net/pubs/files/review_MF.pdf): "It is unfortunately fair to say that the mean field limit for α ≥ 1 is mostly not understood at all."
- **Golse notes, arXiv:1301.5494:** "Obtaining rigorous derivations of both models as the mean field limit of large particle systems remains an important open problem."
- **Jabin–Wang review, 2017:** "The rigorous derivation of the Vlasov equation with the Coulomb or Newtonian potential from Newton dynamics is still a major open question in the topic."
- **Serfaty, Duke 2020** (arXiv:1803.08345):
  - Main text: "The non-monokinetic case is even much more challenging and remains open."
  - Duerinckx appendix: "Justifying a complete mean-field result for non-monokinetic solutions in the Coulomb case in dimensions d ≥ 2 remains one of the main open problems in the field."
- **Serfaty, ICM 2018** (arXiv:1712.04095): convergence and propagation of chaos "are not proven in all generality... often truncating the interactions".
- **Serfaty, IRS slides, Jan 2024:** "Coulomb interaction (or more singular) remains open."
- **Miller–Nahmod–Pavlović–Rosenzweig–Staffilani, arXiv:2206.07589:** "it is an outstanding problem to prove the mean-field limit for Vlasov-Poisson, except in dimension one".
- **Golse, arXiv:2201.02005:** "a notoriously difficult and fundamental problem in the kinetic theory of charged particles."
- **Feistl-Held–Pickl, arXiv:2504.01471** (Apr 2025): "its microscopic derivation from systems without cut-off is still an open problem."

Opened through the fetch tool, quotes relayed by it:

- **Feng–Wang, arXiv:2609.08640** (8 Sep 2026): "the cutoff-free mean-field derivation of the three-dimensional Vlasov–Poisson equation remain open."
- **Duerinckx's 2024 course page** (https://mitia.duerinckx.web.ulb.be/teaching-24-Hue.html): "One major open problem in mathematical physics still concerns the justification of the Vlasov-Poisson equation".

### 2. Partial results

The key update is that 2D fell in August 2026, so every "d ≥ 2" quote above is stale for 2D.

| Result | Setting | Covers |
|---|---|---|
| Hauray–Jabin 2007, 2015 | deterministic, no cutoff | force singularity \|x\|^(-α), α < 1 |
| Bresch–Duerinckx–Jabin, arXiv:2402.04695 | deterministic or noise, no cutoff | K in L²_loc |
| Duerinckx–Jabin, arXiv:2608.04104 (4 Aug 2026, v2 15 Aug) | deterministic or noise, no cutoff | K in L^(2d/(d+2γ))_loc, γ < γ*; includes 2D Coulomb |
| Lazarovici–Pickl 2017 | deterministic, 3D Coulomb, both signs | cutoff N^(-1/3+ε) |
| Feistl-Held–Pickl 2025 | deterministic, 3D Coulomb | cutoff N^(-5/12+ε) |
| Serfaty–Duerinckx 2020 | deterministic, Coulomb, no cutoff | monokinetic data only (pressureless Euler–Poisson) |
| Duerinckx–Jabin, arXiv:2605.28979 | 3D Coulomb at high temperature | linearized Vlasov near Gibbs equilibrium only |
| Bresch–Jabin–Soler, APDE 18 (2025) | with noise | 2D, partial 3D, short time |
| Feng–Wang, arXiv:2609.08640 | with noise, 3D Coulomb, no cutoff | short time |
| Jiang–Wu, arXiv:2610.40422 (29 Oct 2026) | with noise, 3D repulsive Coulomb, no cutoff | arbitrary finite time |

- **Duerinckx–Jabin 2D paper:** they call it "the first derivation of the 2D Vlasov-Poisson equation... without any microscopic cutoff". It is an unrefereed preprint.
- **Why 3D Coulomb is not covered (my computation, not a statement in the paper):** in d=3, γ* ≈ 0.170, so the theorem reaches forces up to about |x|^(-1.67); Coulomb is |x|^(-2) and would need γ > 1/2. Their Remark 1.2(b) says the limiting hierarchy "is expected to be well-posed... for γ ≤ 1/2 when α = 0", so 3D Coulomb sits at or beyond the conjectured edge of their method.
- **Sweep for claims:**
  - arXiv API searches through 2 Nov 2026 found no claim of the 3D deterministic no-cutoff case.
  - The four Semantic Scholar citers of the 2D paper are all noisy or monokinetic results.
  - A full-text grep of the OpenAI-manuscripts community index (721 manuscripts, 373 families, https://mathcommunity.github.io/oai-math-revised/) found no Vlasov–Poisson mean-field entry. Its kinetic entries are families 362 (relativistic Vlasov–Maxwell regularity), 363 and 364.

### 3. Standard formulation

- **Mode of convergence:** Lazarovici–Pickl use i.i.d. initial data with law f_0. They prove weak convergence of k-marginals to f_t^(⊗k), and that P[∃t in [0,T]: W_p(μ_N(t), f_t) > N^(-γ)] tends to 0. Duerinckx–Jabin conclude F_(N,k)(t) → f(t)^(⊗k) in distributions.
- **N-body well-posedness (repulsive):** standard by energy conservation (Jabin §2.2): the system "is well posed for any initial data s.t. H_N(t = 0) < ∞".
- **Limit equation:** global strong solutions for compactly supported data are standard (Jabin §1.3; Lions–Perthame is quoted as Theorem 3.1 in Lazarovici–Pickl).
- **Attractive case:** treated separately. Collisions cannot be excluded and the dynamics is only almost-everywhere defined (Jabin §2.3). Cutoff results cover both signs.

### 4. Hilbert-6 link

This is real but thin.

- Zhenfu Wang's seminar slides (12 Oct 2020, read in full): "From Microscopic Descriptions to Macroscopic Descriptions. Part of Hilbert's 6th Problem... Deriving Boltzmann/Landau and Vlasov-type Kinetic Equations", and for K = ±x/|x|³: "Still open!"
- Chaintron–Diez's propagation-of-chaos review (arXiv:2203.00446) motivates the field by Hilbert's Sixth Problem.
- Gorban's theme-issue introduction (arXiv:1803.03599) places Golse's quantum mean-field paper under Hilbert's "limiting processes" clause.
- None of Jabin, Golse, Serfaty or Duerinckx–Jabin mention Hilbert in the texts I searched, and I found no research article saying that the Vlasov–Poisson limit is part of Hilbert 6.

## Alternatives

- **B (Landau, weak coupling):** recognised as open, and no claim found.
  - Pulvirenti–Simonella (arXiv:2005.10679): "A rigorous proof of this fact seems to be hard, even for short times."
  - Feng–Wang (arXiv:2506.14309, Aug 2025): "the rigorous derivation of the Landau equation from Newton dynamics undergoing the weak-coupling limit is still open."
  - Bobylev–Pulvirenti–Saffirio (CMP 2013) is a first-order consistency result only.
  - It is less crisp than A because scaling, ensemble and time interval are conventions. It is the runner-up.
- **C (direct Euler from Hamiltonian particles):** open and explicitly Hilbert-linked, but not a single closed proposition.
  - Kenig calls it "another approach to Hilbert's problem, known as Morrey's program".
  - Pulvirenti–Simonella 2026: "a complete proof remains open and involves hard issues of ergodic theory".
  - The statement needs a choice of potential, local-Gibbs data and an equation of state.
- **D (Boltzmann–Grad for potentials, long time):** now fails criterion (c). OpenAI family 364, "The Boltzmann–Grad limit for stable radial potentials on regular kinetic intervals" (23 Sep 2026), claims it. The community page lists no Lean tag and no verification, only "work in progress". A companion manuscript claims hard-sphere fluctuations.
- **E:** nothing better found. The gravitational 3D case is less crisp, and 2D is claimed.

## Recommended statement

Let f_0 in C_c^∞(R³×R³) be a probability density, and let f be the global classical solution of

∂_t f + v·∇_x f + E·∇_v f = 0, E(t,x) = ∫∫ (x−y)/|x−y|³ f(t,y,w) dy dw, f(0) = f_0.

For N ≥ 2 let (X_i(0), V_i(0)), i = 1..N, be i.i.d. with law f_0, evolving by

X_i' = V_i, V_i' = (1/N) Σ_{j≠i} (X_i − X_j)/|X_i − X_j|³.

Then for every t ≥ 0 and every bounded continuous φ on R⁶,

(1/N) Σ_i φ(X_i(t), V_i(t)) → ∫ φ f(t) dx dv in probability as N → ∞.

Equivalently, the k-particle marginals converge weakly to f(t)^(⊗k).

## Main risks

- **It may be claimed soon.** 2D deterministic fell in Aug 2026 and 3D with noise in Sep–Oct 2026; 3D deterministic is the obvious next target of an active group.
- **Adjudication mismatch.** The Duerinckx–Jabin theorem needs strictly positive f with regularity of log f (their condition f^(-1) in L^∞_loc), so even their 2D result does not literally cover compactly supported f_0. Decide in advance whether a 3D proof for positive Gaussian-tailed data would count.
- **Hilbert link.** A purist can object that Vlasov-type limits are not what Boltzmann "merely indicated"; the support is slides and reviews, not a canonical statement.

## Not opened (snippet or indirect only)

- IMU Fields citation text: read via Wikipedia and a search snippet, not the IMU page.
- Kiessling 2014: known only through Feistl-Held–Pickl's description.
- The 1D results (Trocheris, Hauray): known only through the Miller et al. quote.
- Diez slides' Hilbert mention: snippet only; text extraction found nothing.
