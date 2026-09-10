import json
import re
from pathlib import Path

ROOT = Path('/tmp/pool-review-20260908')
OUT = Path('/root/conjectures-validator/docs/review-decisions/2026-09-08-pool-review')
OUT.mkdir(exist_ok=True)

def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')

def decision(theorem, status, reason, urls):
    return dict(theorem=theorem, decision=status, reason=reason, evidence_urls=urls)

removed = [
    decision('Erdos126.erdos_126', 'retire_settled',
        'The September 2026 proof gives f(n) ≫ sqrt(n), which implies f(n)/log(n) tends to infinity. The tracker records PROVED (LEAN). This settles the main target only; the separate isLittleO upper bound remains.',
        ['https://www.erdosproblems.com/126', 'https://www.erdosproblems.com/forum/thread/126/proof-claims', 'https://github.com/tadamcz/erdos126', 'https://www.erdosproblems.com/static/126-proof.pdf']),
    decision('Erdos700.erdos_700.parts.ii', 'retire_settled',
        'Proof claim 157 is explicitly accepted by the source site. Stijn Cambie reports checking the paper in detail. Fixed-gap prime triples give infinitely many composite n with f(n)^2 > n. The whole numbered problem remains open because other parts remain. This supersedes the erroneous admission in the preceding September 8 addition review.',
        ['https://www.erdosproblems.com/forum/thread/700/proof-claims#proof-claim-157', 'https://www.erdosproblems.com/forum/proof-claims/157/comments', 'https://www.overleaf.com/read/pmnwkfnhhxhn#82956e']),
    decision('Erdos727.erdos_727.variants.k_2', 'quarantine_unverified_claim',
        'Johan Land publishes an unconditional k=3 construction and a Lean endpoint for k=2, exactly the active target. The general all-k problem being open does not clear this variant. The claimed Lean build has not been independently reproduced in this audit.',
        ['https://www.erdosproblems.com/forum/thread/727/proof-claims', 'https://github.com/beetree/math_erdos_727']),
    decision('Erdos1059.erdos_1059', 'quarantine_unverified_claim',
        'A September 5 manuscript claims the full factorial-avoiding-prime theorem. Its repository displays a matching endpoint and a standard-axiom build report, but also notes that its recorded build was from a dirty/untracked snapshot and lacks independent replication. This is a substantive prior claim, not a confirmed solution in this audit.',
        ['https://www.erdosproblems.com/forum/thread/1059/proof-claims#proof-claim-264', 'https://github.com/beetree/math_erdos_1059']),
    decision('Erdos96.erdos_96', 'quarantine_disputed_claim',
        'Khopkar arXiv:1605.08066v2 claims the exact O(n) convex unit-distance bound. The source discussion treats it as unconfirmed and identifies a missing decomposition argument. No accepted proof was established here; withhold pending adjudication of the published claim.',
        ['https://arxiv.org/abs/1605.08066v2', 'https://www.erdosproblems.com/forum/thread/96']),
    decision('Erdos242.erdos_242', 'quarantine_disputed_claim',
        'Bradford arXiv:2602.11774 claims an Erdos-Straus solution; source discussion disputes its credibility. Other advertised Lean solutions only supply finite checks or unsupported periodicity. The pinned target additionally requires distinct ordered denominators, so the exact implication would need checking even if the paper were accepted. No verified solution is asserted here. The all-numerators Schinzel generalization is a separate retained target.',
        ['https://arxiv.org/abs/2602.11774', 'https://www.erdosproblems.com/forum/thread/242', 'https://github.com/google-deepmind/formal-conjectures/issues/3952']),
    decision('Erdos12.erdos_12.parts.iii', 'quarantine_incomplete_claim',
        'A public claim advertises reciprocal-sum convergence, exactly part iii, but its discussion identifies an assumed growth_ineq axiom and requests the missing block-growth argument. It does not constitute an unconditional proof. Conservatively withhold the target while the underlying claim is unresolved; do not describe it as solved.',
        ['https://www.erdosproblems.com/forum/thread/12/proof-claims#proof-claim-172', 'https://www.erdosproblems.com/forum/proof-claims/172/comments', 'https://github.com/libertas-technology-group/Libertas-Erdos-Solutions']),
    decision('Green72.green_72', 'quarantine_source_mismatch',
        'The pinned target asserts a 2N no-three-in-line configuration for every N >= 3. Green asks whether such configurations are eventually impossible. These are not logical negations: negating an eventual assertion only gives infinitely often. Open correction PR 4941 documents the mismatch. Withhold for a separate statement review; no solution is claimed.',
        ['https://github.com/google-deepmind/formal-conjectures/pull/4941', 'https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf']),
]
write('withdrawals.json', sorted(removed, key=lambda x:x['theorem']))

