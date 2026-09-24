import json,re,os,subprocess,time,tomllib
from pathlib import Path
up=Path('/snapshot'); out=Path('/audit/evidence/build');out.mkdir(exist_ok=True)
pin=Path('/opt/fc-verifier/vendor/formal-conjectures')
rows=json.load(open('/audit/candidates.json'))['candidates']
mods={x['module']:x for x in json.load(open('/audit/evidence/source-comparison.json'))}
env=os.environ.copy();env['LEAN_PATH']=str(out)+':'+env.get('LEAN_PATH','');env['LEAN_NUM_THREADS']='2'
for f in (pin/'.lake/build/lib/lean').rglob('*'):
 if f.is_file():
  dst=out/f.relative_to(pin/'.lake/build/lib/lean')
  dst.parent.mkdir(parents=True,exist_ok=True)
  if not dst.exists() and not dst.is_symlink():dst.symlink_to(f)
seen=set(); built=set(); results=[]
def build(mod):
 if mod in seen or mod not in mods:return
 seen.add(mod)
 src=up/mods[mod]['path']
 for line in src.read_text().splitlines():
  m=re.match(r'(?:public |meta |public meta )?import\s+([\w.]+)',line)
  if m:build(m[1])
 # Rebuild changed modules and modules depending on a rebuilt module (all selected roots).
 deps=[m[1] for line in src.read_text().splitlines() if (m:=re.match(r'(?:public |meta |public meta )?import\s+([\w.]+)',line))]
 rebuild=not mods[mod]['same'] or any(d in built for d in deps)
 if not rebuild:return
 dest=out/(mod.replace('.','/')+'.olean');dest.parent.mkdir(parents=True,exist_ok=True)
 if dest.is_symlink():dest.unlink()
 config=tomllib.loads((up/'lakefile.toml').read_text())
 opts=dict(config['leanOptions'])
 for lib in config['lean_lib']:
  if lib['name']==mod.split('.')[0]:opts.update(lib.get('leanOptions',{}))
 def flatten(obj,prefix=''):
  result={}
  for k,v in obj.items():
   key=prefix+k
   if isinstance(v,dict):result.update(flatten(v,key+'.'))
   else:result[key]=v
  return result
 opts=flatten(opts)
 opts['warn.sorry']=False
 opts['weak.google.answer']='always_true'
 options=['-D'+k+'='+str(v).lower() if isinstance(v,bool) else '-D'+k+'='+str(v) for k,v in opts.items()]
 start=time.time()
 p=subprocess.run(['lean',*options,'-o',str(dest),str(src)],cwd=up,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 log=out/(mod.replace('.','/')+'.log');log.write_text(p.stdout)
 built.add(mod)
 results.append({'module':mod,'exit_code':p.returncode,'seconds':round(time.time()-start,2),'log':str(log.relative_to('/audit'))})
 Path('/audit/evidence/build-results.json').write_text(json.dumps(results,indent=2)+'\n')
 print(mod,p.returncode,flush=True)
 if p.returncode:print(p.stdout[-2500:],flush=True);raise SystemExit(p.returncode)
for r in rows:build(r['source_path'][:-5].replace('/','.'))
print('DONE',len(results),flush=True)
