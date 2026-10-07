#!/usr/bin/env python3
"""Read-only inventory of fixed, public Git trees. No project code is imported or run."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import zipfile
import zlib

TEXT_SUFFIXES = {'.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.py', '.sh',
                 '.mdx', '.html', '.json', '.yml', '.yaml', '.md', '.css', '.less',
                 '.scss', '.svg', '.snap'}
DENIED_NAMES = {'.npmrc', '.npmrc-publish', 'credentials.json', 'secrets.json', 'token.json'}
DENIED_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.jks', '.keystore'}
SENSITIVE_LITERAL = re.compile(rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}')
HARD_LIMITS = {'archive_bytes': 5500000, 'expanded_bytes': 8000000, 'file_bytes': 1000000}
LOCK_NAMES = {'pnpm-lock.yaml', 'package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock',
              'bun.lock', 'bun.lockb', 'Cargo.lock'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def blob_oid(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def safe_relative(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise ValueError('Invalid repository/archive path')
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or any(x in {'', '.', '..'} for x in p.parts):
        raise ValueError('Non-canonical or escaping path: ' + name)
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError('Control character in path')
    return name


def sensitive_name(name):
    p = PurePosixPath(name)
    return (any(part.lower().startswith('.env') for part in p.parts)
            or p.name.lower() in DENIED_NAMES or p.suffix.lower() in DENIED_SUFFIXES)


def selected(name):
    p = PurePosixPath(safe_relative(name))
    return (not sensitive_name(name) and p.suffix.lower() in TEXT_SUFFIXES
            and p.name not in LOCK_NAMES)


def check_limits(limits):
    if set(limits) != set(HARD_LIMITS):
        raise ValueError('Unexpected size-limit schema')
    for name, hard_maximum in HARD_LIMITS.items():
        value = limits[name]
        if type(value) is not int or value <= 0 or value > hard_maximum:
            raise ValueError('Invalid or weakened hard limit: ' + name)


def git(source, *args):
    result = subprocess.run(['git', '-C', str(source), *args], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout


def git_tree(source):
    result = {}
    for row in git(source, 'ls-tree', '-r', '-z', '--full-tree', 'HEAD').split(b'\0'):
        if not row:
            continue
        metadata, name = row.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split()
        name = safe_relative(name.decode('utf-8'))
        if name in result or kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('Duplicate, symlink, submodule, or unsupported tree object: ' + name)
        result[name] = {'mode': mode, 'oid': oid}
    return result


def expected_tree(plan, baseline, variant):
    entries = {row['path']: {'mode': row['mode'], 'oid': row['oid']} for row in baseline}
    if variant == 'candidate':
        for change in plan['candidate_files']:
            entries[change['path']] = {'mode': '100644', 'oid': change['oid']}
    return entries


def source_guard(source, plan, baseline, variant):
    target = plan[variant]
    head = git(source, 'rev-parse', 'HEAD').decode().strip()
    tree = git(source, 'rev-parse', 'HEAD^{tree}').decode().strip()
    if head != target['commit'] or tree != target['tree']:
        raise ValueError('Pinned source commit or tree mismatch')
    actual = git_tree(source)
    if actual != expected_tree(plan, baseline, variant):
        raise ValueError('Complete tracked tree differs from the reviewed baseline plus two-file candidate')
    if variant == 'candidate':
        parents = git(source, 'rev-list', '--parents', '-n', '1', 'HEAD').decode().split()[1:]
        if parents != [plan['baseline']['commit']]:
            raise ValueError('Candidate must have the exact single baseline parent')
    if git(source, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('Source checkout is not clean')
    headers = subprocess.run(['git', '-C', str(source), 'config', '--local', '--name-only',
                              '--get-regexp', r'^(http\..*\.extraheader|credential\..*)$'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if headers.returncode not in (0, 1) or headers.stdout:
        raise ValueError('Unexpected local Git credential configuration')
    return {'commit': head, 'tree': tree, 'tracked_files': len(actual)}


def read_source(source, name, max_size):
    target = source / safe_relative(name)
    if not target.is_file() or target.is_symlink() or not target.resolve().is_relative_to(source.resolve()):
        raise ValueError('Selected tracked file missing or unsafe: ' + name)
    for parent in target.parents:
        if parent == source:
            break
        if parent.is_symlink():
            raise ValueError('Symlink in selected source path')
    if target.stat().st_size > max_size:
        raise ValueError('Selected file exceeds limit: ' + name)
    data = target.read_bytes()
    if len(data) > max_size:
        raise ValueError('Selected file grew beyond limit: ' + name)
    data.decode('utf-8', errors='strict')
    if SENSITIVE_LITERAL.search(data):
        raise ValueError('Potential sensitive literal; source export stopped: ' + name)
    return data


def verify_archive(archive, manifest):
    limits = manifest['limits']
    check_limits(limits)
    if archive.stat().st_size > limits['archive_bytes']:
        raise ValueError('Archive exceeds compressed size limit')
    if digest(archive.read_bytes()) != manifest['archive_sha256']:
        raise ValueError('Archive SHA256 mismatch')
    expected = {row['archive_path']: row for row in manifest['files']}
    if len(expected) != len(manifest['files']):
        raise ValueError('Duplicate manifest path')
    total = 0
    package_scripts = []
    with zipfile.ZipFile(archive) as z:
        names = [i.filename for i in z.infolist()]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError('Duplicate, missing, or unexpected ZIP entries')
        for info in z.infolist():
            name = safe_relative(info.filename)
            row = expected[name]
            if not name.startswith('source/') or sensitive_name(name):
                raise ValueError('Disallowed archive member')
            mode = (info.external_attr >> 16) & 0xffff
            if stat.S_IFMT(mode) != stat.S_IFREG or info.is_dir() or info.flag_bits & 1:
                raise ValueError('Non-regular or encrypted archive member')
            if info.file_size != row['bytes'] or info.file_size > limits['file_bytes']:
                raise ValueError('Archive member size mismatch')
            total += info.file_size
            if total > limits['expanded_bytes']:
                raise ValueError('Expanded archive exceeds limit')
            with z.open(info) as stream:
                data = stream.read(limits['file_bytes'] + 1)
            if (len(data) != row['bytes'] or digest(data) != row['sha256']
                    or blob_oid(data) != row['git_blob']
                    or (zlib.crc32(data) & 0xffffffff) != row['crc32']
                    or info.CRC != row['crc32']):
                raise ValueError('Archive CRC/hash/size verification failed: ' + name)
            if PurePosixPath(name).name == 'package.json':
                package = json.loads(data)
                package_scripts.append({'path': name[len('source/'):], 'name': package.get('name'),
                                        'scripts': package.get('scripts', {})})
        if z.testzip() is not None:
            raise ValueError('ZIP CRC test failed')
    if total != manifest['expanded_bytes']:
        raise ValueError('Expanded total differs from manifest')
    if ('package_scripts' in manifest
            and sorted(package_scripts, key=lambda row: row['path']) != manifest['package_scripts']):
        raise ValueError('Package script summary is not derived from the verified source bytes')


def verify_provenance(manifest, plan, baseline, variant, harness_commit, run_id, run_attempt):
    check_limits(plan['limits'])
    if manifest['limits'] != plan['limits']:
        raise ValueError('Archive limits differ from the trusted plan')
    if not re.fullmatch('[0-9a-f]{40}', harness_commit):
        raise ValueError('Expected harness commit is not bound')
    if not re.fullmatch('[0-9]+', str(run_id)) or not re.fullmatch('[1-9][0-9]*', str(run_attempt)):
        raise ValueError('Expected run identity is not bound')
    if (manifest.get('schema') != 1 or manifest.get('purpose') != 'native-scope-inventory-only'
            or manifest.get('native_tests_executed') is not False or manifest.get('variant') != variant):
        raise ValueError('Unexpected artifact purpose or variant')
    expected_context = {'repository': plan['repository'], 'repository_id': plan['repository_id'],
                        'owner': plan['owner'], 'owner_id': plan['owner_id'],
                        'ref': 'refs/heads/' + plan['validation_branch'], 'event': 'push',
                        'harness_commit': harness_commit, 'run_id': str(run_id),
                        'run_attempt': str(run_attempt)}
    if manifest['context'] != expected_context:
        raise ValueError('Artifact context is not the verified repository/event/run')
    expected = {row['path']: row.copy() for row in baseline}
    if variant == 'candidate':
        for change in plan['candidate_files']:
            expected[change['path']] = dict(change, mode='100644')
    expected_identity = dict(plan[variant], tracked_files=len(expected))
    if manifest['source'] != expected_identity:
        raise ValueError('Artifact commit/tree identity differs from the trusted plan')
    files = manifest['files']
    paths = [row['path'] for row in files]
    selected_paths = sorted(name for name in expected if selected(name))
    if paths != selected_paths:
        raise ValueError('Artifact does not contain exactly the pinned selected file set')
    for row in files:
        name = safe_relative(row['path'])
        if (row['archive_path'] != 'source/' + name or row['git_blob'] != expected[name]['oid']
                or row['bytes'] != expected[name]['bytes']):
            raise ValueError('Artifact file is not the pinned Git blob and size: ' + name)
    if manifest['scope_files_sha256'] != digest(canonical(files)):
        raise ValueError('Scope manifest checksum mismatch')
    locks = [{'path': name, 'git_blob': expected[name]['oid']} for name in sorted(expected)
             if PurePosixPath(name).name in LOCK_NAMES]
    if manifest['lock_metadata'] != locks:
        raise ValueError('Frozen lock identity set differs from the pinned tree')
    if not isinstance(manifest.get('package_scripts'), list):
        raise ValueError('Missing package-script inventory')


def context_guard(harness, plan):
    allowed = {'GITHUB_REPOSITORY': plan['repository'], 'GITHUB_REPOSITORY_ID': plan['repository_id'],
               'GITHUB_REPOSITORY_OWNER': plan['owner'], 'GITHUB_REPOSITORY_OWNER_ID': plan['owner_id'],
               'GITHUB_REF': 'refs/heads/' + plan['validation_branch'], 'GITHUB_EVENT_NAME': 'push'}
    for key, expected in allowed.items():
        if os.environ.get(key) != expected:
            raise ValueError('Unexpected execution context: ' + key)
    event_sha = os.environ.get('GITHUB_SHA', '')
    if not re.fullmatch('[0-9a-f]{40}', event_sha):
        raise ValueError('Missing immutable workflow event SHA')
    if git(harness, 'rev-parse', 'HEAD').decode().strip() != event_sha:
        raise ValueError('Harness checkout is not the event commit')
    for name, expected in plan['harness_files'].items():
        if digest((harness / safe_relative(name)).read_bytes()) != expected:
            raise ValueError('Harness file identity mismatch: ' + name)
    for variant in ('baseline', 'candidate'):
        for key in ('commit', 'tree'):
            if not re.fullmatch('[0-9a-f]{40}', plan[variant][key]):
                raise ValueError('Unbound source identity')
    return {'repository': plan['repository'], 'repository_id': plan['repository_id'],
            'owner': plan['owner'], 'owner_id': plan['owner_id'],
            'ref': allowed['GITHUB_REF'], 'event': 'push', 'harness_commit': event_sha,
            'run_id': os.environ.get('GITHUB_RUN_ID'),
            'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT')}


def inventory(args):
    plan = json.loads(args.plan.read_text())
    check_limits(plan['limits'])
    baseline = json.loads((args.plan.parent / 'base-files.json').read_text())
    context = context_guard(args.harness, plan)
    args.output.mkdir(parents=True, exist_ok=False)
    identity = source_guard(args.source, plan, baseline, args.variant)
    full = expected_tree(plan, baseline, args.variant)
    files, data_by_name, package_scripts = [], {}, []
    total = 0
    for name in sorted(full):
        if not selected(name):
            continue
        data = read_source(args.source, name, plan['limits']['file_bytes'])
        if blob_oid(data) != full[name]['oid']:
            raise ValueError('Working bytes differ from pinned Git blob: ' + name)
        total += len(data)
        if total > plan['limits']['expanded_bytes']:
            raise ValueError('Source scope exceeds expanded size limit')
        member = 'source/' + name
        files.append({'path': name, 'archive_path': member, 'bytes': len(data),
                      'git_blob': full[name]['oid'], 'sha256': digest(data),
                      'crc32': zlib.crc32(data) & 0xffffffff})
        data_by_name[member] = data
        if PurePosixPath(name).name == 'package.json':
            package = json.loads(data)
            package_scripts.append({'path': name, 'name': package.get('name'),
                                    'scripts': package.get('scripts', {})})
    archive = args.output / ('scope-' + args.variant + '.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for row in files:
            info = zipfile.ZipInfo(row['archive_path'], date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data_by_name[row['archive_path']])
    if archive.stat().st_size > plan['limits']['archive_bytes']:
        archive.unlink()
        raise ValueError('Compressed archive too large; no artifact will be exported')
    manifest = {'schema': 1, 'purpose': 'native-scope-inventory-only',
                'native_tests_executed': False, 'context': context, 'variant': args.variant,
                'source': identity, 'limits': plan['limits'], 'expanded_bytes': total,
                'archive_bytes': archive.stat().st_size, 'archive_sha256': digest(archive.read_bytes()),
                'scope_files_sha256': digest(canonical(files)), 'files': files,
                'package_scripts': package_scripts,
                'lock_metadata': [{'path': name, 'git_blob': full[name]['oid']}
                                  for name in sorted(full) if PurePosixPath(name).name in LOCK_NAMES],
                'excluded_sensitive_paths': [name for name in sorted(full) if sensitive_name(name)]}
    verify_archive(archive, manifest)
    verify_provenance(manifest, plan, baseline, args.variant, context['harness_commit'],
                      context['run_id'], context['run_attempt'])
    if source_guard(args.source, plan, baseline, args.variant) != identity:
        raise ValueError('Source identity changed during inventory')
    (args.output / 'inventory-manifest.json').write_bytes(canonical(manifest) + b'\n')
    print(json.dumps({'status': 'inventory_verified_not_tested', 'variant': args.variant,
                      'source': identity, 'selected_files': len(files), 'expanded_bytes': total,
                      'archive_bytes': archive.stat().st_size, 'archive_sha256': manifest['archive_sha256'],
                      'scope_files_sha256': manifest['scope_files_sha256']}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode', required=True)
    inv = sub.add_parser('inventory')
    for name in ('plan', 'harness', 'source', 'output'):
        inv.add_argument('--' + name, type=Path, required=True)
    inv.add_argument('--variant', choices=('baseline', 'candidate'), required=True)
    ver = sub.add_parser('verify-archive')
    ver.add_argument('--archive', type=Path, required=True)
    ver.add_argument('--manifest', type=Path, required=True)
    ver.add_argument('--plan', type=Path, required=True)
    ver.add_argument('--variant', choices=('baseline', 'candidate'), required=True)
    ver.add_argument('--harness-commit', required=True)
    ver.add_argument('--run-id', required=True)
    ver.add_argument('--run-attempt', required=True)
    args = parser.parse_args()
    if args.mode == 'inventory':
        inventory(args)
    else:
        manifest = json.loads(args.manifest.read_text())
        plan = json.loads(args.plan.read_text())
        baseline = json.loads((args.plan.parent / 'base-files.json').read_text())
        verify_provenance(manifest, plan, baseline, args.variant, args.harness_commit,
                          args.run_id, args.run_attempt)
        if args.archive.stat().st_size != manifest['archive_bytes']:
            raise ValueError('Archive file size differs from the bound manifest')
        verify_archive(args.archive, manifest)
        print('Pinned source/run identity, path, size, CRC and hash checks passed; no files extracted.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('INVENTORY BLOCKED: ' + str(error), file=sys.stderr)
        sys.exit(1)
