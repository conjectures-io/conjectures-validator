# Hilbert 12 Λ-geometric statement: mathematical fidelity review

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T02:49:05.180Z)

```text
You are an independent mathematical reviewer (algebraic number theory, class field theory, arithmetic geometry). A platform that pays bounties for Lean proofs or refutations of open problems wants to publish one formal statement as a general, precise form of Hilbert's twelfth problem, using the explicitness criterion of Borger and de Smit, "Explicit class field theory and the algebraic geometry of Λ-rings" (arXiv:1809.02295). Review the statement for fidelity to that paper and to Hilbert's problem, and for whether the labelling is honest. Web access is available; today is 2026-11-16.

Read (read-only; do not modify anything, write no files):
- The candidate Lean file: <scratch>/h612/src/HilbertDrafts/Hilbert12Lambda.lean (target: `Hilbert12LambdaGeometric`). It reuses `Ambient`, `IsFiniteAbelian`, `IsAbelianElement`, `FixedValueCoverage` from `Hilbert12.lean` in the same directory.
- The paper: https://arxiv.org/abs/1809.02295 (PDF https://arxiv.org/pdf/1809.02295). A local text extraction is at <scratch>/bds/paper.txt (PDF at the same directory, paper.pdf). Read at least §1 (definitions, Questions Q1–Q3, Theorem 1.4), §4 (Proposition 4.3), §6.1, §7.1, §8 (periodic locus, Theorem 8.4), §10.1 (G_m), §11 (imaginary quadratic, Lattès scheme), §12.1 (K(X), "Λ-geometric", Questions Q4, Q5).

What the Lean statement says, informally: for every number field K there exist a flat separated scheme X of finite type over Spec O_K and commuting O_K-endomorphisms ψ_p for all maximal ideals p, each a Frobenius lift at p, such that the coordinates of the periodic geometric points (points x ∈ X(K̄) for which there is a cycle f with ψ_a(x) = ψ_b(x) whenever a ∼_f b, with ∼_f encoded as in Proposition 4.3) are all abelian over K and generate every finite abelian extension of K.

Questions (answer each with reasons and precise references to the paper):
1. Definitions. Compare, clause by clause, the Lean definitions with the paper's: Λ_{A,P}-structure on a flat scheme with A = O_K and P = all maximal ideals (§1, §6.1); Frobenius lift at p as "induced endomorphism on the fibre agrees with F_p" versus the Lean encoding (ψ over the base; ψ fixes every point of the fibre; for all opens U and all affine opens V ⊆ U ∩ ψ⁻¹(U) and all s ∈ O(U), ψ^♯(s)|_V − (s|_V)^{N(p)} ∈ p·O(V)) — is this encoding equivalent to the paper's condition for flat X? ψ_a as a composite; cycles and f-equivalence via Proposition 4.3 (check the proposition's exact statement and hypotheses: is it stated for all ideals of O_K or only those supported in P, any conditions on a, b?) ; f-periodic points (§8.1 functor of points of X(f)); K(X) = ∪_f K(X(f)(K̄)) (§12.1); "Λ-geometric" (§12.1). Note every discrepancy, including ones that look harmless (for example the Lean statement lets the cycle depend on the point, and takes values of functions on any open neighbourhood of the image point; the paper adjoins "coordinates").
2. Known cases. Does the Lean statement hold for K = Q with X = G_m (§10.1) and for imaginary quadratic K with the Lattès scheme (§11, Theorem 11.12, Proposition 11.3/11.4), as far as you can tell from the paper? In particular: is the Lattès scheme L flat, separated and of finite type over O_K; are the ψ_a morphisms of O_K-schemes that are Frobenius lifts at every prime of O_K (including primes dividing the discriminant or 2, 3, and when the class number exceeds 1); does K(L) contain K^ab (what exactly does the paper prove — is "K^ab is Λ-geometric" for imaginary quadratic K stated explicitly, e.g. in §12.1 examples)? If the formal statement might FAIL for an imaginary quadratic field or for Q for a technical reason, say so: that would make the universal statement cheaply refutable.
3. The universal form. The paper's Question 4 asks whether there are number fields other than Q and imaginary quadratic fields whose maximal abelian extension is Λ-geometric; the Lean target asserts it for every number field. Is the universal statement a fair "Hilbert 12 in the Λ-geometric sense"? Is its truth value unknown in both directions (could the universal statement already be known false, e.g. is there any published obstruction for some field; note Question 5's remark on CM fields and §12.2 "Every example we know ultimately comes from varieties with complex multiplication")? Would you recommend also or instead publishing Question 4 verbatim (existence of one further field)?
4. The redundant clause. The Lean target also requires all periodic-point coordinates to be abelian over K. Theorem 8.4(2) says periodic loci of finite-type Λ-schemes give finite products of abelian extensions when P is Chebotarev dense. Is the clause therefore automatically satisfied, so that including it does not change the question? Any case where it could fail (e.g. the theorem concerns X(f)_fl, the flat part, while the Lean statement uses all K̄-points with ψ_a(x) = ψ_b(x))?
5. Status. Is the general question still open as of today? Check for later work answering it for any number field beyond Q and imaginary quadratic (search citations; in particular look at Yalkinoglu, arXiv:2611.01201 (Oct 2026) on real quadratic fields, Derek Cheng's 2022 ANU thesis on Drinfeld modules/function fields, Uramoto's papers, anything by Borger, de Smit, Gurney). State clearly whether any of these makes either direction of the Lean statement known.
6. Honest labelling. Propose a neutral one-line public title and a 2–3 sentence scope note that make clear (a) this is one published, precise formulation of general explicit class field theory, not the only reading of Hilbert's twelfth problem, (b) it is known for Q and imaginary quadratic fields, (c) the answer is unknown and the authors state no expectation, (d) how it relates to the Stark conjecture route.
7. Any mathematical objection to publishing it as a prove-or-refute bounty target, or changes you would require first.

Report format: a verdict — ACCEPT, ACCEPT WITH CHANGES (list them), or REJECT — then numbered answers. Quote the paper where it matters. Distinguish what you verified by reading from recollection or inference.
```

