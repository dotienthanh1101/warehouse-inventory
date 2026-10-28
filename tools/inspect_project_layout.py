"""Read project metadata/imports and write one audit ZIP. Never move/delete code."""
import argparse
import ast
import hashlib
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SKIP_DIRS={'.venv','venv','node_modules','.git','__pycache__','.pytest_cache',
           '.mypy_cache','.ruff_cache','dist','build','.next'}
CODE_EXT={'.py','.js','.jsx','.ts','.tsx','.bat','.cmd','.ps1'}
CORE={'backend','frontend','data','captures','.venv','docs','tools'}
EXPERIMENTS={'adaptive_threshold_trial','benchmark_capture','benchmark_validation',
             'benchmark_holdout','de_validation','fg_validation','foreground_trial',
             'holdout_validation','loftr_trial','test_markers'}
DATASETS={'benchmark_dataset','benchmark_holdout_dataset','comparison_images'}
UPDATES={'dino_integration','surface_quality_fix','adaptive_backend_update',
         'duplicate_button_fix','mask_numeric_fix'}
MAX_SOURCE_BYTES=2*1024*1024


def category(path):
    top=path.parts[0]
    if top in CORE:return 'keep_runtime_or_project_support'
    if top in EXPERIMENTS:return 'experiment_review_before_move'
    if top in DATASETS:return 'dataset_keep_and_review_paths'
    if top in UPDATES:return 'old_update_review_backups_before_archive'
    if top=='comparison_results':return 'evidence_keep_and_review_paths'
    if top=='archive':return 'existing_archive_inspect_contents'
    if len(path.parts)==1 and top.startswith('test_') and path.suffix=='.py':
        return 'manual_test_review_before_move'
    if len(path.parts)==1 and top in {'start_project.bat','README.md','.gitignore'}:
        return 'keep_project_entry_or_documentation'
    return 'unclassified_keep'


