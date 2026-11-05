"""Record the bounded, target-specific admission review; never deploy."""
import json,hashlib
from pathlib import Path
B=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
TASKS=B/'conjectures-tasks'
STAGE=TASKS/'staged/erdos-328-20261028'
manifest=json.loads((STAGE/'manifest.json').read_text())
compiled={x['theorem']:x for x in json.loads((STAGE/'compiled-declarations.json').read_text())['declarations']}
claims=json.loads((OUT/'evidence/claim-review-notes.json').read_text())
review={}
def set_review(name,decision,note,duplicate_of=None):
 assert name in compiled,name
 review[name]={'decision':decision,'review_note':note}
 if duplicate_of:review[name]['duplicate_of']=duplicate_of
# Exact claims found in proof-claim tabs, open PRs, or the primary discussion.
for n in [64,75,81,97,279,390,488,501,551,597,740,742,743,802,881,995,996,1002,1061,1075,1110,1122,1139]:
 set_review(f'Erdos{n}.erdos_{n}','hold_resolution_claim',
   'An unverified full-resolution claim overlaps this exact proposition. Excluded from new intake; no assertion that the claim is correct.')
for name in ['Erdos97.erdos_97.variants.k_equidistant','Erdos123.erdos_123.variants.powers_2_3_5_snug','Erdos133.erdos_133.variants.asymptotic','Erdos349.complete_for_alpha_in_Ioo_one_to_goldenRatio','Erdos675.erdos_675.parts.ii','Erdos785.erdos_785.variants.chen_conjecture','Erdos786.erdos_786.parts.i','Erdos786.erdos_786.parts.ii','Erdos820.erdos_820.variants.two_pow_upper','Erdos872.erdos_872.parts.i','Erdos883.erdos_883.parts.i','Erdos70.erdos_70.variants.omega_one','Erdos174.erdos_174.variants.graham']:
 set_review(name,'hold_resolution_claim','The claim/PR concerns this part or variant. Other parts are evaluated independently; correctness of the claimed proof is unverified.')
set_review('Erdos1186.erdos_1186.variants.prs_conjecture','hold_resolution_claim','The October 5 Toledo claim states precisely delta_3=117/2192. Although k>=4 remains open, this exact k=3 target is covered and must be held.')
set_review('Erdos252.erdos_252.variants.k_ge_five','exclude_duplicate','The remaining k>=5 cases are the entire open content of the already retired all-k target, since k=1..4 are known. The September 8 claim explicitly covers every k>=1. Do not reopen the retired problem through this variant.','Erdos252.erdos_252')
set_review('Erdos971.erdos_971','hold_resolution_claim','The claimed analytic route overlaps the exact prime-progression bound; verification remains outstanding.')
set_review('Erdos660.erdos_660.variants.Er75f','hold_resolution_claim','The primary source attributes the linear bound to Altman without a located reference. Do not offer a possibly known result as an open reward.')
for name in ['Erdos897.erdos_897.variants.parts.i','Erdos897.erdos_897.variants.parts.ii']:
 set_review(name,'exclude_statement_discrepancy','Open PR 5350 identifies growth-filter and prime-power hypothesis defects, including the k=0 case. Requires a separately reviewed corrected statement.')
for suffix in ['', '.variants.binary_colors','.variants.r_eq_two']:
 set_review('Erdos1167.erdos_1167'+suffix,'hold_statement_dispute','Primary discussion disputes the unrestricted target-cardinal range. The stated gamma=1 counterexample does not apply to gamma>=2; no disproof is asserted. Exclude pending resolution of the range dispute. The explicitly bounded infinite-target variant is evaluated separately.')