## Report 1 (2026-11-16T03:03:55.203Z)

**Verdict: ACCEPT WITH CHANGES.** The Lean definitions are a faithful formalisation of Borger–de Smit's "the maximal abelian extension of K is Λ-geometric", and I found no cheap proof or refutation. The changes concern labelling and a companion statement, not the mathematics.

Required changes:
- **Label the universal form as the platform's own.** The paper asks only the existential Question 4; the title must not read "Borger–de Smit conjecture" or plain "Hilbert's twelfth problem".
- **Publish Question 4 verbatim as a companion target** (Lean form in answer 3).
- **Use a scope note** along the lines of answer 6.
- **Fix three docstring points.**
  - Cite Theorem 8.4(2) and Proposition 7.3 for the redundant clause.
  - Say "flat separated Λ-schemes of finite type" instead of defining "Λ-scheme" that way.
  - State that the source is an unrefereed arXiv preprint (v1 only, no journal reference found) and that the imaginary quadratic case rests on Gurney's thesis.

**How I checked.**
- **Read directly:** the paper's TeX source for §1, §4, §6.1, §7, §8, §10.1, §11 (11.1–11.4 and Theorem 11.12; the intermediate 11.5–11.11 only skimmed) and §12; Gurney's thesis (arXiv:1710.08674) §4.3.3–4.3.17; the abstract, introduction and reference list of Yalkinoglu arXiv:2611.01201.
- **Second-hand or partial:** Radchenko–Wheeler and Borger's field-with-one-element paper only through a web summary; Cheng's thesis and the Uramoto papers from the copies already in the scratchpad (abstracts and source searches).
- **Not done:** I did not compile the Lean file.

## 1. Definitions

The Lean class of objects and the paper's agree clause by clause.

- **Scheme class.** The paper's flatness is a standing convention (§6: "Below X will denote a flat A-scheme"; §12.1: "any separated Λ-scheme X of finite type over O_K"). Lean states flat, separated, locally of finite type and quasi-compact explicitly. Same class.
- **Frobenius lift.** §6.1: ψ is a Frobenius lift at p "if the induced endomorphism on the fiber … agrees with F_p". The Lean encoding is equivalent, in both directions, and flatness is not needed.
  - Lean implies paper: ψ is over the base and fixes fibre points, so it is the identity on the fibre's space. For a fibre point x, take affine U containing x and affine V inside U ∩ ψ⁻¹(U) containing x; the congruence gives agreement with the N(p)-th power on V mod p, and such V cover the fibre.
  - Paper implies Lean: for affine V, sections of the fibre over V are O(V)/pO(V), so equality on the fibre gives the congruence.
  - The restriction to affine V is necessary and correct. `OverPrime` correctly picks out the points over p.
