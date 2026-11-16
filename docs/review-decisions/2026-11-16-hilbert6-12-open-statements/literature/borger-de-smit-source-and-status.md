# Borger–de Smit: source text and status of the question

Model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-16T02:22:42.365Z)

```text
Research task with web access (today is 2026-11-16). I need exact, quotable mathematical content from one paper and the current status of the question it raises. Accuracy matters more than speed: a formal Lean statement will be written from your report and money will be attached to it, so quote definitions and statements verbatim and say plainly when you could not read something.

Primary source: James Borger and Bart de Smit, "Explicit class field theory and the algebraic geometry of Λ-rings" (arXiv:1809.02295; try https://arxiv.org/abs/1809.02295 , https://arxiv.org/pdf/1809.02295 , and any HTML version; also find the journal reference if it was published). Related earlier papers by the same authors: "Galois theory and integral models of Λ-rings" (arXiv:0801.2352) and "Lambda actions of rings of integers" (arXiv:1105.4662).

My recollection, which you must verify or correct: for a number field K with ring of integers O_K, a Λ-structure on a flat O_K-algebra A is a commuting family of O_K-algebra endomorphisms ψ_p, one for each maximal ideal p, with ψ_p(x) ≡ x^{N(p)} mod pA. Finite flat reduced Λ-rings are classified by the Deligne–Ribet (ray class) monoid, the "ray class algebras" contain the ray class fields, and the paper asks whether the ray class algebras can be obtained as "periodic loci" of a Λ-scheme of finite type — a precise form of explicit class field theory / Kronecker's Jugendtraum / Hilbert's twelfth problem — proving this for Q (via G_m) and for imaginary quadratic fields (via CM elliptic curves / Lattès-type maps), and leaving general number fields open.

Please report, with verbatim quotes and section/theorem/definition numbers:
1. The abstract, and the journal reference if published.
2. The exact definition of a Λ-structure (Λ_P-structure) on an O_K-algebra / scheme used in the paper, including the flat (torsion-free) case description as commuting Frobenius lifts, and any extra conditions (e.g. which set P of primes, what happens in the non-flat case).
3. The exact definitions of: the Deligne–Ribet monoid DR_f (and DR), the ray class algebra(s), the "periodic locus" (with its parameters, e.g. conductor f and any period r), and "Λ-scheme of finite type".
4. The exact statements of the main theorems, in particular the ones for K = Q and K imaginary quadratic (what scheme, what Λ-structure, what the periodic loci are, and how class number > 1 is handled).
5. MOST IMPORTANT: every place where the paper poses the general-number-field case as a question, problem, conjecture or expectation. Quote these verbatim with location. What exactly is asked: existence of a Λ-scheme of finite type whose periodic loci ARE the ray class algebras? or whose periodic points GENERATE the ray class fields / K^ab? over O_K or after inverting something? Is a weaker "generation" form stated? Do the authors state what they expect the answer to be, or prove any obstruction/negative result (for example for abelian varieties or tori when K is not Q or imaginary quadratic)?
6. Whether the paper relates this explicitly to Hilbert's twelfth problem or Kronecker's Jugendtraum (quote).
7. Status since then, through November 2026: search for papers citing it (Google Scholar/Semantic Scholar/OpenAlex/arXiv) that answer, partially answer, or reformulate the question for non-CM number fields — e.g. work of Lance Gurney, Bora Yalkinoglu (Bost–Connes arithmetic subalgebras via Λ-rings), James Borger, Bart de Smit, or others; any negative results (non-existence for real quadratic fields) or positive results; any 2024–2026 AI-generated claims. State clearly whether the general question is open as far as you can tell.
8. Anything in the paper that would make a naive formalization wrong (e.g. the authors work with the Witt-vector/δ-structure definition in the non-flat case, require separatedness, consider only primes away from a finite set, use "periodic" in a specific sense tied to the ray class monoid, etc.).

Report format: numbered answers matching the eight items, verbatim quotes in quotation marks with locations, then a short list of things you could not verify. Distinguish what you read in the source from recollection. Do not write any files.
```

## Report 1 (2026-11-16T02:38:31.760Z)

# Borger–de Smit, "Explicit class field theory and the algebraic geometry of Λ-rings": verbatim content and status

**Bottom line.** The general-number-field case is posed only as yes/no questions (Q1–Q7), with no conjectured direction and no obstruction proved. I found no answer for any number field other than Q and imaginary quadratic fields through 16 Nov 2026.