def scan(project):
    entries=[]; analyses=[]; pruned=[]; issues=[]; hashes=defaultdict(list)
    totals=defaultdict(lambda:{'file_count':0,'bytes_listed':0})
    top_names={p.name for p in project.iterdir()}
    def on_error(error):issues.append({'error':str(error)})
    for folder,dirs,files in os.walk(project,followlinks=False,onerror=on_error):
        folder=Path(folder)
        kept=[]
        for name in sorted(dirs):
            child=folder/name; relative=child.relative_to(project)
            if (name.casefold() in SKIP_DIRS or child.is_symlink()
                    or getattr(child,'is_junction',lambda:False)()):
                pruned.append(relative.as_posix())
            else:kept.append(name)
        dirs[:]=kept
        for name in sorted(files):
            path=folder/name; relative=path.relative_to(project)
            if name.startswith('project_layout_audit_') and path.suffix=='.zip':continue
            if path.is_symlink():
                pruned.append(relative.as_posix());continue
            try:
                stat=path.stat()
                entry={'path':relative.as_posix(),'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,
                       'category':category(relative)}
                entries.append(entry)
                total=totals[relative.parts[0]];total['file_count']+=1;total['bytes_listed']+=stat.st_size
                if path.suffix.casefold() not in CODE_EXT or stat.st_size>MAX_SOURCE_BYTES:continue
                raw=path.read_bytes()
                digest=hashlib.sha256(raw).hexdigest();entry['sha256']=digest;hashes[digest].append(entry['path'])
                try:text=raw.decode('utf-8-sig')
                except UnicodeDecodeError:
                    issues.append({'path':entry['path'],'error':'Non-UTF8 source; analysis skipped'});continue
                imports=[];path_risks=[]
                if path.suffix.casefold()=='.py':
                    try:
                        tree=ast.parse(text,filename=entry['path'])
                        for node in ast.walk(tree):
                            if isinstance(node,ast.Import):
                                imports.extend({'module':a.name,'level':0,'line':node.lineno,'names':[]}
                                               for a in node.names)
                            elif isinstance(node,ast.ImportFrom):
                                imports.append({'module':node.module or '', 'level':node.level,
                                                'names':[a.name for a in node.names],'line':node.lineno})
                            elif isinstance(node,ast.Call):
                                fn=ast.unparse(node.func)
                                if fn in {'__import__','importlib.import_module','eval','exec'}:
                                    path_risks.append({'kind':'dynamic_execution_review','line':node.lineno})
                    except SyntaxError as error:
                        issues.append({'path':entry['path'],'error':'Python parse error','line':error.lineno})
                elif path.suffix.casefold() in {'.js','.jsx','.ts','.tsx'}:
                    for match in re.finditer(r'''(?:\bfrom\s*|\bimport\s*|\brequire\(\s*)['"]([^'"\n]+)['"]''',text):
                        imports.append({'module':match.group(1),'line':text.count('\n',0,match.start())+1})
                for number,line in enumerate(text.splitlines(),1):
                    if '__file__' in line or '.parents[' in line or 'sys.path' in line or 'os.chdir(' in line:
                        path_risks.append({'kind':'location_dependent_path_review','line':number})
                # Export module names and reference locations, never raw source/credentials.
                mentioned=sorted(n for n in top_names if n not in SKIP_DIRS and len(n)>2
                                 and re.search(r'(?<![\w])'+re.escape(n)+r'(?![\w])',text))
                analyses.append({'path':entry['path'],'imports':imports,'path_risks':path_risks,
                                 'mentions_top_level_names':mentioned,
                                 'uvicorn_targets':re.findall(r'\buvicorn\s+([A-Za-z_][\w.]*):\w+',text)})
            except OSError as error:
                issues.append({'path':relative.as_posix(),'error':str(error)})
    modules={}
    for entry in entries:
        p=Path(entry['path'])
        if p.suffix=='.py':
            parts=list(p.with_suffix('').parts)
            if parts[-1]=='__init__':parts.pop()
            modules['.'.join(parts)]=entry['path']
    graph={}
    for item in analyses:
        if not item['path'].endswith('.py'):continue
        parts=list(Path(item['path']).with_suffix('').parts)
        if parts[-1]=='__init__':parts.pop()
        else:parts=parts[:-1]
        targets=set()
        for imp in item['imports']:
            level=imp.get('level',0)
            base=parts[:len(parts)-level+1] if level else []
            module='.'.join(base+([imp['module']] if imp['module'] else []))
            for name in [module]+[module+'.'+n for n in imp.get('names',[])]:
                if name in modules:targets.add(modules[name])
        graph[item['path']]=sorted(targets)
    # Supplemental evidence only: runtime configuration/dynamic imports may add modules.
    reachable=set();queue=['backend/app/daily_api.py']
    while queue:
        p=queue.pop()
        if p in reachable or p not in graph:continue
        reachable.add(p);queue.extend(graph[p])
    return {'format':'project-layout-audit-v1','project_name':project.name,
            'project_files_modified':False,'source_contents_included':False,
            'database_or_image_contents_read':False,
            'scope_note':'Metadata only for data/images; source files parsed, not executed. Excluded dependencies/build/cache trees. Static reachability is NOT proof a file is unused.',
            'files':entries,'source_analysis':analyses,'local_python_import_graph':graph,
            'backend_static_reachable_from_daily_api':sorted(reachable),
            'same_bytes_source_groups':[{'sha256':h,'paths':paths} for h,paths in hashes.items() if len(paths)>1],
            'top_level_totals':dict(sorted(totals.items())),'excluded_trees':pruned,'issues':issues}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=Path.cwd())
    args=parser.parse_args();project=args.project.resolve()
    if not (project/'backend').is_dir() or not (project/'frontend').is_dir():
        raise ValueError('Run from warehouse_inventory containing backend and frontend.')
    print('Reading file inventory and source references. No project files will be moved or deleted.',flush=True)
    report=scan(project)
    lines=['PROJECT FILE INVENTORY (dependency/cache/build trees omitted)','']
    lines.extend(f"{item['path']}  ({item['bytes']} bytes)" for item in report['files'])
    note=('This report is a plan input, not permission to delete unreferenced files.\n'
          'Keep backend, frontend, data, captures and the launcher in place.\n'
          'Review experiments/old update bundles for archive placement only after checking path references.\n'
          'Preserve benchmark images, results and rollback backups until evidence retention is settled.\n'
          'No runnable source, credentials, database contents or image bytes are bundled.\n')
    docs=project/'docs';docs.mkdir(exist_ok=True)
    output=docs/('project_layout_audit_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.zip')
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('report.json',json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        archive.writestr('file_inventory.txt','\n'.join(lines)+'\n')
        archive.writestr('READ_ME.txt',note)
    print('Files listed:',len(report['files']),'| source files inspected:',len(report['source_analysis']),flush=True)
    print('Audit ZIP:',str(output),flush=True)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError) as error:
        print('Audit stopped:',error,file=sys.stderr);raise SystemExit(1)
