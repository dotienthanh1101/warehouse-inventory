"""Read-only lookup of missing evidence in project files and ZIP directories.

No extraction, copying, deletion, database write or backup policy change.
Only writes a private diagnostic JSON under the project's docs directory.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4
from zipfile import ZipFile, BadZipFile

SKIP={'.venv','venv','node_modules','.git','__pycache__'}


def collect(project):
    sys.path.insert(0,str(project/'tools'))
    from backup_core import sqlite_info, image_urls, image_path
    storage=Path(os.environ.get('WAREHOUSE_STORAGE_ROOT',project/'data/warehouses')).resolve()
    sources=[('legacy',project)]
    if storage.is_dir():
        sources.extend((folder.name,folder) for folder in sorted(storage.iterdir())
                       if folder.is_dir() and re.fullmatch('[0-9a-f]{32}',folder.name))
    missing=[]
    for scope,folder in sources:
        references=set()
        database=folder/'data/warehouse.db'
        if database.is_file():
            references.update(sqlite_info(database)[1])
        for kind in ('reviews','duplicate_checks'):
            directory=folder/'data'/kind
            if directory.is_dir():
                for file in directory.rglob('*.json'):
                    references.update(image_urls(json.loads(file.read_text(encoding='utf-8-sig'))))
        absent=[image_path(folder,url) for url in sorted(references)
                if not image_path(folder,url).is_file()]
        missing.extend({'scope':scope,'expected_path':str(path),'filename':path.name} for path in absent)
    return missing


def search(root,names):
    candidates={name:[] for name in names}
    errors=[]
    archive_count=0
    def walk_error(error):errors.append({'path':str(error.filename),'reason':type(error).__name__})
    for folder,dirs,files in os.walk(root,followlinks=False,onerror=walk_error):
        base=Path(folder)
        dirs[:]=[name for name in dirs if name not in SKIP and not (base/name).is_symlink()
                 and not (getattr((base/name).lstat(),'st_file_attributes',0)&0x400)]
        for name in files:
            path=base/name
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:
                continue
            if name in candidates:
                candidates[name].append({'type':'file','path':str(path)})
            if path.suffix.lower()!='.zip':continue
            archive_count+=1
            try:
                with ZipFile(path) as archive:
                    for item in archive.infolist():
                        if item.is_dir():continue
                        filename=PurePosixPath(item.filename.replace('\\','/')).name
                        if filename in candidates:
                            candidates[filename].append({'type':'zip_entry','path':str(path),
                                                        'member':item.filename,'bytes':item.file_size})
            except (OSError,BadZipFile,RuntimeError,NotImplementedError) as error:
                errors.append({'path':str(path),'reason':type(error).__name__})
    return candidates,errors,archive_count


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='Tim anh bang chung that lac; khong sua du lieu.')
    parser.add_argument('--project',type=Path,default=Path.cwd())
    args=parser.parse_args()
    project=args.project.resolve()
    if not (project/'tools/backup_core.py').is_file():
        raise ValueError('Hay chay tai thu muc warehouse_inventory da cai goi backup.')
    missing=collect(project)
    names={item['filename'] for item in missing}
    print('Dang tim file va muc luc ZIP trong: '+str(project.parent),flush=True)
    candidates,errors,archives=search(project.parent,names) if names else ({},[],0)
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'search_root':str(project.parent),
            'read_only_business_data':True,'scope':'SQLite and saved review/check references; not a PostgreSQL task audit',
            'missing_references':missing,'candidates':candidates,'unreadable_locations':errors,
            'archives_checked':archives,'zip_content_validated':False}
    docs=project/'docs';docs.mkdir(exist_ok=True)
    report=docs/('missing_evidence_search_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]+'.json')
    with report.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    os.chmod(report,0o600)
    for scope in sorted({item['scope'] for item in missing}):
        items=[item for item in missing if item['scope']==scope]
        found=sum(bool(candidates[item['filename']]) for item in items)
        print(f'{scope}: THIEU {len(items)} | TIM THAY UNG VIEN {found} | CHUA TIM THAY {len(items)-found}')
    print('ZIP DA QUET: '+str(archives))
    print('VI TRI KHONG DOC DUOC: '+str(len(errors)))
    print('BAO CAO: '+str(report))
    print('Chi tim vi tri; chua khoi phuc, chua xac minh noi dung ung vien va khong sua kho.')


if __name__=='__main__':
    try:main()
    except (Exception,KeyboardInterrupt) as error:
        print('CHUA HOAN TAT: '+str(error),file=sys.stderr)
        raise SystemExit(1)