**What I read.** The full arXiv v1 TeX source of 1809.02295 (the only version), with numbering cross-checked against text extracted from the arXiv PDF. The author-hosted PDF is textually the same, as far as I compared. I also read the sources of 0801.2352 and 1105.4662.

**Transcription convention.** Quotes are from the TeX with macros expanded. Fraktur letters are written plain (p, q, a, b, d, f, r). M_K is the set of maximal ideals of O_K. O(…) with parentheses is a function algebra; O_K, O_H are rings of integers. X(f) is the periodic locus and R_{A,P}(f) the ray class algebra.

Local copies: `<scratch>/bds/tex/v32.tex` and `<scratch>/bds/paper.txt`. Nothing was written to the repo.

## 1. Abstract and journal reference

"We consider generalized Λ-structures on algebras and schemes over the ring of integers O_K of a number field K. When K=Q, these agree with the λ-ring structures of algebraic K-theory. We then study reduced finite flat Λ-rings over O_K and show that the maximal ones are classified in a Galois theoretic manner by the ray class monoid of Deligne and Ribet. Second, we show that the periodic loci on any Λ-scheme of finite type over O_K generate a canonical family of abelian extensions of K. This raises the possibility that Λ-schemes could provide a framework for explicit class field theory, and we show that the classical explicit class field theories for the rational numbers and imaginary quadratic fields can be set naturally in this framework. This approach has the further merit of allowing for some precise questions in the spirit of Hilbert's 12th Problem. In an interlude which might be of independent interest, we define rings of periodic big Witt vectors and relate them to the global class field theoretical mathematics of the rest of the paper."

- **Journal reference:** none found. The arXiv record is v1 only (7 Sep 2018; math.NT, math.AG, math.KT; 37 pp.) with no journal-ref field, and v2 returns 404.
- zbMATH lists it as "Preprint, arXiv:1809.02295", OpenAlex as a preprint, and Crossref has no match.
- Citing papers from 2025–2026 (Yalkinoglu, Uramoto, Ishitsuka–Uramoto) still cite the arXiv number.
- First-page footnote: "The first part of this paper is a revision of our note [6], which has had some circulation since 2006 and has been used by Yalkinoglu [24] and Uramoto [21]." Here [6] is arXiv:1105.4662.
- **Earlier papers:** "Galois theory and integral models of Λ-rings" is Bull. Lond. Math. Soc. 40(3):439–446, 2008 (DOI 10.1112/blms/bdn024). "Lambda actions of rings of integers" is arXiv-only; its arXiv comment calls it "a preliminary version dating from 2006".

## 2. Definition of a Λ-structure

**Setting (§1, p. 2).** "Let A be a Dedekind domain with fraction field K. Let P be a set of maximal ideals of A such that for each p∈P, the residue field k(p)=A/p has finite cardinality N(p). We will be most interested in the case where K is a number field or local field, A is the full ring of integers O_K of K, and P is the set M_K of all maximal ideals of O_K."

**Frobenius lift (§1).** "Let B be a (commutative) A-algebra. Then for each p∈P the algebra B/pB = B⊗_A k(p) over k(p) has a natural k(p)-algebra endomorphism F_p: x↦x^{N(p)}, which is called the Frobenius endomorphism. By a Frobenius lift on B at p we mean an A-algebra endomorphism ψ_p: B→B such that ψ_p⊗k(p)=F_p."

**Λ-structure on an algebra (§1).** "We define a Λ_{A,P}-structure on B to be a set map P→End_{A-alg}(E), denoted p↦ψ_p, such that (1) ψ_p is a Frobenius lift at p for each p∈P. (2) ψ_p∘ψ_q=ψ_q∘ψ_p for all p,q∈P. By a Λ_{A,P}-ring we mean an A-algebra with Λ_{A,P}-structure. (In fact, this definition of Λ_{A,P}-structure is well-behaved only when B is torsion free as an A-module, but since all the Λ_{A,P}-rings in this paper have that property, we will use the simple definition given above. For the general one, see [3].)"
- "End_{A-alg}(E)" is a typo for B.
- [3] is Borger, "Basic geometry of Witt vectors I".

**Vacuous case (§1).** "Observe that if p satisfies B⊗_A k(p)=0, then the lifting condition (1) is vacuous. In particular, if B is an algebra over K, then any commuting collection of K-automorphisms of B indexed by the maximal ideals of P is a Λ_{A,P}-structure on B."

