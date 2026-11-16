# Hilbert 6 mean-field statement: adversarial Lean-semantics review

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T02:49:35.668Z)

```text
You are an adversarial reviewer for a platform that pays cash bounties for Lean 4 proofs OR refutations of formal statements of open problems. If a published statement can be proved or refuted cheaply because of a formalization defect (vacuous or unsatisfiable hypothesis, junk value from a totalized function, wrong quantifier, degenerate case, a definition that does not mean what its docstring says), the platform loses money and credibility. Find such a defect in the candidate statement below before publication, or report that you could not.

First make your own working copy and work ONLY inside it (other processes are editing the original):
`cp -r <scratch>/h612 <scratch>/h612-review-l6`
Then read, in the copy:
- `src/HilbertDrafts/Hilbert6MeanField.lean` — the candidate. The reward target is the proposition `HilbertDrafts.Hilbert6MeanField.VlasovPoissonMeanFieldLimit`.
- `src/HilbertDrafts/Hilbert6.lean` — earlier reviewed definitions it reuses (`Space`, `Phase`, `Configuration`, `empiricalAverage`, `InitialDensity`).
Ignore the `*Checks.lean` files (work in progress by someone else, may contain `sorry`). In the copy, `./compile.sh <path relative to src>` compiles a Lean file under `src/` inside Docker (Lean 4.35.0-rc2, pinned Mathlib, offline, warnings are errors, 30–120 s). Mathlib sources are inside the image at /opt/fc-verifier/vendor/formal-conjectures/.lake/packages/mathlib/Mathlib (read with `docker run --rm --network none --entrypoint bash sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90 -c '...'`). Write experiments as new files in your copy.

Intended informal meaning: N+1 point particles in R^3 with positions X_i and velocities V_i obey Newton's equations X_i' = V_i, V_i' = (1/(N+1)) Σ_{j≠i} (X_i − X_j)/|X_i − X_j|^3 (repulsive Coulomb force, mean-field scaling, no truncation, no noise). Initial (X_i, V_i) are i.i.d. with a smooth compactly supported probability density f_0 on R^3×R^3. Let f be the global classical solution of the Vlasov–Poisson equation ∂_t f + v·∇_x f + E·∇_v f = 0, E(t,x) = ∫∫ (x−y)/|x−y|^3 f(t,y,w) dy dw, f(0) = f_0. Claim: for every t ≥ 0, every continuous compactly supported φ and every δ > 0, the probability that |(1/(N+1)) Σ φ(X_i(t),V_i(t)) − ∫ φ f(t)| ≥ δ tends to 0 as N → ∞. This is believed true and is a well-known open problem; both a proof and a refutation are meant to be hard. The statement also asserts existence of the classical solution (a known theorem: Pfaffelmoser, Lions–Perthame, Schaeffer), to prevent vacuity.

Attack it. Concretely:
1. Unfold every definition (`SmoothDensity`, `coulomb`, `acceleration`, `IsCoulombTrajectory`, `meanField`, `IsVlasovPoissonSolution`, `productLaw`, `deviationSet`, `VlasovPoissonMeanFieldLimit`, and the reused ones) down to Mathlib notions and check each means what its docstring says: norms and measures on `EuclideanSpace ℝ (Fin 3)` and on the product `Phase` (which norm? which volume? is `volume` on `Phase` and on `Fin (N+1) → Phase` the product Lebesgue measure with the expected normalization, so that `productLaw` is the law of N+1 independent samples with density f_0 and total mass 1?), `HasDerivAt` for curves, `fderiv` of the joint function at a point applied to the direction `(1, (v, E))` (is this ∂_t f + v·∇_x f + E·∇_v f?), `ContDiff ℝ 1` of the joint function, Bochner integrals returning 0 when not integrable, `withDensity` with `ENNReal.ofReal`, outer measure of a possibly non-measurable set, `Tendsto … atTop (𝓝 0)` in ℝ≥0∞.
2. CHEAP REFUTATION? The target is a conjunction over all admissible f_0 of (existence of a solution) ∧ (convergence). It is cheaply refutable if the existence part is FALSE under the formal definition of `IsVlasovPoissonSolution`. Check every clause against the true classical solution for smooth compactly supported data, on ALL of time t ∈ ℝ (negative times included): joint C^1 regularity on ℝ × Phase; nonnegativity; "compact support uniformly on bounded time intervals" as formalized (which norm on Phase? is it satisfiable?); integrability of z ↦ f(t,z) • coulomb(x − z.1) over the six-dimensional phase space for every t and x (the kernel is singular like |x−y|^{-2}); the PDE identity at every (t,x,v) including where the totalized `coulomb 0 = 0` matters; the initial condition. Also check the particle side: do global-in-time trajectories (all t ∈ ℝ) exist for almost every initial configuration under `IsCoulombTrajectory` (distinct positions at all times, two-sided derivatives everywhere); could the set of initial configurations with NO trajectory have positive probability for a formal reason (then the deviation set would have probability bounded below and the convergence claim would be false trivially)? Is the acceleration formula the intended one for N+1 particles (sum over j ≠ i, factor 1/(N+1))? Could non-uniqueness of trajectories inflate the deviation set (it uses "∃ trajectory with large deviation")?
3. CHEAP PROOF? Is the convergence claim trivially true for a formal reason: is `SmoothDensity` unsatisfiable (note the smoothness clause is `∀ n : ℕ, ContDiff ℝ n f₀`; check how `ContDiff ℝ (n : WithTop ℕ∞)` behaves in this Mathlib version and that it is not analyticity); is `IsVlasovPoissonSolution` with `f 0 = f₀` satisfiable only by junk; is `productLaw` the zero measure or does it give measure 0 to everything for a formal reason; is `deviationSet` always empty or always null; does `empiricalAverage` do what it says; is the limit `∫ y, φ y * f t y` the right quantity; is the quantifier order right (for all f_0, f, t, φ, δ: limit in N)?
4. Does the formal statement differ from the informal one in a way that changes the mathematical problem (stronger, weaker, different)? In particular: test functions continuous with compact support versus bounded continuous; convergence for each fixed t rather than uniformly on [0,T]; all t ≥ 0; data class C^∞ with compact support; trajectories required on all of ℝ.
5. Anything else a hostile bounty hunter would try.

Report format: VERDICT — one of DEFECT FOUND (give the concrete cheap proof/refutation, ideally machine-checked in your copy), FIDELITY ISSUE (say how the statement differs and whether it matters), or NO DEFECT FOUND. Then one or two lines per attack: what you tried, why it fails or succeeds, and whether you verified it by compiling or on paper. Then residual risks you could not rule out. Do not soften findings.
```

