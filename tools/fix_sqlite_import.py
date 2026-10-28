"""Close all SQLite import handles before replacing files on Windows.

Default: inspect only. --apply backs up and changes tools/setup_accounts.py.
--undo restores that source file; it never modifies inventory databases.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

BEFORE = 'a51f3d36ea7230e805041a7447d476b0918eba572d3cac4f764b95464504e8bc'
AFTER = 'f02e130ee8afb5f37eedc6315fd69fd61f6a44a0304c60cb3ff6bb91be88d4a8'
REPLACEMENTS = (
    ('import argparse\n', 'import argparse\nfrom contextlib import closing\n'),
    ('with sqlite3.connect(destination) as c:',
     'with closing(sqlite3.connect(destination)) as c:'),
    ("with sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True) as old, sqlite3.connect(target) as new:",
     "with closing(sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True)) as old, closing(sqlite3.connect(target)) as new:"),
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patched_bytes(original):
    if sha(original) != BEFORE:
        raise ValueError('setup_accounts.py khong khop ban da doi chieu. Chua sua file.')
    text = original.decode('utf-8')
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            raise ValueError('Khong tim thay dung doan can sua. Chua sua file.')
        text = text.replace(old, new, 1)
    replacement = text.encode('utf-8')
    compile(replacement, 'setup_accounts.py', 'exec')
    if sha(replacement) != AFTER:
        raise ValueError('Noi dung sau sua khong dung ban da kiem tra.')
    return replacement


def atomic_write(path, data):
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def repair(root, apply=False):
    target = root / 'tools' / 'setup_accounts.py'
    if target.is_symlink() or not target.is_file():
        raise ValueError('Khong tim thay file setup_accounts.py hop le.')
    if not (root / 'frontend' / 'package.json').is_file():
        raise ValueError('Can chon dung thu muc goc warehouse_inventory.')
    original = target.read_bytes()
    if sha(original) == AFTER:
        print('Ban sua da duoc cai. Co the chay lai setup_accounts.py --import-legacy.')
        return
    replacement = patched_bytes(original)
    if not apply:
        print('Da doi chieu: chi sua viec dong 3 ket noi SQLite trong setup_accounts.py.')
        print('Chua ghi file. Dung --apply de cai ban sua.')
        return
    receipt_path = root / 'docs' / 'sqlite_import_fix_receipt.json'
    if receipt_path.exists():
        raise ValueError('Da co bien nhan ban sua. Can kiem tra truoc khi cai tiep.')
    backup_dir = root.parent / (root.name + '_backups')
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ('setup_accounts_before_sqlite_fix_' + uuid4().hex + '.py')
    with backup.open('xb') as stream:
        stream.write(original)
    if sha(backup.read_bytes()) != BEFORE:
        raise ValueError('Khong xac minh duoc ban sao luu. Chua sua file.')
    receipt = {'backup': str(backup.resolve()), 'before_sha256': BEFORE, 'after_sha256': AFTER}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write(target, replacement)
        if sha(target.read_bytes()) != AFTER:
            raise ValueError('Khong xac minh duoc file sau sua.')
        atomic_write(receipt_path, json.dumps(receipt, indent=2).encode('utf-8'))
    except BaseException:
        atomic_write(target, original)
        raise
    print('Da sua loi dong ket noi SQLite.')
    print('Sao luu: ' + str(backup))
    print('Chay tiep: .\\.venv\\Scripts\\python.exe .\\tools\\setup_accounts.py --import-legacy')


def undo(root):
    target = root / 'tools' / 'setup_accounts.py'
    receipt_path = root / 'docs' / 'sqlite_import_fix_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if sha(target.read_bytes()) != AFTER:
        raise ValueError('File da thay doi sau ban sua. Khong tu ghi de.')
    backup = Path(receipt['backup'])
    original = backup.read_bytes()
    if receipt.get('before_sha256') != BEFORE or receipt.get('after_sha256') != AFTER or sha(original) != BEFORE:
        raise ValueError('Ban sao luu/bien nhan khong hop le.')
    atomic_write(target, original)
    receipt_path.rename(receipt_path.with_name('sqlite_import_fix_undone_' + uuid4().hex + '.json'))
    print('Da hoan tac ma nguon setup_accounts.py. Khong thay doi du lieu kho.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--undo', action='store_true')
    args = parser.parse_args()
    root = args.project.resolve()
    if args.undo:
        undo(root)
    else:
        repair(root, args.apply)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        raise SystemExit('Chua hoan tat: ' + str(error))
