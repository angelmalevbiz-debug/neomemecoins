#!/usr/bin/env python3
"""Freeze HEAD decision code and compare repaired fixed/adaptive on identical evidence.

Never reconstructs quotes. Historical dependencies are isolated in a temporary
directory; Windows-only lock import compatibility does not change trading logic.
Each arm invokes the actual primary engine in a separate Python process.
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--baseline-ref',default='bccb0ed30bac1efbf2e16cfe89e0ee1af049f050')
    args=parser.parse_args()
    output=args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit('Fresh isolated comparison output directory required')
    output.mkdir(parents=True,exist_ok=True)
    sha=subprocess.check_output(['git','rev-parse',args.baseline_ref+'^{commit}'],cwd=ROOT,text=True).strip()
    archive=subprocess.check_output(['git','archive','--format=zip',sha,'backend'],cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='neo-frozen-replay-') as tmp:
        archived=Path(tmp)
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            for name in package.namelist():
                if not (archived/name).resolve().is_relative_to(archived):
                    raise ValueError('unsafe source archive')
            package.extractall(archived)
        backend=archived/'backend'
        # Equivalent advisory-lock behavior on Windows, preserving archived math.
        (backend/'compat_file_lock.py').write_bytes((ROOT/'backend'/'compat_file_lock.py').read_bytes())
        for path in backend.glob('*.py'):
            source=path.read_text(encoding='utf-8')
            if 'import fcntl,' in source:
                source=source.replace('import fcntl,','import compat_file_lock as fcntl\nimport ')
            source=source.replace('import fcntl\n','import compat_file_lock as fcntl\n')
            path.write_text(source,encoding='utf-8')
        results={}
        for arm in ['frozen','repaired_fixed','repaired_adaptive']:
            command=[sys.executable,str(ROOT/'scripts'/'replay_main.py'),'--input',str(args.input.resolve()),
                     '--output',str(output/(arm+'.json')),'--state-dir',str(output/(arm+'-state'))]
            if arm=='frozen':command.extend(['--engine-backend',str(backend)])
            if arm=='repaired_adaptive':command.append('--adaptive')
            subprocess.run(command,cwd=ROOT,check=True,env={**os.environ,'PYTHONUTF8':'1'})
            report=json.loads((output/(arm+'.json')).read_text(encoding='utf-8'))
            results[arm]={'stats':report['stats'],'records':report['records'],
                          'invalid_records':report['invalid_records']}
        manifest={'baseline_ref':sha,'dataset_sha256':hashlib.sha256(args.input.read_bytes()).hexdigest(),
                  'same_recorded_stream':True,'execution':'identical recorded exact-quantity evidence; no API fallback',
                  'limitations':'Sparse/unequal quote coverage can prevent trades; synthetic data proves correctness only. Frozen code preserves known defects.',
                  'arms':results}
        (output/'comparison.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
