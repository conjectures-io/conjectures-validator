import json,re,hashlib
from pathlib import Path
up=Path('/snapshot'); pin=Path('/opt/fc-verifier/vendor/formal-conjectures')
rows=json.load(open('/audit/candidates.json'))['candidates']
seen={}
def walk(mod):
 if mod in seen:return
 p=Path(mod.replace('.','/')+'.lean'); a=up/p;b=pin/p
 if not a.exists():return
 seen[mod]={'module':mod,'path':str(p),'same':b.exists() and a.read_bytes()==b.read_bytes(),'pinned_exists':b.exists(),'upstream_sha256':hashlib.sha256(a.read_bytes()).hexdigest()}
 for line in a.read_text().splitlines():
  m=re.match(r'(?:public |meta |public meta )?import\s+([\w.]+)',line)
  if m:walk(m[1])
for r in rows:walk(r['source_path'][:-5].replace('/','.'))
Path('/audit/evidence/source-comparison.json').write_text(json.dumps(list(seen.values()),indent=2)+'\n')
print('FC dependency modules:',len(seen),'different:',sum(not x['same'] for x in seen.values()))
for x in seen.values():
 if not x['same']:print(x['module'], 'changed' if x['pinned_exists'] else 'new')
