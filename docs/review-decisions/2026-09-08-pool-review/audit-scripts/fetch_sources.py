import concurrent.futures
import datetime
import hashlib
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

r = Path('/tmp/pool-review-20260908')
rows = json.loads((r/'active-targets.json').read_text())
numbers = sorted({int(re.match(r'Erdos(\d+)', x['theorem'])[1]) for x in rows if x['theorem'].startswith('Erdos')})
for folder in ['pages','forums']:
    (r/folder).mkdir(exist_ok=True)
jobs = [(f'{folder}/{n}.html', f'https://www.erdosproblems.com/'+('forum/thread/' if folder == 'forums' else '')+str(n))
        for n in numbers for folder in ['pages','forums']]
jobs.append(('green-problems.pdf', 'https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf'))
jobs.append(('starfleetmath.html', 'https://www.starfleetmath.com/'))
def fetch(job):
    path, url = job
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'ConjectureAudit/1.0'}), timeout=40).read()
            (r/path).write_bytes(data)
            return {'path': path, 'url': url, 'retrieved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'sha256':hashlib.sha256(data).hexdigest()}
        except Exception as e:
            error = str(e)
            time.sleep(attempt+1)
    return {'path': path, 'url': url, 'error': error}
results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    for future in concurrent.futures.as_completed([pool.submit(fetch, job) for job in jobs]):
        results.append(future.result())
        if len(results)%40 == 0:
            print('fetched', len(results), '/', len(jobs), flush=True)
        (r/'source-fetches.json').write_text(json.dumps(results, indent=2)+'\n')
print('Fetch errors', [x for x in results if 'error' in x], flush=True)
for repo in ['google-deepmind/formal-conjectures', 'teorth/erdosproblems']:
    p = subprocess.run(['gh','api',f'repos/{repo}/commits/main','--jq','.sha'],capture_output=True,text=True)
    print(repo,p.returncode,p.stdout.strip(),p.stderr.strip(),flush=True)
    (r/(repo.split('/')[-1]+'-head.txt')).write_text(p.stdout+p.stderr)
