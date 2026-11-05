import json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path('/opt/fc-verifier');OUT=Path('/admission');SOURCE=ROOT/'vendor/formal-conjectures';sys.path.insert(0,str(ROOT))
from verifier.catalog import _extract_json,_normalize_declaration,load_catalog
from verifier.classification import catalog_statistics
from verifier.environment import tool_path,trusted_environment
from verifier.hashing import pretty_json
from verifier.models import Catalog
from verifier.references import extract_module_references
from verifier.repository import assert_repository_commit,lean_toolchain,mathlib_pin
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip();assert_repository_commit(SOURCE,commit)
manifest=json.loads(Path('/candidate-tasks/staged/erdos-328-20261028/manifest.json').read_text())
paths={p['source_path'] for p in manifest['problems']}|set(json.loads((OUT/'retained-paths.json').read_text()))
scope=ROOT/'.work/admission-catalog-scope'
for rel in sorted(paths):
 p=scope/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('')
home=ROOT/'.work/admission-catalog-home';(home/'.tmp').mkdir(parents=True,exist_ok=True)
start=time.monotonic()
r=subprocess.run([str(tool_path(ROOT,'lake')),'env',str(ROOT/'.lake/build/bin/catalog_extractor'),str(scope/'FormalConjectures')],cwd=ROOT,env=trusted_environment(ROOT,home),capture_output=True,text=True,timeout=1800)
(OUT/'evidence/catalog-extractor.stdout').write_text(r.stdout);(OUT/'evidence/catalog-extractor.stderr').write_text(r.stderr)
if r.returncode:raise RuntimeError(r.stdout[-2000:]+r.stderr[-2000:])
raw=_extract_json(r.stdout);(OUT/'compiled-declarations.json').write_text(json.dumps(raw,indent=2,ensure_ascii=False)+'\n')
decls=tuple(_normalize_declaration(d,extract_module_references(SOURCE/d['source_path'])) for d in raw['declarations'])
catalog=Catalog(schema_version=1,repository_commit=commit,lean_toolchain=lean_toolchain(SOURCE),mathlib_commit=mathlib_pin(SOURCE),generated_by='lean/CatalogExtractor.lean; rebuilt release scope: 328 requested Erdős files plus every retained production target module',extraction_duration_ms=int(1000*(time.monotonic()-start)),declarations=tuple(sorted(decls,key=lambda d:d.theorem)))
(OUT/'catalog.json').write_text(pretty_json(catalog.to_dict()));(OUT/'catalog-summary.json').write_text(pretty_json(catalog_statistics(catalog.declarations)))
old=load_catalog(Path('/candidate-validator/data/catalog.json'));by={x.theorem:x for x in decls};selected=json.loads(Path('/candidate-tasks/staged/erdos-328-20261028/baseline/tiers/tier-1/selection-audit.json').read_text()) if False else json.loads(Path('/candidate-tasks/tiers/tier-1/selection-audit.json').read_text())
names={x['theorem'] for x in selected['selected']};changed=[{'theorem':d.theorem,'old_type':d.type_pretty,'new_type':by[d.theorem].type_pretty,'old_hash':d.type_hash,'new_hash':by[d.theorem].type_hash} for d in old.declarations if d.theorem in names and d.type_hash!=by[d.theorem].type_hash]
(OUT/'evidence/retained-type-comparison.json').write_text(json.dumps({'retained_count':len(names),'changes':changed},indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'modules':len(paths),'declarations':len(decls),'retained_type_changes':len(changed)}))