- **ψ_a.** Matches §6.1. The list composition is in the reverse order, which commutativity makes irrelevant.
- **Cycles.** Matches §1: a nonzero ideal plus a set of real places, with complex places having exponent 0.
- **f-equivalence.** Proposition 4.3 reads: "Two ideals a, b of O_K are f-equivalent if and only if a = xb for some element x ∈ 1 + f_fin b⁻¹ which is positive at all real places dividing f."
  - It is stated for all ideals of O_K, with no restriction to P and no coprimality condition. Nonzero is implicit, and Lean only applies it to products of primes.
  - I re-derived both directions against the §1 definition; the proposition is correct.
  - The printed proof has a wrinkle: its "By definition … (1)–(3)" step omits the condition gcd(a,f) = gcd(b,f). For K = Q, f = (p), b = (p), a = (1), x = 1/p, conditions (1)–(3) hold but the ideals are not equivalent. The proposition's own criterion rejects this case, so Lean, which uses the statement, is unaffected.
- **Periodic points.** `IsPeriodic` is exactly the functor of points of §8.1 evaluated at K̄.
- **Cycle depending on the point.** This is the paper's union over f in K(X) := ⋃_f K(X(f)(K̄)).
- **"Coordinates".** Values of all local sections at a point form exactly the residue field of its image, embedded in K̄. This is the coordinate-free meaning and the one the paper needs: for the Lattès scheme over O_K it includes the Hilbert class field.
- **Λ-geometric.** §12.1 defines it for any abelian L/K by "L ⊆ K(X)". Lean specialises to L = K^ab.

Remaining discrepancies:
- The redundant abelian clause (answer 4).
- The universal quantifier over K (answer 3).
- The docstring's use of "Λ-scheme" for the restricted class.
- Fixed universe and fixed algebraic closure, both harmless.

## 2. Known cases

Both known cases satisfy the Lean statement.

- **K = Q.** Take G_m with ψ_p(x) = x^p (§10.1: the periodic locus for n∞ "is simply μ_n"). Under the Lean relation, (a) and (b) are equivalent mod (n)∞ exactly when a ≡ b mod n, so periodic points are roots of unity and Kronecker–Weber gives coverage.
- **Imaginary quadratic K.** The paper states it explicitly in §12.1, example (3): "X is P¹_{O_H} with the Lattès Λ-structure. Then K(X) is the maximal abelian extension of K."
  - What is proved: Proposition 11.3 (torsion points form a torsor under the ray class monoid), Proposition 11.4 (the flat periodic locus equals the reduced torsion locus) and Theorem 11.12(1) (its Λ-normalisation is the ray class algebra).
  - Gurney's thesis supplies the geometry, and I confirmed the cited statements. 4.3.10 and 4.3.16 give X ≅ P¹ over O_H, so it is flat, proper and of finite type over O_K. 4.3.9(iii) and 4.3.11 give Frobenius lifts for every prime, with no exclusion for 2, 3, ramified primes or class number above 1. 4.3.13 gives K(X[a]) = K(a).
  - The ψ_a are O_K-morphisms, not O_H-morphisms; they lie over the Artin symbols. Lean asks only for O_K.
- **No technical failure mode.** Lean allows X over O_K[1/N], where conditions at primes dividing N are vacuous. Even if integrality failed at finitely many primes, the statement for these fields would survive.

## 3. The universal form

The universal statement is a fair universal closure of the paper's predicate, but it is not the paper's question.

- **What the paper asks.** Question 4: "Are there number fields other than Q and imaginary quadratic fields for which the maximal abelian extension is Λ-geometric?" The paper's only link to Hilbert is the abstract's phrase "precise questions in the spirit of Hilbert's 12th Problem".
- **Truth value.** I found no published obstruction for any field and no construction beyond the two known cases.
- **The authors' stance.** They state no expectation for Question 4.
  - §1: the questions "allow for negative answers, which would also be interesting".
  - §12.2: "Every example we know ultimately comes from varieties with complex multiplication."
  - §12.1: for CM fields of degree above 2, Shimura's method reaches only a subfield of infinite index, so even there the universal statement needs something beyond CM theory.
