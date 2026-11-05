import concurrent.futures,gzip,hashlib,json,time,urllib.request
from pathlib import Path
from html.parser import HTMLParser
ROOT=Path('/admission') if Path('/admission').exists() else Path(__file__).parent
OUT=ROOT/'evidence/primary';OUT.mkdir(exist_ok=True)
STAGE=ROOT.parent.parent/'conjectures-tasks/staged/erdos-328-20261028'
if not STAGE.exists():STAGE=Path('/root/.codex/worktrees/add-all-328-erdos/conjectures-tasks/staged/erdos-328-20261028')
nums=[p['number'] for p in json.loads((STAGE/'manifest.json').read_text())['problems']]
class Parser(HTMLParser):
 def __init__(self):super().__init__();self.skip=0;self.text=[];self.links=[]
 def handle_starttag(self,t,a):
  if t in ['script','style']:self.skip+=1
  if t in ['br','p','div','li','h1','h2','h3']:self.text.append('\n')
  if t=='a':
   h=dict(a).get('href','')
   if h.startswith(('https://','http://','/forum/')):self.links.append(h)
 def handle_endtag(self,t):
  if t in ['script','style']:self.skip=max(0,self.skip-1)
 def handle_data(self,d):
  if not self.skip:self.text.append(d)
def fetch(item):
 n,kind=item;u=f'https://www.erdosproblems.com/{"latex/" if kind=="latex" else "forum/thread/"}{n}';u += '/proof-claims' if kind == 'claims' else '';meta=OUT/f'{n}-{kind}.json'
 if meta.exists():return json.loads(meta.read_text())
 error=None
 for attempt in range(3):
  try:
   req=urllib.request.Request(u,headers={'User-Agent':'Conjectures research audit (Codex; https://github.com/conjectures-io/conjectures-validator)'})
   with urllib.request.urlopen(req,timeout=45) as r:data=r.read(10*1024*1024);status=r.status
   html=data.decode();p=Parser();p.feed(html)
   text='\n'.join(' '.join(line.split()) for line in ''.join(p.text).splitlines() if line.strip())
   (OUT/f'{n}-{kind}.html.gz').write_bytes(gzip.compress(data,mtime=0));(OUT/f'{n}-{kind}.txt').write_text(text+'\n')
   row={'number':n,'kind':kind,'url':u,'http_status':status,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'links':sorted(set(p.links))};meta.write_text(json.dumps(row,indent=2)+'\n');return row
  except Exception as e:error=str(e);time.sleep(2**attempt)
 row={'number':n,'kind':kind,'url':u,'error':error};meta.write_text(json.dumps(row,indent=2)+'\n');return row
import re
items=[(n,'claims') for n in nums if re.search(r'Proof claims \(([1-9]\d*)\)', (OUT/f'{n}-forum.txt').read_text())]
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 for i,r in enumerate(pool.map(fetch,items),1):
  rows.append(r)
  if i%40==0:print(i,len(items),sum('error' in r for r in rows),'errors',flush=True)
(ROOT/'evidence/claims-fetches.json').write_text(json.dumps(rows,indent=2)+'\n')
print('complete',len(rows),sum('error' in r for r in rows),'errors',flush=True)