# Mathematical duplicate screen. Proof/refutation are already modes of one target.
for name,target,note in [
 ('Erdos5.erdos_5.variants.dense','Erdos5.erdos_5','The dense and limit-point formulations describe the same limit-point question.'),
 ('Erdos5.erdos_5.variants.limit_point_set','Erdos5.erdos_5','The limit-point set formulation is equivalent to the retained main question.'),
 ('Erdos125.erdos_125.variants.zero_density','Erdos125.erdos_125.variants.positive_upper_density','Zero density is the negation of existence of a positive-upper-density example; counterexample mode already covers the opposite answer.'),
 ('Erdos125.erdos_125.variants.zero_lower_positive_upper_density','Erdos125.erdos_125.variants.positive_upper_density','The lower density is already known to be zero; the conjunction adds only the same positive-upper-density question.'),
 ('Erdos162.erdos_162','Erdos563.erdos_563','Both ask the balanced-colouring threshold asymptotic. The 162 source corrects largest to smallest; retain the clearer threshold encoding in 563.'),
 ('Erdos596.erdos_596.variants.K4_K3_exceptional_iff','Erdos595.erdos_595','Source explicitly identifies this as the exceptional-pair reformulation of 595. Preserve the earlier retirement instead of reopening it through an alias.'),
 ('Erdos694.erdos_694.variants.carmichael','CarmichaelTotient.charmichaelTotient','A uniquely represented positive totient is precisely a counterexample to the existing Carmichael target.'),
 ('Erdos813.erdos_813','Erdos813.erdos_813.variants.upper','The lower half is a known result; the remaining open content is exactly the separately retained upper bound.'),
 ('Erdos918.erdos_918.variants.all_subgraphs.parts.i','Erdos918.erdos_918.parts.i','A colouring of an induced graph restricts to every subgraph on the same vertices, and induced graphs are subgraphs.'),
 ('Erdos918.erdos_918.variants.all_subgraphs.parts.ii','Erdos918.erdos_918.parts.ii','Induced versus arbitrary subgraph formulations are equivalent by chromatic monotonicity.'),
 ('Erdos1087.erdos_1087','FullOldProblemsCatalog.ErdosPurdyDegenerateQuadruples.conjecture','Already admitted as the full Erdos-Purdy degenerate-quadruple question; big-O constants are absorbed into epsilon.'),
 ('Erdos1169.erdos_1169.variants.finite_blue_target','Erdos1169.erdos_1169','The k=3 negative arrow implies every k>=3, and the quantified variant includes k=3.'),
 ('Erdos1174.erdos_1174.parts.i','Erdos595.erdos_595','A graph not a countable union of triangle-free graphs is equivalently a graph whose every countable edge-colouring has a monochromatic triangle. Do not reopen the retired 595 target through an alias.'),
]:set_review(name,'exclude_duplicate',note,target)
# Three historical holds have concrete resolutions in the pinned newer source/evidence.
for n,note in {
282:'The recursive greedy remainder removes each used denominator. This matches the primary clarification about distinct denominators (Bloom, 7 July 2026). Old held versions stay historical.',
564:'The exponent constant now explicitly has type Real and is positive; the primary question has a positive real constant. Old Nat-constant versions stay held.',
1004:'The author acknowledges that the claimed logarithmic saving was too strong; the forum discussion withdraws that full argument. The c<2 result remains partial. This resolves the specific prior hold, without asserting that no other proof exists.'}.items():
 set_review(f'Erdos{n}.erdos_{n}','eligible',note)
