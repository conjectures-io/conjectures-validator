import concurrent.futures, datetime, hashlib, json, re, time, urllib.request
from pathlib import Path
root=Path('/tmp/pool-release-20260908/replacement-evidence')
root.mkdir(exist_ok=True)
numbers=[509,522,653]
jobs=[]
for n in numbers:
    for kind,prefix in [('pages',''),('forums','forum/thread/'),('claims','forum/thread/')]:
        (root/kind).mkdir(exist_ok=True)
        jobs.append((f'{kind}/{n}.html',f'https://www.erdosproblems.com/{prefix}{n}'+('/proof-claims' if kind=='claims' else '')))
for n in [10,366]:
    h=(root/'claims'/f'{n}.html').read_text()
    for claim in re.findall(r'id="proof-claim-(\d+)"',h):
        (root/'comments').mkdir(exist_ok=True)
        jobs.append((f'comments/{claim}.html',f'https://www.erdosproblems.com/forum/proof-claims/{claim}/comments'))
def fetch(job):
    path,url=job
    for retry in range(3):
        try:
            data=urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ConjectureAudit/1.0'}),timeout=40).read()
            (root/path).write_bytes(data)
            return dict(path=path,url=url,retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),sha256=hashlib.sha256(data).hexdigest())
        except Exception as e:
            error=str(e);time.sleep(retry+1)
    return dict(path=path,url=url,error=error)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    results=list(pool.map(fetch,jobs))
(root/'backup-fetches.json').write_text(json.dumps(results,indent=2)+'\n')
print('Fetched',len(results),'sources;',sum('error' in x for x in results),'errors',flush=True)
print([x for x in results if 'error' in x],flush=True)
