import pathlib,json,sys,concurrent.futures,time
sys.path.insert(0,'/release')
from verifier.catalog import load_catalog
from verifier.task_generator import generate_task
from verifier.workspace import target_validator
from verifier.task_loader import load_task_bundle
r=pathlib.Path('/audit');p=pathlib.Path('/opt/fc-verifier');c=load_catalog(pathlib.Path('/release/data/catalog.json'));ds={x.theorem:x for x in c.declarations};rows=['Erdos653.erdos_653'];out=[]
(r/'bundles').mkdir(exist_ok=True)
def job(args):
 n,m=args;path=r/'bundles'/(n.replace('.','-').lower()+'-'+m);t=time.monotonic()
 try:
  if not path.exists():generate_task(catalog=c,declaration=ds[n],mode=m,output=path,validate_target=target_validator(p))
  b=load_task_bundle(path);return {'theorem':n,'mode':m,'status':'passed','bundle':str(path),'sha256':b.sha256,'seconds':round(time.monotonic()-t,2)}
 except Exception as e:return {'theorem':n,'mode':m,'status':'failed','error':str(e)}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 for x in pool.map(job,[(x,m) for x in rows for m in ['formalized','counterexample']]):
  out.append(x);(r/'generation-last.json').write_text(json.dumps(out,indent=2)); print(len(out),x['status'],x['theorem'],x['mode'],x.get('error',''),flush=True)