**Schemes (§6, p. 15).** "Below X will denote a flat A-scheme." Then §6.1: "An endomorphism ψ∈End_A(X) is said to be a Frobenius lift at p if the induced endomorphism on the fiber X×_{Spec A} Spec k(p) agrees with F_p. A Λ_{A,P}-structure on a flat A-scheme X is defined to be a set map P→End_A(X), denoted p↦ψ_p such that ψ_p is a Frobenius lift at p for each p∈P and such that ψ_p∘ψ_q=ψ_q∘ψ_p for all p,q∈P. We will call an A-scheme with Λ_{A,P}-structure a Λ_{A,P}-scheme. (When X is not flat over A, this definition still makes sense; but as in the affine case, it is not well behaved. In general, one should define it to be an action of the Witt vector monad W^*_{A,P} as in the introduction to [4]. We will only consider Λ-structures on flat schemes in this paper; so the simplified definition above is good enough here.)"

**Composite operators (§6.1).** "For any ideal a∈Id_P with prime factorization a=p_1⋯p_n, let ψ_a denote the composition ψ_{p_1}∘⋯∘ψ_{p_n}."

Your recollection of the definition is correct for P = M_K in the torsion-free case. The paper never works with the non-flat definition; it only points to it.

## 3. Deligne–Ribet monoid, ray class algebra, periodic locus, finite type

**Id_P (§1).** "Let Id_P denote the set of non-zero ideals of A divisible only by the primes in P. It as a monoid under ideal multiplication, the free commutative monoid on the set P."

**Cycle (§1, p. 3).** "A cycle of K is a formal product f=∏_p p^{n_p}, where the product ranges over all primes of K, both finite and infinite, all n_p are non-negative integers, only finitely many of which are non-zero, and we have n_p∈{0,1} for real primes p, and n_p=0 for complex primes p. The finite part of f is f_fin=∏_{p<∞} p^{n_p}, which can be viewed as an ideal of A."

**DR_P(f) (§1, p. 3).** "For a cycle f, we say that two integral ideals a,b are f-equivalent, and write a∼_f b, if the following two conditions are satisfied: (1) a and b have the same greatest common divisor d with f_fin (2) ad^{-1} and bd^{-1} represent the same class in the ray class group Cl(fd^{-1}) of conductor fd^{-1}. This is an equivalence relation, and we write DR_P(f) for the quotient Id_P/∼_f. Because ∼_f is preserved by multiplication of ideals, DR_P(f) inherits a unique monoid structure from Id_P. We call it the ray class monoid (or Deligne–Ribet monoid) of conductor f supported at P. It was introduced in Deligne–Ribet [11] in the case where P=M_K and every real place divides f."

**Equivalent form (Prop. 4.3).** "Two ideals a,b of O_K are f-equivalent if and only if a=xb for some element x∈1+f_fin b^{-1} which is positive at all real places dividing f."

**Structure (§4.1).** There is a bijection ∐_{d∈Id_P, d|f} Cl_P(fd^{-1}) → DR_P(f), [a]↦[da], and "DR_P(f)^* = Cl_P(f)".

**The limit.** The paper has no symbol "DR" or "DR_K"; it writes "the profinite monoid lim_f DR_P(f)". For P=M_K, (4.5.16) gives lim_f DR_P(f) = Ô_K^∘ ⊕_{Ô_K^*} G_K^ab. For K=Q it is "the monoid Ẑ^∘ of all profinite integers under multiplication".

**Chebotarev dense (§1).** "Let us say that P is Chebotarev dense if any element of any ray class group Cl(f) can be represented by an ideal supported at P, or equivalently by infinitely many such ideals. For example, by Chebotarev's theorem, any set P consisting of all but finitely many maximal ideals is Chebotarev dense."

**Integral model (§1).** "…an integral Λ_{A,P}-model, by which we mean a sub-Λ_{A,P}-ring B⊆E which is finite flat as an A-module such that the induced map K⊗_A B→E is a bijection."

**Maximal Λ-order (§2.1).** "A sub-A′-algebra B⊆E with a Λ_{A,P}-structure is said to be a Λ_{A,P}-order (over A′) if it is finite over A′. We say it is maximal (in E) if it contains all others."

