"""Delete audited obsolete archive files after verified external backup. No app edits."""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

HERE=Path(__file__).resolve().parent


def file_sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def helpers(plan):
    path=HERE/'organize_project.py'
    if not path.is_file() or file_sha(path)!=plan['helper_sha256']:
        raise ValueError('Existing organize_project.py differs from the audited helper. Nothing deleted.')
    spec=importlib.util.spec_from_file_location('verified_layout_helpers',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def archive_path(project,name):
    if not isinstance(name,str) or '\\' in name or ':' in name:raise ValueError('Invalid archive path.')
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='archive':
        raise ValueError('Deletion/restoration is limited to audited archive paths.')
    target=project/p
    for parent in [target]+list(target.parents):
        if parent==project:break
        if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():
            raise ValueError('Linked archive paths require manual review.')
    if not target.resolve().is_relative_to((project/'archive').resolve()):
        raise ValueError('Linked path outside project archive.')
    return target


def stream_sha(stream):
    h=hashlib.sha256()
    for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def extracted_zip_matches(project,rule):
    """Every original ZIP member needs a byte-identical retained loose copy."""
    source=archive_path(project,rule['path'])
    try:
        with zipfile.ZipFile(source) as z:
            members=[i for i in z.infolist() if not i.is_dir()]
            if not members or len({i.filename for i in members})!=len(members):return False
            for info in members:
                name=info.filename
                if '\\' in name or ':' in name or Path(name).is_absolute() or '..' in Path(name).parts:return False
                target_name=rule.get('overrides',{}).get(name,rule['default_prefix']+'/'+name)
                target=archive_path(project,target_name)
                if not target.is_file() or target.stat().st_size!=info.file_size:return False
                with z.open(info) as f:
                    if stream_sha(f)!=file_sha(target):return False
        return True
    except (OSError,zipfile.BadZipFile,RuntimeError):return False


def preflight(project,plan):
    if plan.get('version')!='project-cleanup-v1':raise ValueError('Unknown cleanup plan.')
    for item in plan['expected_application_sources']:
        path=project/item['path']
        if not path.is_file() or file_sha(path)!=item['sha256']:
            raise ValueError('Application source changed since the audit: '+item['path'])
    names=[i['path'] for i in plan['candidates']]
    if len(set(names))!=len(names):raise ValueError('Duplicate deletion entry.')
    for item in plan['candidates']:
        path=archive_path(project,item['path'])
        if not path.is_file() or path.is_symlink():raise ValueError('Missing/linked candidate: '+item['path'])
        stat=path.stat()
        if stat.st_size!=item['bytes'] or stat.st_mtime_ns!=item['mtime_ns']:
            raise ValueError('Candidate changed since the audit: '+item['path'])
        if item.get('sha256') and file_sha(path)!=item['sha256']:
            raise ValueError('Candidate content changed since the audit: '+item['path'])


def backup_files(project,selected,backup):
    entries=[]
    for item in selected:
        source=archive_path(project,item['path']);stat=source.stat()
        entries.append({'path':item['path'],'sha256':file_sha(source),
                        'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns})
    with zipfile.ZipFile(backup,'x',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as z:
        for entry in entries:z.write(project/entry['path'],'files/'+entry['path'])
        z.writestr('manifest.json',json.dumps({'version':'project-cleanup-v1','files':entries},indent=2))
    with zipfile.ZipFile(backup) as z:
        for entry in entries:
            with z.open('files/'+entry['path']) as f:
                if stream_sha(f)!=entry['sha256']:raise ValueError('Backup checksum failed.')
            if file_sha(project/entry['path'])!=entry['sha256']:
                raise ValueError('Candidate changed during backup. Nothing deleted.')
    return entries


def apply(project,plan,h):
    print('1/4 Checking audited sources/candidates and extracted datasets...',flush=True)
    preflight(project,plan)
    zip_keep={r['path'] for r in plan['dataset_zip_rules'] if not extracted_zip_matches(project,r)}
    selected=[i for i in plan['candidates'] if i['path'] not in zip_keep]
    baseline=h.live_check();protected=h.protected_hashes(project,{'moves':[]})
    # One rollback ZIP outside the working project, instead of hundreds of loose tests.
    run=datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid4().hex[:8]
    backup_dir=project.parent/(project.name+'_backups');backup_dir.mkdir(exist_ok=True)
    backup=backup_dir/('cleanup_'+run+'.zip')
    print('2/4 Creating and checksum-verifying external rollback ZIP...',flush=True)
    entries=backup_files(project,selected,backup)
    stage=backup_dir/('_staging_'+run);stage.mkdir()
    moved=[]
    try:
        print('3/4 Removing '+str(len(entries))+' obsolete files from the project...',flush=True)
        for entry in entries:
            source=archive_path(project,entry['path']);dest=stage/entry['path']
            dest.parent.mkdir(parents=True,exist_ok=True)
            source.rename(dest);moved.append(entry)
        print('4/4 Verifying application, inventory data and frontend...',flush=True)
        if h.protected_hashes(project,{'moves':[]})!=protected:
            raise ValueError('Protected files changed during cleanup; cleanup will be reversed.')
        if h.live_check()!=baseline:
            raise ValueError('Live application check changed; cleanup will be reversed.')
        for entry in moved:
            if file_sha(stage/entry['path'])!=entry['sha256']:raise ValueError('Staged file checksum mismatch.')
        receipt={'version':'project-cleanup-v1','backup_zip':str(backup),
                 'deleted_files':len(entries),'bytes_removed_from_project':sum(e['bytes'] for e in entries),
                 'retained_unverified_zip_files':sorted(zip_keep),'application_hashes_unchanged':True,
                 'live_before_after_match':True,'backup_verified':True}
        h.write_json(project/'docs/project_cleanup_latest.json',receipt)
    except BaseException:
        for entry in reversed(moved):
            original=archive_path(project,entry['path'])
            if original.exists():raise ValueError('Rollback path occupied. Verified backup remains at '+str(backup))
            original.parent.mkdir(parents=True,exist_ok=True);(stage/entry['path']).rename(original)
        raise
    # Only staged, already-backed-up archive files are permanently deleted here.
    shutil.rmtree(stage,ignore_errors=True)
    empty=[]
    for folder,dirs,_ in os.walk(project/'archive',followlinks=False):
        parent=Path(folder)
        dirs[:]=[n for n in dirs if not (parent/n).is_symlink()
                 and not getattr(parent/n,'is_junction',lambda:False)()]
        empty.extend(parent/n for n in dirs)
    empty.sort(key=lambda p:len(p.parts),reverse=True)
    for folder in empty:
        try:folder.rmdir()
        except OSError:pass
    print('DONE: '+str(len(entries))+' obsolete files deleted from project.',flush=True)
    print('Application, SQLite, captures and retained research results unchanged.',flush=True)
    print('Rollback ZIP outside project: '+str(backup),flush=True)
    if zip_keep:print('Kept ZIPs without full duplicate proof: '+str(len(zip_keep)),flush=True)
    if stage.exists():print('Some staged backup files remain: '+str(stage),flush=True)


def restore(project):
    pointer=project/'docs/project_cleanup_latest.json'
    receipt=json.loads(pointer.read_text(encoding='utf-8'))
    with zipfile.ZipFile(receipt['backup_zip']) as z:
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('version')!='project-cleanup-v1':raise ValueError('Unknown backup format.')
        files=manifest['files']
        if len({e['path'] for e in files})!=len(files):raise ValueError('Duplicate backup paths.')
        pending=[]
        for entry in files:
            target=archive_path(project,entry['path'])
            with z.open('files/'+entry['path']) as stream:
                if stream_sha(stream)!=entry['sha256']:raise ValueError('Backup corrupted.')
            if target.exists():
                if file_sha(target)!=entry['sha256']:raise ValueError('Restore would overwrite new work: '+entry['path'])
            else:pending.append(entry)
        created=[]
        try:
            for entry in pending:
                target=archive_path(project,entry['path']);target.parent.mkdir(parents=True,exist_ok=True)
                with z.open('files/'+entry['path']) as source,target.open('xb') as destination:
                    created.append(target);shutil.copyfileobj(source,destination)
                os.utime(target,ns=(entry['mtime_ns'],entry['mtime_ns']))
        except BaseException:
            for file in created:file.unlink(missing_ok=True)
            raise
    print('RESTORED: deleted archive files restored. Existing app/inventory files were not replaced.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--apply',action='store_true');mode.add_argument('--restore',action='store_true')
    args=parser.parse_args();project=HERE.parent
    if not (project/'backend/app/daily_api.py').is_file():raise ValueError('Extract into warehouse_inventory first.')
    if args.restore:restore(project);return
    plan=json.loads((HERE/'project_cleanup_plan.json').read_text(encoding='utf-8'))
    if args.apply:apply(project,plan,helpers(plan));return
    for item in plan['candidates']:print(item['reason']+' | '+item['path'])
    print(str(len(plan['candidates']))+' candidates. Nothing deleted. Use --apply with backend and frontend running.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('STOPPED: '+str(error),file=sys.stderr);raise SystemExit(1)
