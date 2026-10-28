"""Archive audited research files without changing application code or inventory."""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from uuid import uuid4

HERE=Path(__file__).resolve().parent
SKIP={'.venv','venv','node_modules','.git','__pycache__','.pytest_cache',
      '.mypy_cache','.ruff_cache','dist','build','.next'}
GROUPS={'research','old_updates','manual_tests','frontend_backups'}
PROTECTED=('backend','frontend','data','captures','start_project.bat','README.md','.gitignore')
RECORD_ROOT='archive/project_organization'


def relative(value):
    if not isinstance(value,str) or '\\' in value:
        raise ValueError('Invalid relative path.')
    p=Path(value)
    if p.is_absolute() or not p.parts or any(x in ('..','.') for x in p.parts) or ':' in value:
        raise ValueError('Invalid relative path: '+value)
    return p


def alias(path):
    return path.is_symlink() or getattr(path,'is_junction',lambda:False)()


def files_under(path):
    if alias(path):raise ValueError('Linked paths require manual review: '+str(path))
    if path.is_file():return {'':path}
    if not path.is_dir():raise ValueError('Missing path: '+str(path))
    files={}
    for folder,dirs,names in os.walk(path,followlinks=False):
        folder=Path(folder)
        for name in dirs:
            if alias(folder/name):raise ValueError('Linked directory requires manual review: '+str(folder/name))
        dirs[:]=sorted(n for n in dirs if n.casefold() not in SKIP)
        for name in sorted(names):
            file=folder/name
            if alias(file):raise ValueError('Linked file requires manual review: '+str(file))
            if file.is_file():files[file.relative_to(path).as_posix()]=file
    return files


def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def hashes(path):
    return {name:sha(file) for name,file in files_under(path).items()}


def write_json(path,value):
    temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,allow_nan=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def load_plan(path):
    plan=json.loads(path.read_text(encoding='utf-8-sig'))
    if plan.get('version')!='project-organization-v1':raise ValueError('Unknown plan version.')
    sources=[]
    for item in plan['moves']:
        p=relative(item['source'])
        if item['group'] not in GROUPS:raise ValueError('Unknown archive group.')
        if p.parts[0] in {'backend','data','captures','.venv','archive'}:
            raise ValueError('Plan may not move runtime storage/source: '+str(p))
        if p.parts[0]=='frontend' and p.as_posix()!='frontend/src/App.before-review.jsx':
            raise ValueError('Only the audited inactive frontend backup may move.')
        sources.append(p.as_posix())
    if len(set(sources))!=len(sources):raise ValueError('Duplicate move entry.')
    for a in sources:
        if any(b.startswith(a+'/') for b in sources if b!=a):raise ValueError('Overlapping move entries.')
    return plan


def preflight(project,plan):
    for name in ('backend','frontend','data','captures'):
        if not (project/name).is_dir():raise ValueError('Missing project folder: '+name)
    # Refuse stale plans instead of archiving newly changed work without review.
    for item in plan['expected_application_sources']:
        file=project/relative(item['path'])
        if not file.is_file() or sha(file)!=item['sha256']:
            raise ValueError('Application source changed since audit: '+item['path']+'. Run a fresh layout audit.')
    expected=plan['archived_file_inventory']
    for move in plan['moves']:
        source=move['source'];path=project/relative(source)
        actual=files_under(path)
        wanted={('' if f['path']==source else f['path'][len(source)+1:]):f
                for f in expected if f['path']==source or f['path'].startswith(source+'/')}
        if set(actual)!=set(wanted):
            raise ValueError('Files added/removed since audit in '+source+'. Run a fresh layout audit.')
        for key,file in actual.items():
            stat=file.stat();entry=wanted[key]
            if stat.st_size!=entry['bytes'] or stat.st_mtime_ns!=entry['mtime_ns']:
                raise ValueError('File changed since audit: '+entry['path'])
            if entry.get('sha256') and sha(file)!=entry['sha256']:
                raise ValueError('Source changed since audit: '+entry['path'])
    # A second on-machine check for new references, including simple runtime globs.
    tokens=[]
    for move in plan['moves']:
        p=Path(move['source']);tokens.append(p.name)
        if len(p.parts)==1 and p.suffix=='.py':tokens.append(p.stem)
    for folder in ('backend','frontend'):
        for name,file in files_under(project/folder).items():
            rel=folder+'/'+name
            if rel=='frontend/src/App.before-review.jsx' or file.suffix not in {'.py','.js','.jsx','.ts','.tsx'}:continue
            text=file.read_text(encoding='utf-8-sig')
            if folder=='frontend' and re.search(r'\bimport\.meta\.glob\s*\(',text):
                raise ValueError('Runtime glob needs source review before moving files: '+rel)
            for token in tokens:
                if re.search(r'(?<![\w])'+re.escape(token)+r'(?![\w])',text):
                    raise ValueError('Runtime source mentions an archive candidate: '+rel+' -> '+token)


def protected_hashes(project,plan):
    excluded={m['source'] for m in plan['moves']}
    result={}
    for prefix in PROTECTED:
        p=project/prefix
        if not p.exists():continue
        for name,file in files_under(p).items():
            rel=prefix+('/'+name if name else '')
            if rel not in excluded:result[rel]=sha(file)
    return result


def get(url):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url,timeout=15) as response:
        if response.status!=200:raise ValueError('HTTP check failed: '+url)
        body=response.read()
    return body


def live_check():
    health=json.loads(get('http://127.0.0.1:8000/api/health'))
    if health.get('status')!='ok':raise ValueError('Backend health is not OK.')
    days=json.loads(get('http://127.0.0.1:8000/api/inventory/days'))
    get('http://127.0.0.1:5173/')
    get('http://127.0.0.1:5173/src/App.jsx')
    get('http://127.0.0.1:5173/src/main.jsx')
    return {'health':{k:health.get(k) for k in ('status','service','version')},'days_and_catalog':days}


