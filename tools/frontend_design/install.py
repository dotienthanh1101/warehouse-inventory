"""Cai giao dien navy/blue da doi chieu; sao luu va build truoc khi ket luan."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from datetime import datetime
from uuid import uuid4
from zipfile import ZipFile

EXPECTED_MANIFEST_SHA = '43009803e8e98d71219ea4f2731fe7ab17e088c607a87b6d4ccb1ccffec3fc0f'
ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(root, relative):
    p=PurePosixPath(relative)
    if p.is_absolute() or '\\' in relative or ':' in relative or '..' in p.parts:
        raise ValueError('Duong dan khong hop le.')
    target=root.joinpath(*p.parts)
    for item in (target,*target.parents):
        if item==root:break
        if item.exists() or item.is_symlink():
            flags=getattr(item.lstat(),'st_file_attributes',0)
            if item.is_symlink() or flags&0x400:raise ValueError('Khong ghi qua link/junction: '+relative)
    return target


def atomic(path, content):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.design-pending')
    with temp.open('wb') as f:f.write(content);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def record(path, data):
    atomic(path,json.dumps(data,ensure_ascii=False,indent=2).encode())


def restore(root, receipt):
    receipt=Path(receipt).resolve();doc=json.loads(receipt.read_text(encoding='utf-8'))
    if doc.get('format')!='warehouse-frontend-design-receipt-v1':raise ValueError('Sai bien ban.')
    changes=doc['changes']
    for row in changes:
        rel=row['path']
        if not rel.startswith('frontend/src/') or not rel.endswith(('.jsx','.css')):raise ValueError('Ngoai pham vi frontend.')
        dst=safe(root,rel)
        if dst.exists() and sha(dst) not in {row['after_sha256'],row.get('before_sha256')}:
            raise ValueError('File da duoc sua sau khi cai: '+rel+'. Khong ghi de.')
        if row['existed'] and sha(receipt.parent/'originals'/rel)!=row['before_sha256']:
            raise ValueError('Ban sao luu khong khop: '+rel)
    for row in changes:
        dst=safe(root,row['path'])
        if row['existed']:atomic(dst,(receipt.parent/'originals'/row['path']).read_bytes())
        elif dst.exists():dst.unlink()
    doc['status']='rolled_back';record(receipt,doc)
    return doc


def check_guards(root, manifest, allow_after=False):
    for relative,expected in manifest['guards'].items():
        path=safe(root,relative)
        allowed={expected}
        if allow_after and relative in manifest['files']:allowed.add(manifest['files'][relative]['after_sha256'])
        if not path.is_file() or sha(path) not in allowed:
            raise ValueError('Khac ban ma da doi chieu: '+relative+'. Chua sua file nao.')


def apply(root, manifest, payload, do_build=True):
    root=Path(root).resolve();check_guards(root,manifest,True)
    files=manifest['files'];current={}
    with ZipFile(payload) as z:
        if z.testzip() is not None or set(z.namelist())!=set(files):raise ValueError('Payload khong khop.')
        contents={rel:z.read(rel) for rel in files}
    for rel,entry in files.items():
        if not rel.startswith('frontend/src/') or not rel.endswith(('.jsx','.css')):raise ValueError('Payload vuot pham vi frontend.')
        if hashlib.sha256(contents[rel]).hexdigest()!=entry['after_sha256']:raise ValueError('Payload da doi: '+rel)
        p=safe(root,rel);current[rel]=sha(p) if p.is_file() else None
        if current[rel] not in {entry.get('before_sha256'),entry['after_sha256']}:
            raise ValueError('File hien tai khong khop: '+rel+'. Chua sua file nao.')
    if all(current[rel]==entry['after_sha256'] for rel,entry in files.items()):return {'status':'already_installed'}
    if any(current[rel]==entry['after_sha256'] for rel,entry in files.items()):raise ValueError('Dang co ban cai do dang; can doi chieu truoc.')
    tag=datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    folder=safe(root,'archive/frontend_design/'+tag);folder.mkdir(parents=True,exist_ok=False)
    receipt=folder/'receipt.json'
    changes=[]
    for rel,entry in files.items():
        p=safe(root,rel);existed=p.is_file()
        if existed:
            backup=folder/'originals'/rel;backup.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,backup)
            if sha(backup)!=current[rel]:raise ValueError('Ma dang thay doi. Chua cai.')
        changes.append({'path':rel,'existed':existed,'before_sha256':current[rel],'after_sha256':entry['after_sha256']})
    doc={'format':'warehouse-frontend-design-receipt-v1','status':'prepared','changes':changes,'backup':str(folder),'backend_or_inventory_modified':False}
    record(receipt,doc)
    try:
        # Dependencies first, main entry last: no missing imports during installation.
        ordered=sorted(files,key=lambda rel:(rel in manifest['guards'],rel.endswith('/main.jsx'),rel))
        for rel in ordered:
            p=safe(root,rel)
            if (sha(p) if p.is_file() else None)!=current[rel]:raise ValueError('Ma dang thay doi. Dung cai.')
            atomic(p,contents[rel])
        for rel,row in files.items():
            if sha(safe(root,rel))!=row['after_sha256']:raise ValueError('Ghi file khong khop.')
        for rel,expected in manifest['guards'].items():
            if rel not in files and sha(safe(root,rel))!=expected:raise ValueError('File ngoai pham vi da doi.')
        if do_build:
            command=['cmd','/d','/c','npm.cmd','run','build'] if os.name=='nt' else ['npm','run','build']
            print('2/2 · Dang build giao dien tren may cua ban...',flush=True)
            result=subprocess.run(command,cwd=root/'frontend',capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
            (folder/'build.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
            if result.returncode:raise ValueError('Build chua dat; xem build.log trong thu muc bien ban.')
    except BaseException:
        try:restore(root,receipt)
        except Exception as error:print('Can kiem tra hoan tac: '+str(error),file=sys.stderr)
        print('BIEN BAN: '+str(receipt),flush=True)
        raise
    doc.update(status='completed',build_passed=do_build);record(receipt,doc)
    return {**doc,'receipt':str(receipt)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=ROOT)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--rollback',type=Path)
    args=parser.parse_args();root=args.project.resolve();base=Path(__file__).resolve().parent
    if args.apply and args.rollback:parser.error('Chi chon mot thao tac.')
    try:
        if args.rollback:
            restore(root,args.rollback);print('DA KHOI PHUC MA GIAO DIEN CU. Chay lai frontend local.');return
        manifest_path=base/'manifest.json'
        if sha(manifest_path)!=EXPECTED_MANIFEST_SHA:raise ValueError('Manifest khong dung ban da kiem thu.')
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        payload=base/'payload.zip'
        if sha(payload)!=manifest['payload_sha256']:raise ValueError('Payload khong dung ban da kiem thu.')
        if not args.apply:
            check_guards(root,manifest,True);print('DA DOI CHIEU. Them --apply de cai giao dien.');return
        print('1/2 · Doi chieu ma, sao luu va ap dung giao dien...',flush=True)
        result=apply(root,manifest,payload)
        if result['status']=='already_installed':print('DA CO GIAO DIEN MOI. Khong ghi de them.');return
        print('DA AP DUNG GIAO DIEN · Build thanh cong.')
        print('Backend, camera, AI va du lieu kiem ke khong bi sua.')
        print('BIEN BAN: '+result['receipt'])
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:
        print('CHUA HOAN TAT: '+str(error),file=sys.stderr);raise SystemExit(1)


if __name__=='__main__':main()