# Scope notes concern the pinned mathematical statement, not the numbered page's badge.
notes = {
    'Erdos126.erdos_126.variants.isLittleO': 'Retained: f=o(n/log n) is an upper bound; the accepted sqrt(n) lower bound does not imply it.',
    'Erdos242.erdos_242.variants.schinzel_generalization': 'Retained: all positive numerators a and sufficiently large n. A claim only about numerator 4 does not settle this target.',
    'Erdos354.erdos_354.parts.i': 'Retained: fixed base 2. PR 5286 concerns a different base in part ii.',
}
number_notes = {
  11:'The purported full reduction in the discussion does not establish the target; conditional results remain conditional.',
  51:'The advertised totient-inverse argument confuses minimal and maximal preimages.',
  66:'A result for functions growing faster than log does not settle the logarithmic boundary.',
  82:'Finite graph checks and conditional bounds do not prove the universal statement.',
  120:'Old nonmeasurable counterexamples must be compared with the pinned measurability conditions; the corrected pinned target is retained.',
  137:'ABC-dependent powerful-factor conclusions are conditional.',
  143:'The claimed estimate does not establish the target sharp constant.',
  153:'The claim concerns asymptotically optimal-diameter Sidon sets, not the unrestricted pinned target.',
  218:'The balanced-prime equivalence was noticed. A historical name on the broad retirement denylist does not by itself establish a prior solution; no accepted solution was found.',
  233:'The advertised full proof leaves Hardy-Littlewood and large-gap estimates unproved.',
  238:'Known forall c2 exists small c1 bounds do not give the target forall c1 forall c2.',
  264:'The solved exponential-denominator cases do not settle the factorial variant.',
  269:'Results for two primes or infinitely many primes do not settle the finite set of at least three primes.',
  274:'The proof-claims tab reports verification for at most 17 cells, not the unrestricted group/partition claim.',
  313:'The Sylvester-sequence suggestion is not a prime construction: 1807 is composite (13*139).',
  325:'A result for some k>=33 does not give the universal k statement.',
  357:'PRs 4918/4967 and the proof claim establish other growth bounds; f<=n/2+O(n^(2/3)) does not imply the active o(n), summability, or general h=o(n) conclusions.',
  364:'Excluding a particular shape of powerful triples does not exclude every triple.',
  375:'Finite verification through 10^12 does not establish the universal assertion.',
  383:'The reported smooth-product exponent 2-1/(2k)+epsilon is weaker than the target exponent 1.',
  396:'Finite enumeration and density conclusions do not establish existence for every k.',
  409:'A density-full subclass with finite basins does not establish the universal iterated claim.',
  416:'Cluster/subsequence conclusions do not imply convergence of the entire ratio sequence.',
  456:'The part iii prime-family construction depends on Dickson; already solved parts i/ii are not active.',
  479:'Known k of the form 2^i or 2^i-1 do not settle arbitrary k.',
  535:'Discussion of an auxiliary omega formulation does not settle the active N^(c/loglog N) bounds.',
  617:'All seven claims are for fixed r (5 through 9); the active claim quantifies over every r.',
  699:'Accepted restricted ranges such as j<=3i/2 and other special triples do not cover all triples.',
  701:'The discussed counterexample uses an infinite ground set; the pinned family is finite.',
  726:'The claimed full result assumes unproved reciprocal-prime equidistribution Hypothesis 3.1.',
  770:'Bounds and equality under a large-prime-factor condition do not settle density or infinitely many h=3 cases.',
  835:'The proposed Hoffman argument gives a coloring constraint, not the required universal nonexistence.',
  873:'A 1/4+epsilon exponent is weaker than the target for every epsilon.',
  885:'Fixed small k and finite checks do not establish all k.',
  890:'The pinned part a already uses omegaGt. The formal counterexample to the older ordinary-omega statement does not refute this corrected target.',
  913:'Upstream renamed sub_one to add_one, but both formulas use 8p^2-1. This is a name drift, not evidence of a solution.',
  951:'The pinned conclusion is eventual in x; a small-x counterexample does not settle it.',
  975:'Compare purported counterexamples with the pinned nonconstant-polynomial condition.',
  978:'The pinned part ii includes a local divisibility obstruction hypothesis, excluding the old constant-divisor counterexample. The n^4+2 squarefree conclusion is only known conditionally on ABC.',
  982:'The improved 13/36+3/5270 lower bound and concyclic special case do not establish the general floor(n/2) claim.',
  985:'An almost-all-primes result does not settle the assertion for all primes.',
  1004:'The draft proof was withdrawn because the required uniformity was not justified.',
  1057:'A Carmichael-number exponent .34 is weaker than the target 1-epsilon.',
  1060:'An elementary divisor-product bound does not give a uniform polylogarithmic bound for inverse sigma.',
  1068:'The relevant pinned notion is vertex connectivity; a theorem about edge connectivity does not suffice.',
  1095:'Upstream log_isTheta is now named log_equivalent with a stronger asymptotic equivalence; no unconditional proof of the pinned Theta target was found. The lower conjecture also exceeds the known exp(c log^2 k) bound.',
  1209:'A counterexample using an arbitrary fast sequence does not settle the specified 2^(2^k) sequence.',
}
green_notes = {
  2:'Known restricted-sum-free growth 1+1/69 does not reach the logarithmic power 100 in the target.',
  7:'The positive-density Ulam-sequence variant remains open in the reviewed sources.',
  9:'The solved r3 bound is separate from active r5(N) and r4(F5^n) bounds.',
  12:'The Mobius ladder K5,5 minus C10 instance is not covered by the reviewed high-density Sidorenko results.',
  15:'Known four-term progression avoidance does not settle the three-term Lipschitz-graph question.',
  18:'BMZ corners and the naive nonabelian corners in this target have different multiplication placement.',
  24:'The gamma=1/3 threshold remains open; nested namespace syntax is not a missing upstream declaration.',
  32:'The requested sqrt(p)-scale gap after dilation remains open in the source document.',
  33:'The sharp sqrt(2q) cyclic sumset covering constant remains open in the reviewed source.',
  36:'Retained CKS05 variant; use the preceding addition review for its exact hypothesis and literature comparison.',
  39:'Retained after the preceding addition review and current source/PR/literature screen; no exact resolution found.',
  40:'The known nonlinear f-tilde(2)=1 result does not settle linear f(2)=1. The arbitrary-subsets variant asks growth as r tends to infinity, not the single r=2 case.',
  41:'The 2025 triple-exponential pyjama bound does not imply the polynomial epsilon bound.',
  44:'Retained after the preceding addition review and current source/PR/literature screen; no exact resolution found.',
  47:'Croot-Yip large intersections do not give the full containment assertion.',
  50:'The proved polynomial Freiman-Ruzsa theorem does not supply this stronger linear-logarithmic codimension bound for 10A; log^(3+eta) remains weaker.',
  51:'The near-half-density window 1/2-K/sqrt(n) with bounded codimension is the unresolved variant.',
  58:'Inverse-large-sieve reductions do not settle the N^.49 two-set composite-sum target.',
  60:'Bounds for squares in arithmetic progressions do not settle arbitrary square sets with small sumset.',
  62:'The known three-prime-products covering result does not establish two-prime-products covering.',
  66:'Retained after the preceding addition review and current source/PR/literature screen; no exact resolution found.',
}