notes={
65:'The Montgomery/Milojevic/Pokrovskiy/Sudakov result covers large d; the retained proposition quantifies over all positive k and all n.',
267:'The claim covers ratios bounded below by c>1. The remaining generalisation assumes only n_k/k tends to infinity, so is not covered.',
270:'The linked clambro repository explicitly concerns the affine case. The retained statement is for every monotone unbounded f.',
349:'The general alpha interval claim is held. The two floor(3/2) subsequence variants are separate questions.',
394:'Known constant savings do not settle every c<log 2 or the factorial-gap variant.',
424:'The reported proof addresses positive lower density. This target asks for existence of a positive exact density, which is stronger; no full exact-density claim was identified.',
597:'The unrestricted statement is held for a claim. The finite variant remains explicitly unresolved in the source.',
654:'The 3n/4 construction refutes the unrestricted 1-o(1) version. It does not settle the 1/3+c bound or general-position variants. The at-most-two circle attribution is disputed in the forum, so that variant is held separately.',
670:'Ho addresses dimension growing with n. The retained statement fixes d before epsilon and the eventual n threshold; the primary moderator explicitly distinguishes this open regime.',
700:'The claim concerns a square-root lower bound, not the remaining superpolylogarithmic upper bound.',
769:'Refuting c(n)>>n^n does not determine the limiting exponent requested by the remaining growth-rate variant.',
820:'The K bound claim is held; the H=3 infinitude question and the logarithmic exponent are not resolved by the claimed lower bounds.',
865:'The checked k=3 result does not establish the Erdos-Sos formula for every k>=2.',
1020:'The full Mishra claim was explicitly found to fail at Lemma 4; the surviving partial claim covers r=4, not all r.',
1044:'The fixed-degree minimizer remains open; the claimed proof concerned the unrestricted main problem, and a purported fixed-degree argument was rejected by its author.',
1074:'The new density-one statement follows the authors quoted conjecture; the old density-one-half declaration remains held.',
1083:'The d=3 result does not settle the quantified d>=3 target.',
1154:'Relative consistency/stronger hypotheses do not give the unconditional ZFC assertions encoded here.',
1167:'The infinite-target variant explicitly requires every target cardinal to be at most lambda, avoiding the disputed range.',
}
set_review('Erdos654.erdos_654.variants.erdos_pach','hold_statement_dispute','Primary discussion questions the attribution and the pairing of the at-most-two circle condition with the 1-o(1) bound; the cited original sources need reconciliation.')
rows=[];problems=[]
for p in manifest['problems']:
 names=[]
 for t in p['targets']:
  n=t['theorem'];d=compiled[n];decision='eligible';reason='Compiled proposition/docstring reviewed for quantifiers, domains, positivity, asymptotic regime, and stated part/variant. No full-resolution conflict identified in the bounded source/PR screen.'
  blockers=t['blockers']
  if 'existing_retirement_requires_review' in blockers:decision='exclude_prior_retirement';reason='Preserve the recorded retirement and its existing decision evidence. An upstream research-open label alone does not reverse an audited retirement.'
  elif 'existing_hold_requires_review' in blockers:decision='hold_existing';reason='The existing hold remains unresolved; preserve it and exclude this target from new intake.'
  elif 'type_depends_on_unfilled_definition' in blockers:decision='exclude_unfilled_statement';reason='The proposition transitively depends on sorryAx through an unfilled answer/definition. It is not an independently checkable fixed reward target.'
  elif 'value_answer_adapter_required' in blockers:decision='exclude_value_question';reason='The current production task format requires a fixed proposition; this question requires supplying an unknown value before a target can be fixed.'
  row={'theorem':n,'number':p['number'],'source_path':p['source_path'],'parent_problem_status':p['numbered_problem_status'],'target_source_status':'open','original_blockers':blockers,'decision':decision,'review_note':reason,'type_pretty':d['type_pretty'],'upstream_type_sha256':hashlib.sha256(d['type_canonical'].encode()).hexdigest(),'references':[p['source_url'],f'https://www.erdosproblems.com/forum/thread/{p["number"]}']}
  if n in review:row.update(review[n])
  if p['number'] in notes:row['scope_note']=notes[p['number']]
  rows.append(row);names.append(n)
 problems.append({'number':p['number'],'previously_fully_blocked':all(t['blockers'] for t in p['targets']),'targets':names})
assert len(rows)==482 and len(problems)==328
out={'schema_version':1,'audit_date_utc':'2026-10-28','upstream_commit':manifest['upstream_commit'],'deployment_allowed':False,'review_limitations':['Bounded review of compiled statements, source docstrings, relevant definitions, primary problem pages/discussions, 83 proof-claim pages, and 129 relevant open pull requests. Not an exhaustive literature search or a proof of semantic equivalence for every alternate encoding.','A claim causes a hold when it overlaps the exact target; it is not accepted as a proved mathematical result. Existing 290 admissions are retained, not represented as freshly reviewed here.','Number-level status is preserved separately from the open status of a surviving part or variant.'],'targets':rows,'problems':problems,'partial_claim_scope_notes':claims}
(OUT/'review.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
from collections import Counter
print(Counter(r['decision'] for r in rows))
print('eligible numbers',len({r['number'] for r in rows if r['decision']=='eligible'}))
