"""Export current warehouse source for GitHub review. No network, deletions or database reads."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

SOURCE_ROOTS = {'backend', 'frontend', 'tools', 'tests', 'camera_bridge', 'internet_access',
                'load_test_100', 'gpu_queue_test', 'duplicate_gpu_test', 'docs', 'config', '.github'}
SKIP_DIRS = {'.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__', '.pytest_cache',
             '.mypy_cache', '.ruff_cache', '.cache', '.next', '.vite', 'dist', 'build',
             'runtime', 'logs', 'results', 'captures', 'reviews', 'duplicate_checks',
             'data', 'warehouses', 'archive', 'backups', 'models', 'weights', 'checkpoints',
             'payload', 'originals', '.idea', '.vscode', 'coverage', 'htmlcov'}
SOURCE_SUFFIXES = {'.py', '.jsx', '.tsx', '.js', '.ts', '.css', '.scss', '.html', '.svg',
                   '.toml', '.ini', '.cfg', '.yml', '.yaml', '.md', '.mmd', '.bat', '.cmd', '.ps1'}
ROOT_FILES = {'README.md', 'README_VI.txt', 'LICENSE', 'LICENSE.txt', 'LICENSE.md',
              'NOTICE', 'NOTICE.txt', '.gitignore', '.gitattributes', '.editorconfig',
              '.python-version', 'pyproject.toml', 'requirements.txt', 'requirements-dev.txt',
              'start_project.bat', 'check_camera_1080.py', 'find_missing_evidence.py',
              'prepare_github.py'}
SPECIAL_NAMES = {'package.json', 'package-lock.json', 'tsconfig.json', 'jsconfig.json',
                 'components.json', 'Dockerfile', '.dockerignore', '.npmrc.example',
                 '.env.example', '.env.sample', '.env.template'}
SECRET_NAME = re.compile(r'(?:^|[._-])(?:local|credentials?|secrets?|tokens?|passwords?)(?:[._-]|$)', re.I)
PRIVATE_KEY = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')
TOKEN = re.compile(r'(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|hf_[A-Za-z0-9]{25,}|AKIA[0-9A-Z]{16})(?![A-Za-z0-9])')
DSN = re.compile(r'(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/\'"{}]+:([^\s@\'"{}]+)@', re.I)
ASSIGN = re.compile(r'''(?im)(?<![\w-])(?:["']?)(password|passwd|api_key|access_token|client_secret|secret_key)(?:["']?)\s*[:=]\s*(["'])([^\r\n"']{8,})\2''')
PLACEHOLDER = re.compile(r'(?:changeme|change_me|replace|placeholder|example|your_|<|\$\{|test[_ -]|dummy|fake|\*{3}|\.\.\.)', re.I)
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 96 * 1024 * 1024
GITIGNORE = '''
# Warehouse runtime/private files: source control is not a data backup.
.env
.env.*
!.env.example
!.env.sample
!.env.template
*.local.*
config/*
!config/*.example.json
!config/*.sample.json
!config/README.md
**/credentials*
**/secrets*
*.pem
*.key
*.p12
*.pfx
*.db
*.db-*
*.sqlite
*.sqlite3
*.sqlite-*
*.sql
*.dump
*.backup
*.wbackup
*.zip
*.7z
*.tar
*.gz
data/
captures/
archive/
backups/
warehouse_inventory_backups/
comparison_images/
comparison_results/
**/runtime/
**/results/
**/logs/
*.log
.venv/
venv/
**/__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
node_modules/
frontend/dist/
**/.vite/
models/
weights/
checkpoints/
.cache/
.idea/
.vscode/
.DS_Store
Thumbs.db
'''
ENV_EXAMPLE = '''# Prefer tools/setup_accounts.py for first local setup (prompts interactively).
# Alternative environment configuration: fill locally; never commit real secrets.
WAREHOUSE_AUTH_DATABASE_URL=postgresql://APP_USER:CHANGE_ME@127.0.0.1:5432/warehouse_accounts
WAREHOUSE_AUTH_SECRET=REPLACE_WITH_AT_LEAST_48_RANDOM_CHARACTERS
WAREHOUSE_AUTH_ORIGINS=http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000
WAREHOUSE_AUTH_SECURE_COOKIE=false
'''
README = '''# Chuẩn bị mã nguồn kiểm kê kho cho GitHub

Bản xuất từ thư mục dự án hiện tại, không thay mã nguồn đang chạy. Đây là mã nguồn,
không phải bản sao lưu dữ liệu vận hành. File README gốc (nếu có) được giữ nguyên.

## Thành phần

- React/Vite: giao diện user và admin, camera, hiệu chỉnh ảnh, phiên, danh mục, báo cáo.
- FastAPI/Python: API và điều phối xử lý ảnh; PostgreSQL cho tài khoản; SQLite theo kho.
- OpenCV/ArUco, Grounding DINO và DINOv2; các thử nghiệm LoFTR theo mã thực tế.
- Công cụ và kiểm thử nguồn hiện có. HTTPS/cầu nối được giữ nếu có nguồn, chưa coi là đã nghiệm thu.

## Máy mới

Cài Python/Node.js/PostgreSQL và môi trường CUDA/PyTorch tương thích. Xem requirements
thực tế trong backend; tránh dùng một lệnh cài PyTorch CPU thay cho môi trường GPU.
Tạo venv, cài các requirements cần thiết, chạy npm.cmd ci trong frontend.
Thiết lập tài khoản mới bằng tools/setup_accounts.py. Không dùng --import-legacy trên
repo sạch vì dữ liệu kho cũ không nằm trong GitHub. Mật khẩu nhập tương tác tại máy.

Chạy bằng start_project.bat hiện tại của dự án sau khi thiết lập môi trường.
Script xuất không thay entrypoint backend đã được kiểm tra trên máy đang dùng.

Frontend (PowerShell):

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

## Không nằm trong repo

Mật khẩu/token, config/auth.local.json, tài khoản và dữ liệu người dùng, database,
ảnh chụp/bản nháp/kết quả vận hành, môi trường .venv/node_modules, trọng số mô hình,
ZIP bản vá/sao lưu cũ. Mô hình được tải theo mã nguồn, không nhét trọng số vào Git.
Ảnh kiểm thử/tài liệu nhị phân không được xuất tự động; cần bộ dữ liệu riêng để chạy
những bài kiểm thử phụ thuộc ảnh. Danh sách file đã chọn có trong GITHUB_EXPORT.json.

Quét bí mật chỉ là bước hỗ trợ, không thay thế kiểm tra nội dung trước commit.
Không tự đổi license hoặc tuyên bố độ chính xác AI/100 camera đồng thời.
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def is_link(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def allowed(rel):
    parts = rel.parts
    name = rel.name
    if any(p.lower() in SKIP_DIRS for p in parts[:-1]):
        return False
    example = name.endswith(('.example.json', '.sample.json')) or name in {'.env.example', '.env.sample', '.env.template'}
    if SECRET_NAME.search(name) and not example:
        return False
    if name.startswith('.env') and not example:
        return False
    if len(parts) == 1:
        return name in ROOT_FILES or name in SPECIAL_NAMES
    if parts[0] not in SOURCE_ROOTS:
        return False
    if parts[0] == 'config':
        return example or name == 'README.md'
    if parts[0] == 'docs':
        return rel.suffix.lower() in {'.md', '.mmd'}
    if name in SPECIAL_NAMES or name.startswith('requirements') and rel.suffix == '.txt':
        return True
    return rel.suffix.lower() in SOURCE_SUFFIXES


def scan(data, relative):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return [{'path': relative, 'line': 0, 'rule': 'non_utf8_source_needs_review'}]
    issues = []
    for line_no, line in enumerate(text.splitlines(), 1):
        rules = []
        if PRIVATE_KEY.search(line): rules.append('private_key')
        if TOKEN.search(line): rules.append('platform_token')
        if any(not PLACEHOLDER.search(x.group(1)) for x in DSN.finditer(line)): rules.append('credential_in_database_url')
        if any(not PLACEHOLDER.search(x.group(3)) for x in ASSIGN.finditer(line)): rules.append('literal_secret_needs_review')
        issues.extend({'path': relative, 'line': line_no, 'rule': rule} for rule in rules)
    return issues


def export(project):
    project = Path(project).resolve()
    for required in ['backend/app', 'frontend/src', 'frontend/package.json']:
        path = project / required
        if not path.exists(): raise ValueError('Khong thay thanh phan du an: ' + required)
    output = project.parent / (project.name + '_github')
    if output.exists() and is_link(output): raise ValueError('Thu muc xuat la link/junction.')
    output.mkdir(exist_ok=True)
    tag = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    report_file = output / ('github_export_' + tag + '.json')
    chosen, excluded, issues, unknown_roots = [], Counter(), [], []
    total = 0
    # Enumerate only source areas, never enter database/image/private/runtime trees.
    def walk(folder):
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if is_link(entry):
                excluded['link_or_junction'] += 1; continue
            rel = entry.relative_to(project)
            if entry.is_dir():
                if len(rel.parts) == 1 and entry.name not in SOURCE_ROOTS:
                    excluded['non_source_root'] += 1
                    unknown_roots.append(entry.name)
                elif entry.name.lower() in SKIP_DIRS:
                    excluded['runtime_archive_dependency_directory'] += 1
                elif rel.parts[0] == 'config':
                    # Only inspect example/config readme filenames; never read local config.
                    yield from walk(entry)
                else:
                    yield from walk(entry)
            elif entry.is_file():
                if allowed(rel): yield entry, rel.as_posix()
                else: excluded['non_source_or_private_file'] += 1
    for path, rel in walk(project):
        before = path.stat()
        if before.st_size > MAX_FILE: issues.append({'path': rel, 'line': 0, 'rule': 'source_file_too_large'}); continue
        data = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('File dang thay doi; hay luu file va chay lai: ' + rel)
        total += len(data)
        if total > MAX_TOTAL: raise ValueError('Ma nguon vuot 96 MiB; can kiem tra pham vi truoc khi xuat.')
        if rel != 'prepare_github.py': issues.extend(scan(data, rel))
        chosen.append((rel, data, digest(data), before.st_mtime_ns))
    report = {'format': 'warehouse-github-source-export-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
              'source_modified': False, 'network_used': False, 'database_or_capture_contents_read': False,
              'secret_scan': 'common_patterns_only_not_a_guarantee', 'issues': issues,
              'excluded_counts': dict(excluded), 'excluded_top_level_directories': unknown_roots,
              'files': [{'path': rel, 'bytes': len(data), 'sha256': sha} for rel, data, sha, _ in chosen]}
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if issues:
        print('CHUA TAO ZIP · Co file can kiem tra. Khong in noi dung bi mat.')
        print('BAO CAO: ' + str(report_file))
        for row in issues[:15]: print(f"  {row['path']}:{row['line']} · {row['rule']}")
        return None
    for rel, data, sha, stamp in chosen:
        path = project / rel
        if path.stat().st_mtime_ns != stamp or digest(path.read_bytes()) != sha:
            raise ValueError('Ma nguon da thay doi trong luc xuat; chay lai: ' + rel)
    source = {rel: data for rel, data, _, _ in chosen}
    old_ignore = source.get('.gitignore', b'').decode('utf-8-sig')
    source['.gitignore'] = (old_ignore + '\n' + GITIGNORE).encode('utf-8')
    source['.env.example'] = ENV_EXAMPLE.encode('utf-8')
    source['README_GITHUB.md'] = README.encode('utf-8')
    source['GITHUB_EXPORT.json'] = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
    target = output / ('warehouse_inventory_source_' + tag + '.zip')
    partial = target.with_suffix('.partial')
    try:
        with ZipFile(partial, 'x', ZIP_DEFLATED) as z:
            for rel, data in sorted(source.items()): z.writestr(rel, data)
        with ZipFile(partial) as z:
            if z.testzip() is not None: raise ValueError('ZIP khong hop le.')
            for rel, data in source.items():
                if digest(z.read(rel)) != digest(data): raise ValueError('ZIP sai hash: ' + rel)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    print('DA DONG GOI MA NGUON HIEN TAI · ' + str(len(source)) + ' file')
    print('Khong sua du an, khong doc database/anh, khong gui len mang.')
    print('ZIP: ' + str(target))
    print('BAO CAO: ' + str(report_file))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        result = export(args.project)
        return 0 if result else 2
    except (ValueError, OSError, UnicodeError) as error:
        print('CHUA HOAN TAT: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