**Ray class algebra (§7.4).** "We can view DR_P(f) as a pointed DR_P(f)-set: the distinguished point is the identity element, and the action is translation. By theorem 5.5, the corresponding Λ_{A,P}-ring over K has an integral model. Define R_{A,P}(f), the ray class algebra of conductor f, to be the maximal Λ_{A,P}-order in this K-algebra. Thus we have K⊗_A R_{A,P}(f) = ∏_{d∈Id_P, d|f_fin} K(fd^{-1}), where K(fd^{-1}) is the ray class field of K with conductor fd^{-1}."
- Also §7.4: "Observe that the ray class algebra is not usually a domain, and in particular the map β: R_{A,P}(f)→K(f) to the ray class field is not usually injective. Also, unlike the ray class field, it depends not only on f but also on A and P."
- So K(f) is a direct factor of K⊗R, not a subring of R.
- Prop. 9.8 gives R_{A,P}(f) ≅ W^{(f)}_{A,P}(A^int), the f-periodic Witt vectors of the integral closure.

**f-periodic (§7.1).** "We will say that a Λ_{A,P}-scheme X is f-periodic if for all f-equivalent ideals a,b∈Id_P, the two maps ψ_a,ψ_b: X→X are equal—in other words, the monoid map Id_P→End_A(X) factors through DR_P(f)."

**Periodic locus (§8, §8.1).** "Let f be a cycle on K. Let X be a separated (flat) Λ_{A,P}-scheme." Then: "Define the f-periodic locus X(f) of X to be the scheme-theoretic intersection X(f) = ⋂_{a,b∈Id_P, a∼_f b} X(ψ_a=ψ_b), where X(ψ_a=ψ_b) denotes the equalizer of the two maps ψ_a,ψ_b: X→X. Since X is separated, X(f) is a closed subscheme. The functor X(f) represents is X(f)(C) = {x∈X(C): ψ_a(x)=ψ_b(x) for all a,b∈Id_P with a∼_f b}. We emphasize that X(f) depends on P as well as f, although the notation does not reflect this."
- Flat part (§8.1): "let X(f)_fl denote the maximal A-flat subscheme of X(f). … Although the actual periodic locus X(f) is more fundamental than X(f)_fl, for the purposes of this paper it is enough to consider X(f)_fl, and doing so will allow us to avoid the subtleties of Λ-rings with torsion."
- The only parameter is the cycle f, including its real places. There is no separate period.
- The letter r is a fixed cycle giving the type away from P (§1, p. 5): "fix a cycle r supported away from P, and let Z(P,r) denote the set of cycles f which agree with r away from the primes in P. We will refer to the triple (A,P,r) as the context."

**Torsion locus (§8.3), a different object.** "X[f] = ψ_{f_fin}^{-1}(X(f_∞))", with X(f)⊆X[f]. For G_m: "if f=(n), then the f-torsion locus is again μ_n, but the f-periodic locus G_m(f) is μ_m, where m=gcd(2,n)."

**"Λ-scheme of finite type".** This is not separately defined. It means a flat, separated Λ_{A,P}-scheme whose underlying scheme is of finite type over A.
- Thm 1.3: "a Λ_{A,P}-scheme of finite type over A, as above".
- p. 5: "any Λ_{A,P}-scheme X of finite type (still flat and separated)".
- §12.1: "any separated Λ-scheme X of finite type over O_K".

## 4. Main theorems

**Thm 1.2 (classification).** "Suppose that K is a number field and that P is Chebotarev dense. Let E be a finite étale K-algebra with a Λ_{A,P}-structure, and let S be the set of K-algebra maps from E to K^sep. Then E has an integral Λ_{A,P}-model if and only if there is a cycle f of K such that the action of G_K×Id_P on S factors (necessarily uniquely) through the map G_K×Id_P→DR_P(f) above."

**Cor. 5.6.** "If P is Chebotarev dense, there is a contravariant equivalence between the category of finite étale Λ_{A,P}-rings over K which admit an integral model and the category of finite discrete sets with a continuous action of the profinite monoid lim_f DR_P(f)."

**Thm 1.3.** "Let X be a Λ_{A,P}-scheme of finite type over A, as above, and assume P is Chebotarev dense. Then possibly after inverting some primes, X(f) is an affine Λ_{A,P}-scheme which is reduced and finite flat over A."

