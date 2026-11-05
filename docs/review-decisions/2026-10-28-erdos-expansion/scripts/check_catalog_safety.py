import json,subprocess
from pathlib import Path
O=Path('/admission');ROOT=Path('/opt/fc-verifier')
r=subprocess.run(['lake','env','lean',str(O/'CatalogSafetyCheck.lean')],cwd=ROOT,capture_output=True,text=True)
(O/'evidence/catalog-safety.log').write_text(r.stdout+r.stderr)
assert r.returncode==0,r.stdout+r.stderr
rows=json.loads(next(x for x in r.stdout.splitlines() if x.startswith('{')))['declarations'];by={r['theorem'].split('.')[-1]:r for r in rows}
assert by['hidden_statement_hole']['contains_sorry_in_type']
assert by['value_question']['classification']=='GENERAL_VALUE_ANSWER'
assert by['value_question']['contains_answer_annotation']
assert not by['ordinary_proposition']['contains_sorry_in_type']
assert by['ordinary_proposition']['classification']=='DIRECT_PROP'
(O/'evidence/catalog-safety.json').write_text(json.dumps({'passed':True,'checks':['transitive sorry through a chosen witness rejected','question value rejected even with a concrete answer definition','ordinary proposition preserved']},indent=2)+'\n')
print('catalog safety checks passed')