- **My own inference, not from the paper.** K·Q^ab is Λ-geometric for every K via G_m with norm-power maps, so the content is the rest of K^ab. For fields with no CM subfield, a negative answer is at least as plausible as a positive one, and in dimension 1 the classification of commuting rational maps leaves only toric, Chebyshev and Lattès structures.
- **Companion statement.** Yes, publish Question 4 as well. A refutation of the universal form by one field would not answer it, and one new field would not prove the universal form. Suggested Lean form:
  `∃ K, finrank ℚ K ≠ 1 ∧ ¬(finrank ℚ K = 2 ∧ IsEmpty (K →+* ℝ)) ∧ LambdaGeometric K`

## 4. The redundant clause

The clause is automatically true and does not change the question.

- Theorem 8.4(2), via Proposition 7.3(2), places the coordinates of f-periodic points in the ray class field K(f). The set of all maximal ideals is Chebotarev dense.
- The flat-part worry does not arise. X(f)_fl is cut out by O_K-torsion, which dies in K̄, so X(f) and X(f)_fl have the same K̄-points.
- With the clause, the statement reads K(X) = K^ab, as in the paper's examples. It adds proof burden only on the "prove" side.

## 5. Status

The question is open as of 2026-11-16; none of the works below makes either direction of the Lean statement known.

- **Yalkinoglu, arXiv:2611.01201** (31 Oct 2026, "preliminary version"). It builds generators of ray class fields of real quadratic fields by Kummer theory over K(ζ_n), using Frobenius eigenvalues of a varying family of Fermat Jacobians. It contains no Λ-scheme and does not cite Borger–de Smit. It says: "The main mystery of whether there is a geometric replacement for elliptic curves with CM for a real quadratic number field remains completely open!"
- **Cheng, ANU thesis 2022.** It proves the analogue for global function fields via rank-1 Drinfeld modules. That is outside the Lean statement, which covers number fields only.
- **Uramoto (Semi-galois categories III and IV, arXiv:2607.28465), Ishitsuka–Uramoto, Yalkinoglu arXiv:2507.23759.** These concern the Deligne–Ribet monoid and algebraic Witt vectors, not finite-type Λ-schemes.
- **Borger, de Smit, Gurney.** Nothing later by them addresses Question 4.

## 6. Labelling

**Title:** "Is the maximal abelian extension of every number field generated by the periodic points of one finite-type Λ-scheme? (universal form of Borger–de Smit, Question 4)"

**Scope note:** "This is one precise published formulation of general explicit class field theory 'in the spirit of Hilbert's 12th Problem' (Borger–de Smit, arXiv:1809.02295, §12.1). It is algebraic-geometric rather than Hilbert's analytic formulation, and it is not the only modern reading. It is known for Q (the multiplicative group) and for imaginary quadratic fields (the Lattès scheme of CM elliptic curves). The authors ask only whether any further field has the property and state no expectation; the universal form is this platform's strengthening, its answer is unknown, and one field with no such scheme would refute it. It is logically independent of the Stark-conjecture route (generators from special values of L-functions, including Brumer–Stark units for totally real fields): results there neither settle nor are settled by this statement."

## 7. Objections

I have no mathematical objection to a prove-or-refute target once the changes above are made. Three points should be visible to participants.

- **Asymmetry.** A proof must construct a scheme for every number field, and so must also formalise Kronecker–Weber and CM theory. A refutation needs an obstruction for one field.
- **Explicitness caveat.** The paper notes in §8.6 that without an algorithm for periodic loci "our approach to generating abelian extensions would not be so explicit".
- **Optional regression test.** Prove in Lean that G_m over 𝓞 ℚ with power maps is a `LambdaStructure` whose (n)∞-periodic points are the n-th roots of unity. This would exercise the Frobenius-lift and equivalence encodings.

**Files and sources.**
- Lean target: `<scratch>/h612/src/HilbertDrafts/Hilbert12Lambda.lean`
- Paper source read: `<scratch>/bds/tex/v32.tex`
- Web: https://arxiv.org/abs/1809.02295, https://arxiv.org/abs/1710.08674, https://arxiv.org/abs/2611.01201, https://arxiv.org/abs/2609.21892, https://arxiv.org/abs/0906.3146