**Thm 8.4.** "If X is of finite type over A and P is Chebotarev dense, then we have the following: (1) The flat f-periodic locus X(f)_fl is a closed f-periodic sub-Λ_{A,P}-scheme of X, and it is the maximal flat closed subscheme with these properties. (2) We have X(f)_fl=Spec B, where B is a finitely generated A-algebra, and K⊗_A B is a finite product of abelian extensions of K of conductor dividing f. (3) If X is proper, then B is a finite A-algebra."

**Thm 1.4 (main).** "The answer to (Q3) is positive in the following contexts (A,P,r) with the Λ_{A,P}-schemes X: (1) (Z,M_Q,∞) and X=G_{m/Z} with the Λ_{Z,M_Q}-structure above (2) (Z,M_Q,(1)) and X=A^1_Z with the Chebyshev Λ_{Z,M_Q}-structure (3) (O_K,M_K,(1)), where K is imaginary quadratic with Hilbert class field H, and X is P^1_{O_H}, viewed as a scheme over O_K, with the Lattès Λ_{O_K,M_K}-structure defined in (11.2)."

**K = Q, toric line (§10.1).** ψ_p(x)=x^p on G_m=Spec Z[x^{±1}].
- "For each cycle of the form (n)∞, with n≥1, the periodic locus G_m(n∞) is simply μ_n=Spec Z[x]/(x^n−1)."
- §7.4: "the ray class algebra of conductor (n)∞ is Z[x]/(x^n−1)". This is the second theorem of the 2008 paper: "The Λ-ring Z[μ_r] is the maximal integral Λ-model of Q⊗Z[μ_r]."
- So the stronger (Q2) holds here.

**K = Q, Chebyshev line (§10.2).** Y=Spec Z[y], y=x+x^{-1}.
- Cor. 10.4: "The map O(Y(n))→R_{Z,P}(n) to the ray class algebra given by the point ζ_n+ζ_n^{-1}∈Y(n)(O_Q̄) is injective. If n is odd, it is surjective; if n is even, its cokernel is a group of order 2".
- So (Q2) fails for this X, and only (Q3) holds.

**K imaginary quadratic (§11).** The scheme is the Lattès scheme L, the coarse space of the universal CM elliptic curve over the moduli stack M_CM.
- The operators are ψ_a: (E,x)↦(a^{-1}⊗E, 1⊗x), using the Serre tensor product.
- (11.2.31): "L×_{Spec(O_H)}Spec(R) = E/O_K^*" for a CM elliptic curve E over R.
- Thm 11.12: "Let O(L(f)_fl)^∼ denote the maximal Λ-order over O_K in K⊗_{O_K}O(L(f)_fl). Then (1) O(L(f)_fl)^∼ is isomorphic as a Λ-ring to the ray class algebra R_{O_K,P}(f). (2) The map (O(L(f)_fl))_f → (O(L(f)_fl)^∼)_f is an isomorphism of pro-rings. (3) If a prime number divides the index [O(L(f)_fl)^∼ : O(L(f)_fl)] then it divides the order of the unit group O_K^*."
- Prop. 11.4: "L(f)_fl = L(f)_red = L[f]_red".
- Prop. 11.3(2): L[f](C) is a DR_P(f)-torsor.

**Class number > 1 (§11.2).** It is handled by working over O_K with a scheme that lives over O_H.
- "L(C) is the union of h_K copies of P^1(C)".
- "Gurney (4.3.16 of [14]) proves there exists an isomorphism L→P^1_{O_H} of O_H-schemes."
- "as an O_K-scheme, L has geometrically disconnected fibers if h_K>1. We also emphasize that there appears to be no canonical isomorphism L→P^1_{O_H}."
- "The endomorphisms ψ_a of 𝓔 induce endomorphisms ψ_a: L→L (morphisms of O_K-schemes)… Further the map L→Spec O_H above is a morphism of Λ-schemes, where O_H is given its unique Λ-ring structure."
- So ψ_p is O_K-linear but not O_H-linear.

## 5. Where the general case is posed (most important)

**Intro, p. 4.** "It is natural to ask whether something like this holds for number fields K larger than Q. Do the ray class algebras R_{A,P}(f) have a common origin in the algebraic geometry of Λ_{A,P}-rings? If so, this would give a systematic way of generating ray class algebras and hence ray class fields. Or more modestly, is it at least true that the known explicit class field theories admit a Λ-refinement as above?"