def undo_moves(project,operations):
    for item in reversed(operations):
        dest=project/relative(item['destination']);source=project/relative(item['source'])
        if dest.exists():
            if source.exists():raise ValueError('Restore would overwrite a file: '+item['source'])
            source.parent.mkdir(parents=True,exist_ok=True)
            dest.rename(source)


def apply(project,plan):
    print('1/4 Checking audited files and runtime references...',flush=True)
    preflight(project,plan)
    print('2/4 Checking running backend/frontend and hashing protected files...',flush=True)
    baseline=live_check()
    protected=protected_hashes(project,plan)
    inventories={m['source']:hashes(project/m['source']) for m in plan['moves']}
    run=datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid4().hex[:8]
    base=project/RECORD_ROOT/run
    base.mkdir(parents=True,exist_ok=False)
    operations=[{**m,'destination':(Path(RECORD_ROOT)/run/m['group']/Path(m['source']).name).as_posix()}
                for m in plan['moves']]
    record={'version':'project-organization-v1','status':'moving','operations':operations,
            'original_files':inventories,'protected_before':protected,'live_before':baseline}
    journal=base/'manifest.json';write_json(journal,record)
    write_json(project/RECORD_ROOT/'LATEST.json',{'run':run})
    completed=[]
    try:
        print('3/4 Archiving '+str(len(operations))+' paths; no source code edits...',flush=True)
        for item in operations:
            dest=project/item['destination'];dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():raise ValueError('Archive destination already exists: '+str(dest))
            (project/item['source']).rename(dest)
            completed.append(item)
        print('4/4 Verifying every archived file and checking application again...',flush=True)
        for item in completed:
            if hashes(project/item['destination'])!=inventories[item['source']]:
                raise ValueError('Archive hash verification failed: '+item['source'])
        if protected_hashes(project,plan)!=protected:
            raise ValueError('Protected files changed during organization; archive moves will be reversed.')
        after=live_check()
        if after!=baseline:raise ValueError('API data changed during organization; archive moves will be reversed.')
        record.update(status='completed',protected_after_matches=True,live_after=after)
        write_json(journal,record)
        write_json(project/RECORD_ROOT/'LATEST.json',{'run':run})
    except BaseException as error:
        undo_moves(project,completed)
        record.update(status='rolled_back',reason=str(error));write_json(journal,record)
        raise
    print('DONE: '+str(len(operations))+' paths archived. Application/data/capture hashes unchanged.',flush=True)
    print('Archive: '+str(base),flush=True)
    print('Old experiment commands require --undo to restore their original locations.',flush=True)


def undo(project):
    root=project/RECORD_ROOT
    name=json.loads((root/'LATEST.json').read_text(encoding='utf-8'))['run']
    if not isinstance(name,str) or Path(name).name!=name or name in ('.','..'):
        raise ValueError('Invalid archive pointer.')
    journal=root/name/'manifest.json';record=json.loads(journal.read_text(encoding='utf-8'))
    if record.get('status')=='restored':
        print('Already restored.');return
    if record.get('status') not in {'completed','moving','rolled_back'}:
        raise ValueError('Unknown archive operation state.')
    # All checks happen before the first reverse move.
    pending=[]
    for item in record['operations']:
        source=project/relative(item['source']);dest=project/relative(item['destination'])
        if relative(item['source']).parts[0] in {'backend','data','captures','.venv','archive'}:
            raise ValueError('Archive manifest may not restore protected paths.')
        if relative(item['source']).parts[0]=='frontend' and item['source']!='frontend/src/App.before-review.jsx':
            raise ValueError('Unexpected frontend path in archive manifest.')
        expected_prefix=(Path(RECORD_ROOT)/name).as_posix()+'/'
        if not item['destination'].startswith(expected_prefix):raise ValueError('Archive path outside this run.')
        if source.exists() and not dest.exists():
            if hashes(source)!=record['original_files'][item['source']]:
                raise ValueError('Original location contains different files: '+item['source'])
            continue
        if source.exists() or not dest.exists():raise ValueError('Restore path conflict: '+item['source'])
        if hashes(dest)!=record['original_files'][item['source']]:raise ValueError('Archived content changed: '+item['source'])
        pending.append(item)
    restored=[]
    try:
        for item in reversed(pending):
            source=project/item['source'];source.parent.mkdir(parents=True,exist_ok=True)
            (project/item['destination']).rename(source);restored.append(item)
    except BaseException:
        for item in reversed(restored):(project/item['source']).rename(project/item['destination'])
        raise
    record['status']='restored';write_json(journal,record)
    print('RESTORED: all archived paths returned to their original locations. No application files replaced.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--apply',action='store_true');group.add_argument('--undo',action='store_true')
    parser.add_argument('--project',type=Path,default=HERE.parent)
    args=parser.parse_args();project=args.project.resolve()
    if not (project/'backend/app/daily_api.py').is_file():raise ValueError('Cannot locate the project backend.')
    if args.undo:undo(project);return
    plan=load_plan(HERE/'project_layout_plan.json')
    if args.apply:apply(project,plan);return
    for m in plan['moves']:print(m['source']+' -> archive/project_organization/<run>/'+m['group']+'/')
    print(str(len(plan['moves']))+' planned moves. Nothing changed. Use --apply with backend and frontend running.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('STOPPED: '+str(error),file=sys.stderr)
        print('No database/application code was edited. See the archive manifest if an operation began.',file=sys.stderr)
        raise SystemExit(1)