## Report 1 (2026-11-16T03:11:52.505Z)

**VERDICT: NO DEFECT FOUND.** I found no cheap proof or cheap refutation, and no fidelity gap that changes the problem. The formal statement is equivalent to the 3D repulsive Coulomb mean-field limit for i.i.d. C_c^∞ data, conjoined with the known global-existence theorem.

All Lean checks compile (warnings as errors) against the candidate rebuilt from source in my copy; the sources were still identical to the original when I finished. The target depends only on `propext`, `Classical.choice`, `Quot.sound`. The steps that rest on paper arguments are marked as such.

## 1. Definitions unfolded

- **Norms and measures (compiled):** `volume` on `Phase` is `volume.prod volume` by `rfl`, and on `Configuration (N+1)` it is `Measure.pi fun _ => volume` by `rfl`. `volume` on `Space` is Lebesgue (`PiLp.volume_preserving_ofLp`). The norm on `Phase` is `max ‖z.1‖ ‖z.2‖`, which is harmless for the support clause.
- **`productLaw` (compiled):** it equals `Measure.pi` of the one-particle law `volume.withDensity (ofReal ∘ f₀)` (`productLaw_eq_pi`) and has total mass 1 (`productLaw_univ`). `ENNReal.ofReal` clips nothing because `f₀ ≥ 0`.
- **PDE clause (compiled):** for differentiable `F`, `fderiv ℝ F (t,(x,v)) (1,(v,E))` equals `∂ₜF + ∇ₓF·v + ∇ᵥF·E` (`fderiv_split`). Clause 1 forces C¹, so the junk value `fderiv = 0` is unavailable.
- **`HasDerivAt` for curves (compiled):** the candidate elaborates it through `WithLp` instance paths, but standard normed-space lemmas close such goals by `exact`, so it is the ordinary derivative.
- **Totalized values (paper, except clause 4):** `coulomb 0 = 0` only matters on a null set. Every Bochner integral in the target is of a genuinely integrable function. Trajectories use `HasDerivAt`, not `deriv`.
- **Outer measure (paper):** the deviation set is Borel (a closed null set union a relatively closed subset of its open complement), so nothing odd happens.

## 2. Cheap refutation