**(Q1), p. 5 — generation, conductor by conductor.** "Does there exist a Λ_{A,P}-scheme X of finite type such that for all f∈Z(P,r), the direct factors of the étale K-algebra O(X(f)_K) generate the ray class field K(f)?"

**(Q2), p. 6 — loci are the ray class algebras.** "The strongest question one might ask is the following: (Q2) Does there exist a Λ_{A,P}-scheme X of finite type such that for all f∈Z(P,r), the function algebra O(X(f)) of the periodic locus is isomorphic to the ray class algebra R_{A,P}(f)?" Followed by: "the answer is positive in the cyclotomic context with X=G_m. But it appears to be slightly too much to ask for in general."

**(Q3), p. 6.** "given a finite reduced Λ_{A,P}-ring B, let B̃ denote the maximal integral Λ_{A,P}-model in K⊗_A B." Then: "Does there exist a Λ_{A,P}-scheme X of finite type such that for all f∈Z(P,r), (i) the function algebra O(X(f)) is of finite index in O(X(f))^∼, (ii) the morphism of pro-rings (O(X(f)))_{f∈Z(P,r)} → (O(X(f))^∼)_{f∈Z(P,r)} is an isomorphism, and (iii) O(X(f))^∼ is isomorphic to R_A(f) as a Λ_{A,P}-ring?" ("R_A(f)" is a slip for R_{A,P}(f).)

**Intro, p. 7.** "It would be interesting to know if something similar holds for other number fields, starting with CM fields. One virtue of the questions above is that they allow for negative answers, which would also be interesting."

**§12 opening.** "Is it possible to use Λ-schemes of finite type to generate other large abelian extensions, beyond the Kroneckerian explicit class field theories? We will formulate a range of such questions in this section, some of which it is reasonable to hope have a positive answer and some which are more ambitious."

**§12.1 — the weaker field-theoretic form.** "But hungry for positive answers, we will give weaker, field-theoretic formulations here. Let K be a number field, and write Λ=Λ_{O_K,M_K} and R(f)=R_{O_K,M_K}(f). For any separated Λ-scheme X of finite type over O_K, consider the extension of K obtained by adjoining the coordinates of the f-periodic points, for all cycles f: K(X) := ⋃_f K(X(f)(K̄))."
- Example (4): "If K is general and X=Spec R(f), then K(X) is the ray class field K(f)."
- Example (5): "K(X_1⨿X_2) is the compositum K(X_1)K(X_2)".
- "Let us say that an abelian extension L/K is Λ-geometric if there exists an X as above such that L⊆K(X). First observe that any finite extension of a Λ-geometric extension is Λ-geometric, by (4) and (5) above."

**(Q4).** "Are there number fields other than Q and imaginary quadratic fields for which the maximal abelian extension is Λ-geometric?"

**(Q5), with the only stated expectation.** "It is natural to consider Shimura's generalization of Kronecker's theory to CM fields and abelian varieties. We expect that it can be realized in our framework, or at least that some version of it. But note that, assuming [K:Q]>2, the maximal abelian extension generated by Shimura's method is an infinite subextension of the maximal abelian extension itself—the relative Galois group is an infinite group of exponent 2." Then: "Let K be a CM field of degree greater than 2. Is Shimura's extension Λ-geometric? If so, is there an infinite extension of it which is Λ-geometric?"

**§12.2.** "It appears difficult to find Λ-schemes of finite type which generate large infinite abelian extensions. Every example we know ultimately comes from varieties with complex multiplication."

**(Q6).** With R[r] = lim_{f∈Z(P,r)} R(f): "Does R[r] have a dense sub-O_K-algebra which is finitely generated as an O_K-algebra? Does it have a dense sub-Λ-ring which is finitely generated as an O_K-algebra?"

**(Q7), §12.3.** With I_a the kernel of ψ_a: R(ra)→R(r)=O_{K(r)}: "Given a maximal ideal q⊂O_{K(r)} with residue field k, is the dimension dim_k(k⊗I_a/I_a^2) constant for large a?" Followed by: "One might hope it is the number of places of K at infinity."

**The only item labelled "Conjecture" (§8.5), not about class fields.** "Let r denote the product of all the real places. If X is proper and nonempty, we conjecture that X(r) is nonempty. When K=Q, this was proved in [5]."

**§8.6.** "Is there an algorithm to find equations describing X(f)…?"