active = json.loads((ROOT/'active-targets.json').read_text())
upstream = {x['theorem']: x for x in json.loads((ROOT/'upstream-screen.json').read_text())}
prs = {x['theorem']:x['prs'] for x in json.loads((ROOT/'selected-prs.json').read_text())}
withdrawals = {x['theorem']:x for x in removed}
claimed = {x['number'] for x in json.loads((ROOT/'claims.json').read_text())}
rows=[]
for source in active:
    name=source['theorem']; erdos=name.startswith('Erdos')
    n=int(Path(source['source_path']).stem)
    row=dict(source, upstream_screen=upstream[name], open_prs=[{k:p[k] for k in ('number','title','html_url','updated_at')} for p in prs[name]])
    row.update(withdrawals.get(name, dict(decision='retain_bounded_screen', reason='No exact accepted solution or unresolved full-scope claim identified in the reviewed sources. This is a dated, bounded screen, not certification that no prior solution exists.')))
    row['scope_note']=notes.get(name, (number_notes if erdos else green_notes).get(n, 'Reviewed the pinned statement against current source status, source discussion, and matching upstream PR/issue signals.'))
    row['source_url']=f'https://www.erdosproblems.com/{n}' if erdos else 'https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf'
    row['discussion_url']=f'https://www.erdosproblems.com/forum/thread/{n}' if erdos else None
    row['proof_claims_url']=f'https://www.erdosproblems.com/forum/thread/{n}/proof-claims' if erdos and n in claimed else None
    rows.append(row)
write('target-decisions.json', rows)

# Fix cross-family number matches: Green 15 must not inherit Erdos 15 issues.
issues=[]
for f in ['open-issues.json','closed-issues.json']:
    issues.extend(json.loads((Path('/tmp/add-50-20260908')/f).read_text()))
matches=[]
for s in active:
    n=int(Path(s['source_path']).stem)
    family=r'erd[oőö]s' if s['theorem'].startswith('Erdos') else r'green(?:s|\x27s)?'
    pattern=re.compile(r'\b'+family+r'[^\d\n]{0,25}#?'+str(n)+r'\b',re.I)
    matches.append({'theorem':s['theorem'],'issues':[x for x in issues if pattern.search(x['title'])]})
write('issue-matches.json', matches)

# Section boundaries must be headings, not in-paragraph cross references.
document=(ROOT/'green-problems.txt').read_text()
headings=list(re.finditer(r'(?m)^Problem\s+(\d+)\.',document))
selected={int(Path(x['source_path']).stem) for x in active if x['theorem'].startswith('Green')}
sections=[]
for i,m in enumerate(headings):
    n=int(m.group(1))
    if n in selected:
        sections.append({'number':n,'text':document[m.start():headings[i+1].start() if i+1<len(headings) else len(document)]})
assert len(sections)==22
(ROOT/'green-sections.json').write_text(json.dumps(sections,indent=2)+'\n')
print('Prepared',len(rows),'target decisions;',len(removed),'withdrawals')