- **Existence clause false? No (paper).** The Pfaffelmoser / Schaeffer / Lions–Perthame solution satisfies every clause on all of ℝ. Negative times follow from `f(−t,x,v) = g(t,x,−v)`; I checked that the time derivatives match at `t = 0`, so the glued function is jointly C¹. Support is compact uniformly on bounded time intervals.
- **Integrability clause (compiled):** `coulomb` is locally integrable on `Space`, and `z ↦ g z • coulomb (x − z.1)` is integrable for every continuous compactly supported `g` and every `x` (`integrable_force`). Clause 4 is therefore implied by clauses 1 and 3; it is redundant and harmless.
- **Extra junk solutions? No (paper).** The second conjunct quantifies over all formal solutions, so the statement implies uniqueness. Formal solutions are exactly classical compactly supported solutions, and the standard Gronwall argument on characteristics gives uniqueness in both time directions.
- **No-trajectory set with positive probability? No.** The coincident-position set is `productLaw`-null (compiled, `productLaw_coincide_null`). Every configuration with distinct positions has a global two-sided trajectory (paper): the energy `½Σ|Vᵢ|² + (N+1)⁻¹Σ_{i<j}|Xᵢ−Xⱼ|⁻¹` is conserved, which bounds distances below and speeds above.
- **Non-uniqueness inflating the deviation set? No (paper).** The vector field is locally Lipschitz on the open set of distinct positions.
- **Acceleration formula (compiled):** it is the intended one. Time reversal preserves `IsCoulombTrajectory`, the two-particle forces sum to zero, and the force is repulsive.

## 3. Cheap proof

- **`SmoothDensity` unsatisfiable? No (compiled).** A normalized `ContDiffBump` on `Phase` is a witness. The cast in `ContDiff ℝ n f₀` is `Nat.cast` into `WithTop ℕ∞`, a finite order; `(∀ n, ContDiff ℝ n f) ↔ ContDiff ℝ ∞ f` compiles. It is not analyticity.
- **`productLaw` zero or `deviationSet` trivially null or full? No.** At `t = 0` the claim reduces to the weak law of large numbers, which is consistent.
- **`empiricalAverage`, the limit `∫ φ·f(t)`, the quantifier order:** all as intended.
- A proof must also establish global existence for every smooth `f₀`, so no shortcut exists.

## 4. Fidelity

None of these differences changes the problem.

- Continuous compactly supported tests are equivalent to bounded continuous ones, because the limit is a probability measure.
- Fixed-time convergence is weaker than uniform on `[0,T]`, but it is the standard qualitative statement and is still open.
- Trajectories and solutions on all of ℝ are equivalent to the forward-time versions by reversibility.
- The existence conjunct makes the "prove" side strictly harder than the open problem: global existence must be formalized. That is not cheaper.

## 5. Residual risks

- **Paper-only steps.** Not machine-checked: that the true solution lies in the formal class, uniqueness of formal solutions, and global existence and uniqueness of N-body trajectories. If `IsVlasovPoissonSolution` were unsatisfiable for some admissible `f₀`, the existence conjunct would turn that into a cheap refutation rather than prevent a loss. I found no such gap.
- **Truncation results can decide it.** The module docstring says results with a truncated interaction do not decide the statement. That is inaccurate in one regime: energy conservation keeps all pair distances above roughly `c(f₀)/N²` with probability tending to 1. A 3D repulsive cutoff result with radius `o(N⁻²)` would therefore imply the statement by a short argument. The best 3D cutoff I found is `N^(−5/12+σ)` (Feistl-Held–Pickl 2025), so this is not exploitable now.
- **Open status.** Duerinckx–Jabin derive 2D Vlasov–Poisson with no cutoff, for kernels in `L^{2−η}_loc`, which excludes 3D Coulomb (that needs `p < 3/2`). I found no 3D no-cutoff result, but the search was brief and the area is moving.

## Files

Working copy: `<scratch>/h612-review-l6`

Check files in `<scratch>/h612-review-l6/src/Rev/`:
- `Sanity.lean`
- `Indep.lean`
- `Null.lean`
- `Force.lean`
- `Reverse.lean`
- `Axioms.lean`

Sources:
- [Duerinckx–Jabin, Derivation of 2D Vlasov-Poisson for classical particles with Coulomb interactions](https://mitia.duerinckx.web.ulb.be/publications-DJ26b.pdf)
- [Feistl-Held–Pickl, On the mean-field limit for the Vlasov-Poisson system (arXiv:2504.01471)](https://arxiv.org/pdf/2504.01471)
- [Feistl-Held–Pickl, 2D version (arXiv:2509.17821)](https://web3.arxiv.org/abs/2509.17821)
- [Chen–Jung–Pickl, mean-field limit of Vlasov-Poisson-Fokker-Planck (arXiv:2505.13038)](https://arxiv.org/html/2505.13038v1)
- [Lazarovici, Vlasov-Poisson as the mean field limit of extended charges (arXiv:1502.07047)](https://arxiv.org/pdf/1502.07047v2)