**Summary of what is asked.**
- Everything for general K is a question, not a conjecture. No direction is asserted.
- All three forms appear: loci are the ray class algebras (Q2), the same up to Λ-normalisation and pro-isomorphism (Q3), and generation only (Q1 per conductor, Q4 for K^ab).
- Q1–Q3 are stated for an arbitrary context (A,P,r). Q4–Q7 fix A=O_K and P=M_K, with nothing inverted.
- No obstruction or negative result is proved for any field, and nothing is said about tori or abelian varieties beyond the CM remark above.

## 6. Hilbert's twelfth problem and Kronecker

- "Hilbert's 12th Problem" appears once, in the abstract (quoted in item 1), plus in the title of reference [22].
- "Jugendtraum" does not appear anywhere in the source.
- Kronecker appears in:
  - "a Λ-refinement of the Kronecker–Weber theorem" (§1);
  - "explicit class field theory over K, due to Kronecker and his followers" (§11);
  - "Kroneckerian explicit class field theories" (§12);
  - "Shimura's generalization of Kronecker's theory" (§12.1).

## 7. Status through 16 Nov 2026

**Open as far as I can tell.** I found no paper answering or partially answering Q1–Q7 for a non-CM number field or for a CM field of degree > 2.

Citing works found (OpenAlex lists 4; Semantic Scholar 1; searches added the thesis):
- **Derek Cheng, ANU PhD thesis, January 2022** (supervisor Borger), "Drinfeld Modules, Explicit Class Field Theory and Λ-Structures". Abstract: "given a global function field K, the maximal abelian extension of K is Λ-geometric", via rank-1 Drinfeld modules. This is the function-field analogue of Q4, answered yes. For number fields it records only Q and imaginary quadratic as known.
- **Yalkinoglu, arXiv:2507.23759 (Jul 2025)**, "Bost-Connes systems and periodic Witt vectors". Uses the periodic Witt vectors of §9 to give integral models of Bost–Connes arithmetic subalgebras for all number fields. It uses W, which is not of finite type, so it does not touch Q1–Q7.
- **Uramoto, Semi-galois Categories III and IV** (arXiv:2007.13367 and 2305.13265; the journal references, Res. Math. Sci. 2025 and Math. Z. 2025, come from OpenAlex and a later bibliography, not arXiv). Modularity theorems for algebraic Witt vectors over imaginary quadratic and CM fields. Neither source contains "finite type".
- **Ishitsuka–Uramoto, J. Aust. Math. Soc. 121 (2026) 53–75.** Decidability of integrality of algebraic Witt vectors over imaginary quadratic fields. Landing page only; body paywalled.
- **Uramoto, arXiv:2607.28465 (v4, 13 Nov 2026)**, "Deformation of the absolute Galois groups of number fields". A non-abelian monoid DG_K with abelianisation DR_K; not about finite-type Λ-schemes. I read the source file arXiv served, which is dated August and may predate v4.

Other findings:
- **Yalkinoglu, arXiv:2611.01201 (31 Oct 2026)**, "Fermat curves and explicit class field theory for real quadratic number fields", marked "preliminary version". It claims "an alternative constructive solution to Hilbert's 12th problem for real quadratic fields" via Kummer theory and Jacobi sums. It does not cite Borger–de Smit or use Λ-structures. It carries a "Use of AI tools" paragraph (calculation help and proof-reading). It does not answer Q1–Q4.
- No follow-up by Borger, de Smit or Gurney on arXiv addresses the questions.
- Gurney's thesis abstract (arXiv:1710.08674) states that CM elliptic curves "of Shimura type are precisely those admitting Lambda structures". It is the only obstruction-style statement I met, and it is in the imaginary quadratic setting.
- No negative result for real quadratic fields, and no AI-generated claim about these questions, turned up in the searches.

## 8. Pitfalls for a naive formalization

