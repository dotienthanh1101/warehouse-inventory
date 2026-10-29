"""Apply the checked local completion from an extracted source tree. No DB/config edits."""
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

MANIFEST = {'files': {'tools/check_local_integrity.py': {'before_sha256': None, 'after_sha256': '39dfa54a46001d2f68c598f231bf9dc2ac71aec0ee963649c064f6193464b376'}, 'tools/check_local_readiness.py': {'before_sha256': None, 'after_sha256': 'cb7ef827f2f83b05525cb9948ca222587a4707e331e0814c3bb9bb7e6fd2a3c8'}, 'backend/requirements-test.txt': {'before_sha256': None, 'after_sha256': '7a9cea7af97ef1163f6c2bf8a130e0c7ed85e0fec8462175a15643a5228cea57'}, 'tests/test_readiness.py': {'before_sha256': None, 'after_sha256': '8631c3d2d4634d946436d62f4a0c9b69c5c0158091a7664d5739af7c34050af6'}, 'tests/test_inventory_trace.py': {'before_sha256': None, 'after_sha256': 'b8f6a81534601e154d732d83f950aafbf0fdcc98350868d08338563466688907'}, 'tests/test_trace_http.py': {'before_sha256': None, 'after_sha256': 'ea34eb4544b47b871df2640b072953f7f0eeba46ba223b62735f536f83647fff'}, 'tests/test_camera_network.py': {'before_sha256': None, 'after_sha256': '4452f37dcbaf00e5c4d256b9ac8883b94dc38d6fe875257564f063ca6027c404'}, 'docs/UPDATE_LOCAL.md': {'before_sha256': None, 'after_sha256': '5e5b004b6e1d0cd938ce507e15c5b5ac6912215176606b6e5e79096749b4e9c2'}, 'docs/DEMO_LOCAL.md': {'before_sha256': None, 'after_sha256': 'f24dc0f320b785e2867ed01329e350d50f40a373ccfed5f6d985194dab761be2'}, 'docs/LOCAL_STATUS_20261029.md': {'before_sha256': None, 'after_sha256': '0be8ff718ad70f4eaf971e1e20b543047f3ec212034f76f5aa5370dc77c23ab2'}, 'frontend/src/reportTrace.css': {'before_sha256': None, 'after_sha256': 'c6c6336a3a46d864620b01977ecf85b2d0e4f38d269af866f3730e6848de05ee'}, 'frontend/src/ReportTrace.jsx': {'before_sha256': None, 'after_sha256': '03ef9b1e07db0f1b7b7ce1cad25182925bef27ff4ab57fa30439d04db40fa1ed'}, 'frontend/src/App.jsx': {'before_sha256': '3909504f93ee41dfd96377dd717caa847b623b66202b10fbc7e4e5855c7c30da', 'after_sha256': '2872b63e885b95f111285aff359dac645cc300467ad6688feeb6e73112e14067'}, 'backend/app/box_inventory_service.py': {'before_sha256': '3375ee9b15f43bf0bc97486eb79b38bfcb79a53840ffffc65b05ad32f3c95b61', 'after_sha256': '72934dc0656ba8bc13695e1aa3b33f0bd0dd14e651da5fa0f764dc8f864ff0ee'}, 'backend/app/daily_api.py': {'before_sha256': '581c7be4de421492289032a6a6e46e1556b02d400d5d776ada0304245cebff5b', 'after_sha256': '090e2afad02a75e719adc7a9f459d9268ba96969043265c6458ba02341665622'}, 'backend/app/inventory_trace_service.py': {'before_sha256': None, 'after_sha256': 'db74b02844611fa73ff0c9f39fd4e52341a6dee3ed79eceed8491f92d30333de'}, 'backend/app/warehouse_camera.py': {'before_sha256': '666c4d621ad5fd21ab94a7e91ddf9a384708143540e4c0bf8dad95c3077885dc', 'after_sha256': '7c8bf26cecb61a12ad3bb338ceb01f353e1ab042649cee0af79ca6ceee248846'}, 'backend/app/inventory_provenance.py': {'before_sha256': None, 'after_sha256': '191893f890c44f9f61c9a52dffef7198853a0dab5cfe991d524a529c9f864526'}, 'backend/app/inventory_trace_api.py': {'before_sha256': None, 'after_sha256': '3357ff711eeaf87e293767c348692ff488d3fa97551ee1371ec747fb6688ee44'}, 'backend/app/review_api.py': {'before_sha256': 'd0560e3d3df1c502303437119d070b50633b204f8328aa4b5c97a6e40ddcba39', 'after_sha256': '0195661c1e092b199feb80b89f615bf2be5492b7302b451ce5ad01f7798b2370'}, 'docs/design/LOCAL_DESIGN.md': {'before_sha256': None, 'after_sha256': '22d47d30f2d9bf9dcc36c90d822d86f2d384a440241d29ba9c81caf09e64deee'}}, 'guards': {'backend/app/__init__.py': '01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b', 'backend/app/account_gate.py': '19f6098794171b2ed864ee77279c1f3c9716a1cd3bf6f2d0e11f98aaa2c908f9', 'backend/app/admin_warehouse_api.py': '2919d3364ba4ecb554baaae9bcca4156a30cebf5696cfeecce089ff12f1234d4', 'backend/app/aruco_reader.py': '2506b73ba9761cfa72deb32757a3bde27870b33eda66d059ed30265b3cc441ef', 'backend/app/auth_api.py': 'a049918fd5da98e9f6571d728eb1db52bce94126ae5c619b65b1a14b0f2f663a', 'backend/app/auth_middleware.py': '5b60ae74458c95cec842b5803f8e011554d5f6c91a2db09453d4ffa61036508f', 'backend/app/auth_settings.py': '3e5088db309e60538bdda931c632723e77dd8adbcbc8768c2645cc32b0e4f306', 'backend/app/auth_store.py': '1028245a7cb6bd1ab088a8b959fafc4d4f5c874601c105a14f8384985b07ef38', 'backend/app/box_analysis.py': '5d51f8fcf849950f2ab0b31183a72eb74e7a0934351ae9fa461bf45810e94ae5', 'backend/app/box_inventory_service.py': '3375ee9b15f43bf0bc97486eb79b38bfcb79a53840ffffc65b05ad32f3c95b61', 'backend/app/camera_gateway.py': 'cde35e054bffbc3163588e7e12aba36bf4e7271949e2719344955c35b90073b1', 'backend/app/camera_gateway_api.py': 'dc2b0a71921d94e1548f16c998f725f34b303f8c66509d3a552d42c4a43c9629', 'backend/app/camera_video.py': '8e79578150d8d80cbb657d0a86f63591530e3f9db126b44f4c4f2eaac33acc31', 'backend/app/catalog_api.py': '82757c5b7955ee13e4709dc0d1b08f2cfbc1ba0d1dde1ee3ac439e838c31076d', 'backend/app/catalog_service.py': '43eede7745635d943dc7e9be544df4c3af4e526c89057760b12f3d03a35400e5', 'backend/app/daily_api.py': '581c7be4de421492289032a6a6e46e1556b02d400d5d776ada0304245cebff5b', 'backend/app/daily_inventory_service.py': 'bb6a00f33a82632ab33b83e3c1dab74887523d293efdfa1123b9ea45c5e847d4', 'backend/app/daily_reconciliation_service.py': '7c9158ba40729a18789f9db1ca73d0602cbcb545ff11e8b1b5754f7d8af3438a', 'backend/app/daily_session_service.py': 'f98faf05916038220a048b4c01b13c56e1b6d1e0bd841e11bcf316c527083200', 'backend/app/database.py': '35b09e624eddad3306277bf6accdef58c6735af4e6ec7095f6fc5eff73f2e7f5', 'backend/app/day_closing_service.py': 'fdd0d8c2555d67d6775ffaf77bbc42660ad75237e435327e7ea24d33c6632b4e', 'backend/app/duplicate_check.py': '5eb921d4ea4e22f4a7ef1782434c92c35d7f17adbcc12d22f46ff2f90f4353f2', 'backend/app/duplicate_repository.py': 'b094085f2ac05ad07181cee8229a56fba3a778dc8b13b8d35632ae2570cab327', 'backend/app/image_task_api.py': '6d8eff5dc84c5304d8e43b90e67a7fb624745ab8abba7b1433ec669ab6f3735d', 'backend/app/image_tasks.py': 'c24011bc74388f2a8c6a1a9c669c1a3e0a608c8b1e8d048cf1fb035e1f111725', 'backend/app/inventory_service.py': '64a5f314c32ee076947d5e73a51332811c444079151fa7c8741ced85fc46ab96', 'backend/app/main.py': 'b3ecc035fe21509057b10d48637347b66feabe386bfdeaa2d2d6d0c82bb0ace9', 'backend/app/recovery_email.py': 'b62e7e480aaf146d29de1edfe2023ffdc364b87344326a76b4227d8afc2ddb59', 'backend/app/report_export_api.py': 'f297f51042f7e7ce82b701fbdfcc84096b68bbf7ef84647471f371bf72970f39', 'backend/app/report_export_service.py': 'ae567bcd3cce5d87b8eb0519b079cdcbcf70bbbea10649ec7c07c0b2adb102b2', 'backend/app/review_api.py': 'd0560e3d3df1c502303437119d070b50633b204f8328aa4b5c97a6e40ddcba39', 'backend/app/server_capacity.py': '3ef462b0263bf9472fc25a899251e97fa5eed118b13ba4fa121bf792fc155b65', 'backend/app/session_finalize_service.py': 'b64982f0983995f8837df24d0298087e2f2c69a85ce9acd050c28e1ed2402474', 'backend/app/storage_api.py': 'e1fa0ef327c80f9aa525d6ab5c683f15eb857282ce7f0ac62646786c432a8c69', 'backend/app/storage_quota.py': 'a1ae5de3cbeb5e21d1909c9852c7f5aef4566075799fa5034ef133c9384e0969', 'backend/app/support_api.py': '8f0ce3b941eb7d17cca15c19e2db50a5eeb10a81af908bfc9b630e980262dc45', 'backend/app/support_service.py': '0fecccc8b3c83553cfd73572913065382dbe625e65edae3748fd35c9c0471d69', 'backend/app/visual_similarity.py': '817ea3b6702870e8db5c65be8c884dd9ba44fe57385f651adc65f6df514cd08c', 'backend/app/warehouse_camera.py': '666c4d621ad5fd21ab94a7e91ddf9a384708143540e4c0bf8dad95c3077885dc', 'backend/app/warehouse_scope.py': 'bcb2d9cbc56607f9d026d2fbfac2844f7bfa3827e1076b3b41603339d79d4bbd', 'frontend/src/AdminAccountsPanel.jsx': 'bf5cc07568b0906720e012109539a9d424d8acd745201a9dc8e17e60675eddc3', 'frontend/src/AdminDashboard.jsx': '2af22ec9895f1ec9cdb792d8ccc6a0fdd2e3b4d18dd9e47b71496e29ea97f1ba', 'frontend/src/AdminWarehousePanel.jsx': 'a67669f82df253da47ed3f6b55d330696926d8b7d3ae8e75acaac385ace4bb87', 'frontend/src/App.jsx': '3909504f93ee41dfd96377dd717caa847b623b66202b10fbc7e4e5855c7c30da', 'frontend/src/AuthShell.jsx': '820248e69ac6884370c5da47dd9f526713654b4f3c46c7d1697c62d41ae42092', 'frontend/src/CatalogPanel.jsx': '19237d586911b1170ec667431c47d2021a5e43cb4050829a4686d981ce1d8c92', 'frontend/src/GatewayCameraPanel.jsx': '69b90af15c01b83458dcd63c426df42fcb0a2ced3508fdfc2503aaff47ab1fb3', 'frontend/src/GatewayVideoPreview.jsx': 'b50c90af2482973a9d14c84ef1aca7f855c6fd00f179e0c5371be80a323c07ab', 'frontend/src/GuestSupportPanel.jsx': '90db4ca8decee314064e29a5b3b0afd495f9af74824e4ccd58df388c6130e844', 'frontend/src/Icon.jsx': '06179b5f034b6dba4c25a4092d267112cf9da87b7a3b4e6d5555067e31e96255', 'frontend/src/ImageTaskPanel.jsx': '722d666d3f2c9c3d7bb5e9a6e9090f6b6c1f042dfb9a2b2713313b02c4062b55', 'frontend/src/PasswordPanel.jsx': '6349ac23f9600bbc2cc0b0cfb2ea180024d7a0a7b816f6b75255daf6c325d0be', 'frontend/src/RecoveryEmailPanel.jsx': 'e2f5672709f4be82efbe8761e637e612a65f88b8d52b516311176b3619b0fd15', 'frontend/src/ReportExport.jsx': 'be35bbf7e6d487abc5d49bba6a6db57271a162441971176a0ac1b6825ea519ab', 'frontend/src/ServerStorage.jsx': 'e0bfb0246b2780c46790c2439b3b666ffb46fabfc9f5034744ee66eeaf903c14', 'frontend/src/StoragePanel.jsx': '36ecaa5f05bbc90f99fa566d5523a010d5109a0a78158b2e6ddedc40688c3940', 'frontend/src/SupportMessages.jsx': '295a6e092e9e3640bb5ee780b433cd9ee2933704f27651c42cf21629f88a1107', 'frontend/src/SupportPanel.jsx': 'a434340608feefc53b227225191822a6ec2112aaab8c596d41cd1f26300d5db1', 'frontend/src/TransientNotice.jsx': '8cd4e0ede8abe8685fd7f460ce5c151e2bc02f3b922e986a5dbbbf1ff10d279d', 'frontend/src/WarehouseDashboard.jsx': 'e580b26140c24ea959948f8999cf1e8c123b39170b26647b90ba80f1ab31a5f0', 'frontend/src/authClient.js': '096e4b304ec6dd94426562f65a8dd2d98252c38d0eaf49cdea8438b16b50b24d', 'frontend/src/imageTaskClient.js': 'bf75be6b01fdc876408d55bc0377987f51f03e7ef68d90a2e2b857b50f83dc3d', 'frontend/src/main.jsx': '99121b374c3ddd1a0429f6292d050595a3e4e28649cce0e3bb9bffab1be71b31', 'frontend/src/supportClient.js': '826f22157ebecf809f8e77a58351ed9c147a0ae880717007d18d445454c59424'}}
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def allowed(rel):
    return rel in MANIFEST['files']


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
    if doc.get('format')!='warehouse-local-closeout-receipt-v1':raise ValueError('Sai bien ban.')
    changes=doc['changes']
    for row in changes:
        rel=row['path']
        if not allowed(rel):raise ValueError('Ngoai pham vi ban local.')
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
    contents={rel:safe(Path(payload).resolve(),rel).read_bytes() for rel in files}
    for rel,entry in files.items():
        if not allowed(rel):raise ValueError('Payload vuot pham vi frontend.')
        if hashlib.sha256(contents[rel]).hexdigest()!=entry['after_sha256']:raise ValueError('Payload da doi: '+rel)
        p=safe(root,rel);current[rel]=sha(p) if p.is_file() else None
        if current[rel] not in {entry.get('before_sha256'),entry['after_sha256']}:
            raise ValueError('File hien tai khong khop: '+rel+'. Chua sua file nao.')
    if all(current[rel]==entry['after_sha256'] for rel,entry in files.items()):return {'status':'already_installed'}
    if any(current[rel]==entry['after_sha256'] for rel,entry in files.items()):raise ValueError('Dang co ban cai do dang; can doi chieu truoc.')
    tag=datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    folder=safe(root,'archive/local_closeout/'+tag);folder.mkdir(parents=True,exist_ok=False)
    receipt=folder/'receipt.json'
    changes=[]
    for rel,entry in files.items():
        p=safe(root,rel);existed=p.is_file()
        if existed:
            backup=folder/'originals'/rel;backup.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,backup)
            if sha(backup)!=current[rel]:raise ValueError('Ma dang thay doi. Chua cai.')
        changes.append({'path':rel,'existed':existed,'before_sha256':current[rel],'after_sha256':entry['after_sha256']})
    shutil.copyfile(Path(__file__).resolve(),folder/'rollback.py')
    doc={'format':'warehouse-local-closeout-receipt-v1','status':'prepared','changes':changes,'backup':str(folder),'inventory_or_config_modified':False}
    record(receipt,doc)
    try:
        # Dependencies first, main entry last: no missing imports during installation.
        ordered=sorted(files,key=lambda rel:(rel in manifest['guards'],rel.endswith('/main.jsx'),rel))
        for rel in ordered:
            if rel.endswith('.py'):compile(contents[rel],rel,'exec')
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
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--rollback',type=Path)
    args=parser.parse_args();root=args.project.resolve();base=Path(__file__).resolve().parent
    if args.apply and args.rollback:parser.error('Chi chon mot thao tac.')
    try:
        if args.rollback:
            restore(root,args.rollback);print('DA KHOI PHUC MA NGUON CU. Khoi dong lai backend va frontend.');return
        manifest=MANIFEST
        payload=SOURCE_ROOT
        if not args.apply:
            check_guards(root,manifest,True);print('DA DOI CHIEU. Them --apply de ap dung ban local.');return
        import socket
        with socket.socket() as probe:
            probe.settimeout(.3)
            if probe.connect_ex(('127.0.0.1',8000))==0:
                raise ValueError('Hay dung backend local bang Ctrl+C truoc khi ap dung. Du lieu chua bi sua.')
        print('1/2 · Doi chieu ma, sao luu va ap dung ban hoan thien local...',flush=True)
        result=apply(root,manifest,payload)
        if result['status']=='already_installed':print('DA CO BAN LOCAL MOI. Khong ghi de them.');return
        print('DA AP DUNG BAN LOCAL · Build thanh cong.')
        print('Du lieu kho, config, mo hinh va nguong AI duoc giu nguyen.')
        print('BIEN BAN: '+result['receipt'])
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:
        print('CHUA HOAN TAT: '+str(error),file=sys.stderr);raise SystemExit(1)


if __name__=='__main__':main()
