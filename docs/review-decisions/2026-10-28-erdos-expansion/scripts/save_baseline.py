import subprocess,tarfile,io,json
from pathlib import Path
O=Path(__file__).resolve().parent;T=O.parents[1]/'conjectures-tasks';dest=O/'baseline-tasks'
if not dest.exists():
 dest.mkdir();data=subprocess.check_output(['git','archive','e46f6c64','task-versions.json','allowlist.json','pool','versions','tiers','history'],cwd=T)
 with tarfile.open(fileobj=io.BytesIO(data)) as f:f.extractall(dest,filter='data')
if not (dest/'history').exists():
 data=subprocess.check_output(['git','archive','e46f6c64','history'],cwd=T)
 with tarfile.open(fileobj=io.BytesIO(data)) as f:f.extractall(dest,filter='data')
print(dest)
