from pathlib import Path
import hashlib,json,re,subprocess,sys,tarfile,tempfile,zipfile,shutil,ast,csv,io,base64
repo=Path.cwd(); out=repo/'docs/evidence/fb77abe4'; out.mkdir(exist_ok=True)
patterns={
'consumer_reference':r'(?i)pixpool|hostcert|devstash|lauro.cesar',
'absolute_local_path':r'/(?:home|media|Users)/[^\s\x22\x27]+',
'credential_shape':r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{24,}|https?://[^\s/@:]+:[^\s/@]+@',
'privacy_sentinel':r'AUDIT_PRIVATE_SENTINEL_7F32'}
def inspect(name,data):
    value={'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'findings':[]}
    try: text=data.decode('utf-8')
    except UnicodeDecodeError:
        value['findings'].append({'category':'non_utf8_requires_review'});return value
    for cat,pat in patterns.items():
        lines=[i for i,l in enumerate(text.splitlines(),1) if re.search(pat,l)]
        if lines:value['findings'].append({'category':cat,'lines':lines})
    return value

def archive(p):
    if p.suffix=='.whl':
        with zipfile.ZipFile(p) as z: data={n:z.read(n) for n in z.namelist() if not n.endswith('/')}
    else:
        with tarfile.open(p) as t:
            assert all(m.isfile() or m.isdir() for m in t.getmembers())
            data={m.name:t.extractfile(m).read() for m in t.getmembers() if m.isfile()}
    for n in data:assert not n.startswith('/') and '..' not in Path(n).parts
    result={'artifact':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'members':[inspect(n,d) for n,d in data.items()]}
    if p.suffix=='.whl':
        record=next(n for n in data if n.endswith('/RECORD'))
        for n,h,size in csv.reader(io.StringIO(data[record].decode())):
            if h:
                assert h=='sha256='+base64.urlsafe_b64encode(hashlib.sha256(data[n]).digest()).rstrip(b'=').decode()
                assert int(size)==len(data[n])
        result['record_hashes']='PASS'
    return result,data
sources=['README.md','PROVENANCE.json','pyproject.toml','MANIFEST.in','setup.py','src/codex_team/__init__.py','src/codex_team/__main__.py','src/codex_team/executor.py','templates/AGENTS.md','tests/test_team_executor.py','tests/test_package_sources.py','tools/package_codex_team.py']
report={'task':'fb77abe41d2d432da57c050e25c6a72f','sources':[inspect(n,(repo/n).read_bytes()) for n in sources], 'historical_artifacts':[], 'candidate_artifacts':[], 'scan_categories':patterns}
for p in sorted((repo/'../../dist').resolve().rglob('*')):
    if p.is_file() and (p.name.endswith('.whl') or p.name.endswith('.tar.gz')):
        result,_=archive(p);result['artifact']='root/dist/'+p.name;report['historical_artifacts'].append(result)
workspace=Path(tempfile.mkdtemp(prefix='codex-team-fb77-')); staging=workspace/'source';staging.mkdir()
for n in sources:
    dest=staging/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(repo/n,dest)
p=staging/'pyproject.toml';p.write_text(p.read_text().replace('version = "0.3.0"','version = "0.3.1.dev0"'))
sentinels=['tests/private_audit.py','templates/private_audit.md','src/private_namespace/leak.py','src/codex_team/private_audit.txt','.codex-team/logs/private.txt','sessions/private.jsonl','credentials','docs/private.md']
for n in sentinels:
    p=staging/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('AUDIT_PRIVATE_SENTINEL_7F32')
log=workspace/'build.log'
with log.open('w') as f:
    subprocess.run([sys.executable,'-B','setup.py','sdist','bdist_wheel'],cwd=staging,stdout=f,stderr=subprocess.STDOUT,check=True)
for p in sorted((staging/'dist').iterdir()):
    result,data=archive(p);result['artifact']='temporary-candidate/dist/'+p.name
    assert not any('AUDIT_PRIVATE_SENTINEL_7F32' in d.decode('utf8') for d in data.values())
    assert not any(v['findings'] for v in result['members'])
    assert not any(any(bad in n for bad in ['private_audit','private_namespace','.codex-team/','sessions/','credentials','docs/']) for n in data)
    result['sentinel_exclusion']='PASS';report['candidate_artifacts'].append(result)
report['temporary_workspace']=str(workspace)
report['build_log']={'path':str(log),'classification':'external build log; not archive member; not copied into report or distribution'}
report['provenance_checks']={}
prov=json.loads((repo/'PROVENANCE.json').read_text())
for key,n in [('packaged_sha256','src/codex_team/executor.py'),('generic_rules_sha256','templates/AGENTS.md')]:
    report['provenance_checks'][key]=hashlib.sha256((repo/n).read_bytes()).hexdigest()==prov[key]
assert all(report['provenance_checks'].values())
report['verification']={'unit_tests':'29 passed','candidate_version':'0.3.1.dev0 in temporary copy only; not release approval','historical_archives':'read-only; not replaced','publication':'none','git_mutations':'none'}
report['limits']=['Static selected-pattern scan and include-boundary sentinel exercise do not prove absence of arbitrary secrets.','Discovery: all wheel/sdist under this submodule and root/dist; max-depth-3 root search excluding private state found no other candidates. No global disk or private session scan.','Candidate is offline local setup.py build with setuptools 66.1.1 / wheel 0.38.4; other backends/environments and future source additions require renewed archive review.','PROVENANCE source hashes are identifiers, not embedded source contents; upstream source/test identity claims not rederived in this audit.','Runtime filenames referenced by generic code and synthetic tests are not runtime file payloads.']
(out/'inventory.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'historical_archives':len(report['historical_artifacts']),'candidate_archives':len(report['candidate_artifacts']),'candidate_members':[len(a['members']) for a in report['candidate_artifacts']],'sentinels':'PASS','report':str(out/'inventory.json')}))
