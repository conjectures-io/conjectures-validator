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
(r/'claims').mkdir(exist_ok=True)
jobs = [(f"claims/{x['number']}.html", f"https://www.erdosproblems.com/forum/thread/{x['number']}/proof-claims")
        for x in json.loads((r/'page-text.json').read_text()) if re.search(r'Proof claims \([1-9][0-9]*\)', x['text'])]
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
        (r/'claim-fetches.json').write_text(json.dumps(results, indent=2)+'\n')
print('Fetch errors', [x for x in results if 'error' in x], flush=True)