- **Integrality is the whole content.** Over a K-algebra the Frobenius condition is vacuous, so the statement must involve a flat O_K-model with ψ_p ≡ Frobenius on the fibre at every p∈P.
- **Flat, separated, finite type are standing hypotheses.** The naive definition is declared "not well behaved" without flatness.
- **ψ_p is A-linear.** It is an O_K-algebra or O_K-scheme endomorphism, not merely a ring map; for P^1_{O_H} it is not O_H-linear.
- **Finite type is what makes Q4 non-trivial.** Without it, W_{A,P} or ∐_f Spec R(f) works. Remark 9.11: "W_{A,P} is not of finite type, and so this does not give an explicit method". Every finite abelian extension is trivially Λ-geometric, so the target must be all of K^ab, with one X for all f.
- **"Periodic" is tied to DR_P(f).** It depends on the real places of f, on P, and on non-invertible classes through the gcd condition. It is not the torsion locus.
- **X(f) versus X(f)_fl.** Q2 and Q3 are worded with O(X(f)), but everything proved (Thm 8.4, Thm 11.12) concerns X(f)_fl. Q1 and Q4 only see the generic fibre, so they are unaffected.
- **The ray class algebra is not O_{K(f)}.** It is the maximal Λ-order in ∏_{d|f_fin} K(fd^{-1}), is not a domain, and depends on P.
- **Imaginary quadratic case.** The paper proves (Q3), not (Q2); the index is only controlled at primes dividing |O_K^*|. The scheme is P^1_{O_H} over O_K with a non-canonical identification.
- **Thm 1.4 has no proof block.** It is assembled from §10.1 with the 2008 paper, Cor. 10.4 and Thm 11.12. I did not find the pro-isomorphism (Q3)(ii) for the Chebyshev line written out in §10; the intro only says the error "disappears in the limit".
- **Choice of P.** Q4–Q7 require P = all primes of O_K. Allowing a cofinite P or A=O_K[1/t] gives a different, weaker statement.
- **Typos in the source:**
  - "End_{A-alg}(E)" in the definition should be B;
  - "Then K has an integral model" in Thm 1.1 should be E;
  - "u∘v and u∘v" in §7.5;
  - the letter r also denotes an unrelated ideal in §5 (5.0.17).

## Could not verify

- Google Scholar's citing list (blocked by a verification page) and Semantic Scholar paper metadata (rate-limited). Citation coverage rests on OpenAlex, one Semantic Scholar citations call, arXiv and web search.
- MathSciNet was not accessible. "No journal version" rests on arXiv, zbMATH, OpenAlex, Crossref and 2025–2026 citations.
- The published Bull. LMS text and its theorem numbering. I read the arXiv source; Cheng's thesis cites the first theorem as "Theorem 0.1".
- Gurney's thesis sections cited by the paper (2.6.10, 4.2.2, 4.3.10, 4.3.11, 4.3.16) were not checked.
- The body of Ishitsuka–Uramoto, the correctness of Yalkinoglu's Oct 2026 preprint, and the status of Conjecture 8.5 for K≠Q.
- From recollection only, not checked this session: the Dasgupta–Kakde and Darmon–Vonk approaches to Hilbert's twelfth problem are p-adic-analytic and, to my knowledge, do not produce finite-type Λ-schemes.
- The TeX source has a block after `\end{document}` ("Miscellaneous questions", e.g. "Is W^{(f)}_{A,P}(O_K̄)→O_{K(f)} surjective?"). It is not part of the paper.

Sources:
- [arXiv:1809.02295 abstract](https://arxiv.org/abs/1809.02295), [PDF](https://arxiv.org/pdf/1809.02295), [HTML](https://arxiv.org/html/1809.02295v1)
- [Author-hosted PDF](https://maths-people.anu.edu.au/~borger/papers/_all/ExplicitClassFieldTheory.pdf), [Borger's papers page](https://maths-people.anu.edu.au/~borger/papers.html)
- [zbMATH record](https://zbmath.org/900358547)
- [arXiv:0801.2352](https://arxiv.org/abs/0801.2352), [arXiv:1105.4662](https://arxiv.org/abs/1105.4662)
- [Cheng thesis, ANU](https://openresearch-repository.anu.edu.au/server/api/core/bitstreams/dab15ecc-3160-4cd0-9708-c9eea4d81cbb/content)
- [arXiv:2507.23759](https://arxiv.org/abs/2507.23759), [arXiv:2611.01201](https://arxiv.org/abs/2611.01201)
- [arXiv:2607.28465](https://arxiv.org/abs/2607.28465), [arXiv:2007.13367](https://arxiv.org/abs/2007.13367), [arXiv:2305.13265](https://arxiv.org/abs/2305.13265)
- [Ishitsuka–Uramoto](https://www.cambridge.org/core/product/identifier/S1446788726101487/type/journal_article)
- [arXiv:1710.08674](https://arxiv.org/abs/1710.08674), [arXiv:1910.14358](https://arxiv.org/abs/1910.14358)
