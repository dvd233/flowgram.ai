#!/usr/bin/env python3
"""Package only bounded report files; never source, secrets, dependencies, or caches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

MAX_FILE = 100_000_000
MAX_EXPANDED = 150_000_000
MAX_ARCHIVE = 20_000_000
MAX_FILES = 150
HERE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def package(reports, output, variant, source, context, plan):
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ('native-' + variant + '.zip')
    manifest_path = output / ('manifest-' + variant + '.json')
    if archive.exists() or manifest_path.exists():
        raise ValueError('Evidence output already exists')
    rows = []
    total = 0
    paths = sorted(reports.iterdir()) if reports.is_dir() else []
    if len(paths) > MAX_FILES:
        raise ValueError('Too many report files')
    try:
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for path in paths:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or path.suffix not in ('.json', '.xml', '.log'):
                    raise ValueError('Unexpected evidence file type: ' + path.name)
                if info.st_size > MAX_FILE:
                    raise ValueError('Individual evidence file exceeds hard bound')
                total += info.st_size
                if total > MAX_EXPANDED:
                    raise ValueError('Expanded evidence exceeds hard bound')
                data = path.read_bytes()
                if len(data) != info.st_size:
                    raise ValueError('Report changed while packaging')
                z.writestr(path.name, data)
                rows.append({'path': path.name, 'bytes': len(data), 'sha256': digest(data)})
        if archive.stat().st_size > MAX_ARCHIVE:
            raise ValueError('Compressed evidence exceeds hard bound')
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None or z.namelist() != [x['path'] for x in rows]:
                raise ValueError('Evidence archive verification failed')
        observed = {}
        for key, ref in [('commit', 'HEAD'), ('tree', 'HEAD^{tree}')]:
            result = subprocess.run(['git', '-C', str(source), 'rev-parse', ref], capture_output=True, text=True)
            observed[key] = result.stdout.strip() if result.returncode == 0 else None
        manifest = {'schema': 1, 'purpose': 'native-execution-evidence; may contain failed or never-reached stages',
                    'variant': variant, 'expected_source': plan[variant], 'observed_source': observed,
                    'context': context, 'plan_sha256': digest((HERE / 'native-plan.json').read_bytes()),
                    'archive': archive.name, 'archive_bytes': archive.stat().st_size,
                    'archive_sha256': digest(archive.read_bytes()), 'expanded_bytes': total,
                    'limits': {'archive_bytes': MAX_ARCHIVE, 'expanded_bytes': MAX_EXPANDED, 'file_bytes': MAX_FILE, 'files': MAX_FILES},
                    'files': rows, 'stages_present': [x['path'] for x in rows if x['path'].endswith('.execution.json')],
                    'e2e_executed': False, 'live_api_cases_not_executed': 3,
                    'replays_are_not_new_test_cases': True, 'complete_repository_ci_claim': False}
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        return manifest
    except Exception:
        archive.unlink(missing_ok=True)
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--reports', type=Path, required=True)
    p.add_argument('--variant', choices=['baseline', 'candidate'], required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    plan = json.loads((HERE / 'native-plan.json').read_text())
    for key, wanted in plan['context'].items():
        if os.environ.get(key) != wanted:
            raise ValueError('Wrong hosted package context: ' + key)
    context = {key: os.environ.get(key) for key in [*plan['context'], 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT']}
    result = package(a.reports.resolve(), a.output.resolve(), a.variant, a.source.resolve(), context, plan)
    print(json.dumps({'archive': result['archive'], 'bytes': result['archive_bytes'], 'files': len(result['files'])}))


if __name__ == '__main__':
    main()
