"""Proposed frozen native comparison; mixed-option precedence remains unresolved."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
M = json.loads((HERE / 'manifest.json').read_text())
SOURCE = Path(sys.argv[1]).resolve(strict=True)
TEMP = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
OUT = TEMP / 'semi-foundation-evidence'
WORK = TEMP / 'semi-foundation-work'
OUT.mkdir(exist_ok=False)
WORK.mkdir(exist_ok=False)
for name in ('home', 'tmp', 'tooling'):
    (WORK / name).mkdir()
ENV = {
    'PATH': os.environ['PATH'], 'HOME': str(WORK / 'home'),
    'TMPDIR': str(WORK / 'tmp'), 'TMP': str(WORK / 'tmp'),
    'TEMP': str(WORK / 'tmp'), 'CI': 'true', 'LANG': 'C.UTF-8',
    'FORCE_COLOR': '0', 'npm_config_ignore_scripts': 'true',
    'npm_config_yes': 'false', 'npm_config_update_notifier': 'false',
    'npm_config_cache': str(WORK / 'npm-cache'),
    'YARN_IGNORE_SCRIPTS': 'true',
}
REPORT = {
    'schema_version': 3, 'status': 'not_validated', 'started_utc': None,
    'scope': M['scope'], 'base_commit': M['base_commit'], 'base_tree': M['base_tree'],
    'run': {k: os.environ.get(k) for k in ('GITHUB_REPOSITORY', 'GITHUB_REPOSITORY_ID', 'GITHUB_REPOSITORY_OWNER_ID', 'GITHUB_SHA', 'GITHUB_WORKFLOW_SHA', 'GITHUB_REF', 'GITHUB_EVENT_NAME', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_ARCH')},
    'steps': [], 'source_checks': [], 'artifact_complete': True, 'artifact_omissions': [],
    'native_results': {}, 'broader_results': {}, 'mixed_observations': {},
    'scoped_fix_verified': False, 'all_requested_checks_passed': False,
    'mixed_compatibility_requires_review': True,
    'child_environment_keys': sorted(ENV),
}
EXECUTION_STARTED = time.monotonic()
BASE_ENTRIES = []
TEST_ADDED = False
PRODUCTION_FIXED = False
ORIGINAL_PRODUCTION = None
GENERATED = {}
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
ALLOWED_EVIDENCE = {'receipt.json', 'dependency-metadata.json'}
for name in ('node-version', 'npm-version', 'yarn-version', 'install', 'plugin-build',
             'original-baseline', 'base-typecheck', 'base-lint', 'expanded-red',
             'base-compatibility', 'focused-green', 'original-candidate',
             'candidate-compatibility', 'candidate-typecheck', 'candidate-lint',
             'changed-lint', 'negative-control', 'restored-green'):
    ALLOWED_EVIDENCE.update({name + '.log', name + '.json'})
for name in ('initial', 'after-install', 'plugin', 'tests', 'candidate', 'reverted', 'restored', 'final'):
    ALLOWED_EVIDENCE.add('source-' + name + '.json')
ALLOWED_EVIDENCE.update({'base-compatibility-observations.json', 'candidate-compatibility-observations.json'})

def check(condition, message):
    if not condition:
        raise RuntimeError(message)

def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def digest(data, kind='sha256'):
    return hashlib.new(kind, data).hexdigest()

def regular(path):
    check(not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode),
          'Expected regular nonsymlink file: ' + str(path))
    return path

def file_sha(path):
    with regular(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def blob(data):
    return digest(b'blob ' + str(len(data)).encode() + b'\0' + data, 'sha1')

def save():
    (OUT / 'receipt.json').write_text(json.dumps(REPORT, indent=2) + '\n')

def git(*args, cwd=SOURCE):
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args],
                            cwd=cwd, env=ENV, capture_output=True, timeout=30)
    check(result.returncode == 0, 'Read-only Git command failed: ' + ' '.join(args))
    check(len(result.stdout) <= 2 * 1024 * 1024, 'Git metadata exceeds bound')
    return result.stdout

def tree_hash(entries):
    root = {}
    for path, mode, sha in entries:
        node = root
        parts = path.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        check(parts[-1] not in node, 'Duplicate tree path')
        node[parts[-1]] = (mode, sha)
    def walk(node):
        rows = []
        for name, value in node.items():
            is_dir = isinstance(value, dict)
            mode, sha = ('40000', walk(value)) if is_dir else value
            rows.append((name.encode() + (b'/' if is_dir else b''),
                         mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(sha)))
        data = b''.join(row[1] for row in sorted(rows))
        return digest(b'tree ' + str(len(data)).encode() + b'\0' + data, 'sha1')
    return walk(root)

def indexed_entries(cwd):
    rows = git('ls-files', '--stage', '-z', cwd=cwd).split(b'\0')
    entries = []
    for row in filter(None, rows):
        fields, path = row.split(b'\t', 1)
        mode, sha, stage = fields.decode('ascii').split(' ')
        text = path.decode('utf-8')
        check(stage == '0' and mode in ('100644', '100755'), 'Unexpected Git stage/mode')
        check(not text.startswith('/') and all(x not in ('', '.', '..') for x in text.split('/')),
              'Unsafe Git index path')
        entries.append((text, mode, sha))
    return entries

def usage():
    total = 0
    for root in (SOURCE, WORK / 'yarn-cache'):
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name != '.git' and not (Path(folder) / name).is_symlink()]
            for name in files:
                path = Path(folder) / name
                try:
                    if not path.is_symlink() and stat.S_ISREG(path.stat().st_mode):
                        total += path.stat().st_size
                except FileNotFoundError:
                    pass
    return total, shutil.disk_usage(WORK).free

def stop_process(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # A shell can exit before its descendants; always terminate the entire owned group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)

def run(name, argv, seconds, install=False):
    remaining = M['limits']['overall_seconds'] - (time.monotonic() - EXECUTION_STARTED)
    check(remaining >= 1, 'Overall execution budget exhausted')
    seconds = min(seconds, int(remaining))
    step = {'name': name, 'argv': argv, 'cwd': str(SOURCE), 'started_utc': now(),
            'timeout_seconds': seconds, 'status': 'running', 'output_truncated': False}
    REPORT['steps'].append(step)
    save()
    log = OUT / (name + '.log')
    started = time.monotonic()
    last_stats = started - 10
    peak = 0
    min_free = shutil.disk_usage(WORK).free
    stop = None
    count = 0
    recent = ''
    stopped_group = False
    proc = subprocess.Popen(argv, cwd=SOURCE, env=ENV, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    os.set_blocking(proc.stdout.fileno(), False)
    try:
        with selectors.DefaultSelector() as selector, log.open('xb') as output:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                for key, _ in selector.select(timeout=0.5):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    room = M['limits']['artifact_file_bytes'] - count
                    output.write(chunk[:max(0, room)])
                    count += len(chunk)
                    output.flush()
                    if count > M['limits']['artifact_file_bytes']:
                        step['output_truncated'] = True
                        stop = 'Command output exceeds 8 MiB; bounded prefix retained'
                    if install:
                        recent = (recent + chunk.decode('utf-8', errors='replace'))[-131072:]
                        if re.search(r'(?im)^error[^\n]*(?:401|403|unauthori[sz]ed|forbidden|access.denied|authenticat|integrity|incompatible.*engine|engine.*incompatible|frozen.lockfile|lockfile.*update|certificate|CERT_|SSL_|TLS_)', recent):
                            stop = 'Installer reported access/auth/integrity/engine/lock/TLS stop-class error'
                        for line in recent.splitlines():
                            if re.match(r'^error\b', line, re.I):
                                for url in re.findall(r'https?://[^\s\"\']+', line):
                                    if urllib.parse.urlparse(url).hostname not in M['registry_hosts']:
                                        stop = 'Unexpected URL host in package-manager error'
                if time.monotonic() - started >= seconds:
                    stop = 'Command time budget reached'
                if time.monotonic() - last_stats >= 5:
                    size, free = usage()
                    peak, min_free = max(peak, size), min(min_free, free)
                    last_stats = time.monotonic()
                    if size >= M['limits']['install_soft_bytes']:
                        stop = 'Source plus dependencies/cache reached conservative soft capacity limit'
                    if free < M['limits']['minimum_free_bytes']:
                        stop = 'Runner free disk below minimum'
                if stop and not stopped_group:
                    stop_process(proc)
                    stopped_group = True
                if stop and time.monotonic() - started > seconds + 15:
                    step['output_truncated'] = True
                    break
            if proc.poll() is None:
                try:
                    proc.wait(timeout=max(1, seconds - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    stop = 'Command time budget reached after output closed'
                    stop_process(proc)
            code = proc.wait()
    finally:
        if proc.poll() is None:
            stop_process(proc)
        proc.stdout.close()
    step.update(exit_code=code, elapsed_seconds=round(time.monotonic() - started, 3),
                status='completed' if not stop else 'stopped', stop_reason=stop,
                log=log.name, log_sha256=file_sha(log), log_bytes=log.stat().st_size,
                observed_output_bytes=count, peak_sampled_source_dependencies_cache_bytes=peak,
                minimum_sampled_free_bytes=min_free)
    if step['output_truncated']:
        REPORT['artifact_complete'] = False
    save()
    print(name + ': exit=' + str(code) + ', status=' + step['status'], flush=True)
    check(not stop and not step['output_truncated'], name + ': ' + str(stop))
    return step

def tooling():
    url = M['yarn']['url']
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError('Unexpected Yarn bootstrap redirect; no alternate download')
    check(urllib.parse.urlparse(url).hostname == 'registry.npmjs.org', 'Yarn host mismatch')
    # Native hosted TLS and trust are unchanged. No local proxy/CA configuration is copied.
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': 'semi-native-validation',
                                                 'Accept-Encoding': 'identity'})
    archive = WORK / 'yarn.tgz'
    with opener.open(request, timeout=45) as response:
        check(response.status == 200 and response.geturl() == url, 'Yarn response mismatch')
        data = response.read(M['yarn']['tarball_bytes'] + 1)
    check(len(data) == M['yarn']['tarball_bytes'], 'Yarn tarball byte count differs')
    check(digest(data, 'sha512') == M['yarn']['sha512'], 'Yarn tarball SHA-512 mismatch')
    check('sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode() == M['yarn']['sri'],
          'Yarn official SRI mismatch')
    archive.write_bytes(data)
    seen = set()
    expanded = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            parts = member.name.rstrip('/').split('/')
            check(parts[0] == 'package' and all(x not in ('', '.', '..') for x in parts)
                  and '\\' not in member.name, 'Unsafe Yarn archive path')
            check(member.name not in seen and len(seen) < 32, 'Yarn duplicate/member limit')
            seen.add(member.name)
            check(not member.issym() and not member.islnk() and member.sparse is None,
                  'Yarn link/sparse member rejected')
            target = WORK / 'tooling' / Path(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            check(member.isfile(), 'Yarn nonregular member rejected')
            expanded += member.size
            check(expanded <= M['yarn']['expanded_bytes'], 'Yarn expanded byte limit')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as inp, target.open('xb') as out:
                value = inp.read(member.size + 1)
                check(len(value) == member.size, 'Yarn member size differs')
                out.write(value)
    paths = sorted(str(path.relative_to(WORK / 'tooling' / 'package'))
                   for path in (WORK / 'tooling' / 'package').rglob('*') if path.is_file())
    check(paths == M['yarn']['files'] and expanded == M['yarn']['expanded_bytes'],
          'Yarn exact regular-file inventory differs')
    package = json.loads((WORK / 'tooling/package/package.json').read_text())
    check(package['name'] == 'yarn' and package['version'] == M['yarn']['version'], 'Yarn identity drift')
    REPORT['yarn_bootstrap'] = {'url': url, 'sha512': M['yarn']['sha512'],
                                'sri': M['yarn']['sri'], 'bytes': len(data),
                                'expanded_bytes': expanded, 'files': paths,
                                'lifecycle_scripts_executed': False}
    save()
    return str(WORK / 'tooling/package/bin/yarn.js')

def audit_inputs():
    package = json.loads((SOURCE / 'package.json').read_text())
    check(package['packageManager'] == M['package_manager'], 'Package manager declaration drift')
    check(package['scripts']['test:unit'] == M['test_script'], 'Original npm test script drift')
    blocked = ('preinstall', 'install', 'postinstall', 'prepare', 'pretest:unit', 'posttest:unit')
    check(not any(name in package.get('scripts', {}) for name in blocked), 'Unexpected root lifecycle')
    manifests = sorted(path for path, _, _ in BASE_ENTRIES
                       if path.startswith('packages/') and path.endswith('/package.json'))
    workspace = []
    for path in manifests:
        item = json.loads((SOURCE / path).read_text())
        if item.get('name'):
            workspace.append(path)
            check(not any(name in item.get('scripts', {}) for name in blocked[:4]),
                  'Unexpected workspace automatic install lifecycle')
    check(workspace == sorted(M['workspace_manifests']), 'Workspace manifest inventory mismatch')
    lock = (SOURCE / 'yarn.lock').read_text()
    urls = re.findall(r'^  resolved "([^\"]+)"$', lock, re.M)
    integrity = re.findall(r'^  integrity (.+)$', lock, re.M)
    blocks = re.findall(r'^\S[^\n]*:\n', lock, re.M)
    check(len(urls) == len(integrity) == len(blocks) == M['lock_blocks'], 'Lock block completeness differs')
    hosts = {}
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        check(parsed.scheme == 'https' and parsed.hostname in M['registry_hosts']
              and parsed.username is None and parsed.password is None
              and parsed.port in (None, 443), 'Lock references unexpected network destination')
        hosts[parsed.hostname] = hosts.get(parsed.hostname, 0) + 1
    REPORT['input_audit'] = {'workspace_manifests': workspace, 'lock_blocks': len(blocks),
                              'resolved_hosts': hosts, 'automatic_install_hooks': [],
                              'original_config_hashes': {path: file_sha(SOURCE / path)
                                  for path in M['native_configuration_files']}}
    save()

def dependencies():
    result = {}
    for name, expected in M['required_root_dependency_versions'].items():
        target = SOURCE / 'node_modules' / name / 'package.json'
        check(target.resolve().is_relative_to(SOURCE / 'node_modules'), 'Dependency metadata escaped node_modules')
        data = json.loads(regular(target).read_text())
        check(data['name'] == name and data['version'] == expected, 'Dependency version drift: ' + name)
        result[name] = {'version': data['version'], 'package_json_sha256': file_sha(target)}
    integrity = SOURCE / 'node_modules/.yarn-integrity'
    regular(integrity)
    json.loads(integrity.read_text())
    result['yarn_integrity_sha256'] = file_sha(integrity)
    (OUT / 'dependency-metadata.json').write_text(json.dumps(result, indent=2) + '\n')
    REPORT['dependency_metadata'] = result
    save()

def package_evidence():
    omitted = []
    eligible = []
    total = 0
    for path in sorted(OUT.iterdir(), key=lambda item: (item.name != 'receipt.json', item.name)):
        if path.name not in ALLOWED_EVIDENCE or path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            omitted.append({'name': path.name, 'reason': 'Not an allowlisted regular nonsymlink evidence file'})
            continue
        size = path.stat().st_size
        if size > M['limits']['artifact_file_bytes'] or total + size > M['limits']['artifact_expanded_bytes']:
            omitted.append({'name': path.name, 'reason': 'Expanded evidence limit', 'bytes': size})
            continue
        eligible.append(path)
        total += size
    if omitted:
        REPORT['artifact_complete'] = False
        REPORT['artifact_omissions'].extend(omitted)
    REPORT['artifact_members'] = [{'name': path.name, 'bytes': path.stat().st_size,
                                   'sha256': file_sha(path)} for path in eligible if path.name != 'receipt.json']
    save()
    bundle = TEMP / 'semi-foundation-red.tar.gz'
    def write_bundle(paths):
        check(all(regular(path).stat().st_size <= M['limits']['artifact_file_bytes'] for path in paths)
              and sum(path.stat().st_size for path in paths) <= M['limits']['artifact_expanded_bytes'],
              'Final evidence members exceed expanded bounds')
        with tarfile.open(bundle, 'w:gz', compresslevel=9, format=tarfile.USTAR_FORMAT) as tar:
            for path in paths:
                regular(path)
                tar.add(path, arcname=path.name, recursive=False)
    check((OUT / 'receipt.json') in eligible, 'Receipt must be included in evidence')
    total = sum(path.stat().st_size for path in eligible)
    if any(path.stat().st_size > M['limits']['artifact_file_bytes'] for path in eligible) or total > M['limits']['artifact_expanded_bytes']:
        REPORT['artifact_complete'] = False
        REPORT['artifact_omissions'].append({'reason': 'Final expanded size limit; only receipt uploaded'})
        REPORT['artifact_members'] = []
        save()
        eligible = [OUT / 'receipt.json']
    write_bundle(eligible)
    if bundle.stat().st_size > M['limits']['artifact_compressed_bytes']:
        REPORT['artifact_complete'] = False
        REPORT['artifact_omissions'].append({'reason': 'Compressed evidence exceeded 12,000,000 bytes; only receipt uploaded'})
        REPORT['artifact_members'] = []
        save()
        write_bundle([OUT / 'receipt.json'])
    check(bundle.stat().st_size <= M['limits']['artifact_compressed_bytes'], 'Receipt bundle exceeds compressed ceiling')
    print('evidence bundle bytes=' + str(bundle.stat().st_size) + ', sha256=' + file_sha(bundle), flush=True)

def source_check(label, inventory=None):
    check(git('rev-parse', 'HEAD').decode().strip() == M['base_commit'], 'Source HEAD drift')
    check(git('rev-parse', 'HEAD^{tree}').decode().strip() == M['base_tree'], 'Base tree drift')
    check(indexed_entries(SOURCE) == BASE_ENTRIES, 'Original index drift')
    current = []
    changed = []
    for path, mode, original_sha in BASE_ENTRIES:
        target = SOURCE / path
        check(target.resolve().is_relative_to(SOURCE), 'Source path escaped checkout')
        actual = blob(regular(target).read_bytes())
        expected = M['production']['candidate_blob'] if PRODUCTION_FIXED and path == M['production']['path'] else original_sha
        check(actual == expected, 'Unexpected tracked blob: ' + path)
        actual_mode = '100755' if target.stat().st_mode & stat.S_IXUSR else '100644'
        check(actual_mode == mode, 'Tracked executable mode changed: ' + path)
        current.append([path, mode, actual])
        if actual != original_sha:
            changed.append(path)
    check(changed == ([M['production']['path']] if PRODUCTION_FIXED else []), 'Production delta differs')
    logical = list(current)
    if TEST_ADDED:
        for item in M['tests']:
            target = regular(SOURCE / item['source_path'])
            value = target.read_bytes()
            check(digest(value) == item['sha256'] and blob(value) == item['git_blob'], 'Test/probe bytes drift')
            check(not target.stat().st_mode & stat.S_IXUSR, 'Test/probe mode drift')
            logical.append([item['source_path'], '100644', item['git_blob']])
    allowed = {row[0] for row in logical} | set(GENERATED)
    for folder, dirs, files in os.walk(SOURCE, followlinks=False):
        retained = []
        for name in dirs:
            child = Path(folder) / name
            if name == 'node_modules' or child == SOURCE / '.git':
                continue
            check(not child.is_symlink(), 'Unexpected source directory symlink')
            retained.append(name)
        dirs[:] = retained
        for name in files:
            path = str((Path(folder) / name).relative_to(SOURCE))
            check(path in allowed, 'Unexpected added source file: ' + path)
    for path, expected in GENERATED.items():
        check(file_sha(SOURCE / path) == expected, 'Generated plugin file drift: ' + path)
    expected_tree = M['candidate_tree'] if PRODUCTION_FIXED else M['source_with_test_tree'] if TEST_ADDED else M['base_tree']
    check(tree_hash(logical) == expected_tree, 'Complete logical tree mismatch')
    check(file_sha(SOURCE / 'yarn.lock') == M['lock_sha256'], 'Frozen lock drift')
    check(file_sha(SOURCE / 'package.json') == M['package_sha256'], 'Manifest drift')
    data = (json.dumps(current, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    item = {'label': label, 'utc': now(), 'tracked_count': len(current),
            'tracked_inventory_sha256': digest(data), 'logical_tree': expected_tree,
            'production_fixed': PRODUCTION_FIXED, 'changed_original_paths': changed,
            'tests_added': TEST_ADDED, 'generated_plugin_files': dict(GENERATED)}
    if inventory:
        (OUT / inventory).write_bytes(data)
        item['inventory_file'] = inventory
    REPORT['source_checks'].append(item)
    save()


def add_tests():
    global TEST_ADDED
    for item in M['tests']:
        value = regular(HERE / item['payload_name']).read_bytes()
        check(digest(value) == item['sha256'] and blob(value) == item['git_blob'], 'Payload test/probe mismatch')
        target = SOURCE / item['source_path']
        check(not target.exists() and not target.is_symlink(), 'Test/probe already exists')
        with target.open('xb') as stream:
            stream.write(value)
        target.chmod(0o644)
    TEST_ADDED = True
    source_check('tests-added', 'source-tests.json')


def set_production(fixed):
    global PRODUCTION_FIXED
    target = SOURCE / M['production']['path']
    expected = M['production']['candidate_sha256'] if PRODUCTION_FIXED else M['production']['original_sha256']
    check(file_sha(target) == expected, 'Production pre-edit drift')
    value = ORIGINAL_PRODUCTION
    if fixed:
        for old, new in M['production']['replacements']:
            check(value.count(old.encode()) == 1, 'Production replacement is not unique')
            value = value.replace(old.encode(), new.encode())
    check(digest(value) == M['production']['candidate_sha256' if fixed else 'original_sha256'], 'Production result differs')
    target.write_bytes(value)
    PRODUCTION_FIXED = fixed


def native_summary(label, step):
    path = OUT / (label + '.json')
    summary = {'exit_code': step['exit_code'], 'valid_native_result': False}
    try:
        regular(path)
        check(path.stat().st_size <= M['limits']['artifact_file_bytes'], 'Native JSON over bound')
        native = json.loads(path.read_text())
        keys = ['numTotalTests', 'numFailedTests', 'numPassedTests', 'numPendingTests',
                'numTotalTestSuites', 'numFailedTestSuites', 'numPassedTestSuites',
                'numPendingTestSuites', 'numRuntimeErrorTestSuites', 'numTodoTests',
                'wasInterrupted', 'success']
        summary['counts'] = {key: native.get(key) for key in keys}
        summary['assertions'] = [{'name': a['fullName'], 'status': a['status'],
            'failureMessages': a.get('failureMessages', [])}
            for suite in native.get('testResults', []) for a in suite.get('assertionResults', [])]
        summary['suites'] = [{'name': suite.get('name'), 'status': suite.get('status'),
            'testExecError': suite.get('testExecError')} for suite in native.get('testResults', [])]
        summary['valid_native_result'] = not native.get('wasInterrupted') and native.get('numRuntimeErrorTestSuites', 0) == 0 and not any(s['testExecError'] for s in summary['suites'])
        summary['json_sha256'] = file_sha(path)
    except Exception as error:
        summary['read_error'] = str(error)
    REPORT['native_results'][label] = summary
    save()
    return summary


def focused_matches(summary, fixed):
    if not summary.get('valid_native_result'):
        return False
    expected = M['focused_cases']
    actual = summary.get('assertions', [])
    names = [a['name'] for a in actual]
    if len(names) != len(set(names)) or set(names) != {c['full_name'] for c in expected}:
        return False
    if len(summary['suites']) != 2:
        return False
    if {Path(s['name']).name for s in summary['suites']} != {t['payload_name'] for t in M['tests'] if not t.get('diagnostic_only')}:
        return False
    want = {c['full_name']: c for c in expected}
    for item in actual:
        case = want[item['name']]
        status = 'passed' if fixed else case['predicted_status']
        if item['status'] != status:
            return False
        if status == 'passed':
            if item['failureMessages']:
                return False
        else:
            if len(item['failureMessages']) != 1:
                return False
            message = ANSI.sub('', item['failureMessages'][0])
            if 'expect(received).toBe(expected) // Object.is equality' not in message:
                return False
            expected_lines = re.findall(r'^\s*Expected:\s*("[^"\n]*")\s*$', message, re.M)
            received_lines = re.findall(r'^\s*Received:\s*("[^"\n]*")\s*$', message, re.M)
            if expected_lines != [json.dumps(case['expected'])] or received_lines != [json.dumps(case['predicted_received'])]:
                return False
    counts = summary['counts']
    failures = 0 if fixed else M['focused_expected_base_failed']
    return (summary['exit_code'] == (0 if fixed else 1)
            and counts['numTotalTests'] == M['focused_expected_total']
            and counts['numFailedTests'] == failures
            and counts['numPassedTests'] == M['focused_expected_total'] - failures
            and counts['numPendingTests'] == counts['numTodoTests'] == 0
            and counts['success'] is fixed)


def native(label, paths, focused=False, fixed=False, diagnostic=False):
    argv = ['npm', 'run', 'test:unit', '--', '--runInBand', '--watch=false', '--notify=false',
            '--runTestsByPath', *paths, '--json', '--outputFile=' + str(OUT / (label + '.json'))]
    seconds = M['limits']['test_seconds'] if focused or diagnostic else M['limits']['original_test_seconds']
    step = run(label, argv, seconds)
    source_check(label)
    summary = native_summary(label, step)
    if focused:
        summary['expected_focused_result_verified'] = focused_matches(summary, fixed)
    save()
    return summary


def observe_mixed(label):
    target = OUT / (label + '-observations.json')
    ENV['SEMI_CURRENCY_OBSERVATIONS'] = str(target)
    try:
        probe = next(t for t in M['tests'] if t.get('diagnostic_only'))
        summary = native(label, [probe['source_path']], diagnostic=True)
        value = json.loads(regular(target).read_text())
        check(value.get('diagnosticOnly') is True and value.get('noPublicContractAsserted') is True,
              'Compatibility diagnostic markers missing')
        expected = [{'precision': 2, 'maximumFractionDigits': 0},
                    {'precision': 0, 'minimumFractionDigits': 1},
                    {'precision': 2, 'minimumFractionDigits': 0, 'maximumFractionDigits': 0},
                    {'precision': 2, 'maximumFractionDigits': 1},
                    {'minimumFractionDigits': 3, 'maximumFractionDigits': 1},
                    {'maximumFractionDigits': 0}]
        check([x['options'] for x in value['observations']] == expected, 'Compatibility inputs drift')
        REPORT['mixed_observations'][label] = value
        REPORT['mixed_observations'][label]['native_probe_exit'] = summary['exit_code']
    except Exception as error:
        REPORT['mixed_observations'][label] = {'error': str(error)}
    finally:
        ENV.pop('SEMI_CURRENCY_OBSERVATIONS', None)
        save()


def compile_plugin():
    root = SOURCE / 'packages/semi-eslint-plugin/lib'
    check(not root.exists(), 'Plugin build output must initially be absent')
    step = run('plugin-build', ['npm', 'run', 'build:lib', '--workspace=eslint-plugin-semi-design'], 120)
    if root.exists():
        check(not root.is_symlink(), 'Plugin output symlink')
        actual = []
        for path in root.rglob('*'):
            check(not path.is_symlink(), 'Plugin generated symlink')
            if path.is_file():
                actual.append(str(path.relative_to(SOURCE)))
        check(sorted(actual) == sorted(M['generated_plugin_files']), 'Unexpected plugin output inventory')
        GENERATED.update({path: file_sha(SOURCE / path) for path in actual})
    REPORT['plugin_build_exit'] = step['exit_code']
    source_check('plugin-build', 'source-plugin.json')


def broader(label):
    type_step = run(label + '-typecheck', ['node', 'node_modules/typescript/bin/tsc', '--noEmit', '--pretty', 'false', '--incremental', 'false'], M['limits']['check_seconds'])
    source_check(label + '-typecheck')
    lint_step = run(label + '-lint', ['npm', 'run', 'lint:script', '--', '--format', 'json', '--output-file=' + str(OUT / (label + '-lint.json'))], M['limits']['check_seconds'])
    source_check(label + '-lint')
    REPORT['broader_results'][label] = {'root_typecheck_exit': type_step['exit_code'], 'full_script_lint_exit': lint_step['exit_code']}
    save()


def main():
    global BASE_ENTRIES, ORIGINAL_PRODUCTION
    REPORT['started_utc'] = now()
    guards = {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REPOSITORY': 'dvd233/flowgram.ai',
              'GITHUB_REPOSITORY_ID': '1352889832', 'GITHUB_REPOSITORY_OWNER_ID': '111864431',
              'GITHUB_REF': 'refs/heads/verify/semi-currency-native-20261007',
              'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64'}
    check(all(os.environ.get(k) == v for k, v in guards.items()), 'Execution destination guard failed')
    check(re.fullmatch('[0-9a-f]{40}', os.environ.get('GITHUB_SHA', '')) is not None, 'Invalid SHA')
    check(os.environ.get('GITHUB_WORKFLOW_SHA') == os.environ['GITHUB_SHA'], 'Workflow SHA mismatch')
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    check(SOURCE == workspace / 'source' and HERE == workspace / 'payload/semi-hosted-validation', 'Unexpected checkout paths')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    check(event['after'] == os.environ['GITHUB_SHA'] and event['ref'] == guards['GITHUB_REF'] and not event.get('deleted'), 'Event mismatch')
    check(event['repository']['private'] is False, 'Private repository not authorized')
    check(git('rev-parse', 'HEAD', cwd=HERE).decode().strip() == os.environ['GITHUB_SHA'], 'Payload HEAD mismatch')
    header = git('cat-file', '-p', 'HEAD', cwd=workspace / 'payload').split(b'\n\n', 1)[0]
    parents = [x.removeprefix(b'parent ').decode() for x in header.splitlines() if x.startswith(b'parent ')]
    check(parents == [M['parent_commit']], 'Wrong payload parent')
    payload = indexed_entries(workspace / 'payload')
    check(sorted(x[0] for x in payload) == sorted(M['payload_paths']), 'Payload inventory mismatch')
    for path, mode, h in payload:
        check(mode == '100644' and blob(regular(workspace / 'payload' / path).read_bytes()) == h, 'Payload bytes/mode mismatch')
    REPORT['payload_tree'] = git('rev-parse', 'HEAD^{tree}', cwd=HERE).decode().strip()
    REPORT['payload_parent'] = parents[0]
    REPORT['payload_files'] = [{'path': p, 'git_blob': h, 'sha256': file_sha(workspace / 'payload' / p)} for p, _, h in payload]
    BASE_ENTRIES = indexed_entries(SOURCE)
    check(len(BASE_ENTRIES) == M['base_blob_count'] and tree_hash(BASE_ENTRIES) == M['base_tree'], 'Original tree mismatch')
    check(not (SOURCE / 'node_modules').exists(), 'Pre-existing node_modules')
    ORIGINAL_PRODUCTION = regular(SOURCE / M['production']['path']).read_bytes()
    check(digest(ORIGINAL_PRODUCTION) == M['production']['original_sha256'], 'Original production mismatch')
    source_check('initial', 'source-initial.json')
    audit_inputs()
    REPORT['tool_versions'] = {}
    for name in ('node', 'npm'):
        step = run(name + '-version', [name, '--version'], 30)
        actual = (OUT / (name + '-version.log')).read_text().strip()
        REPORT['tool_versions'][name] = actual
        check(step['exit_code'] == 0 and actual, 'Tool version failure')
        if name == 'node':
            check(actual == M['node_version'], 'Node version mismatch')
    yarn = tooling()
    step = run('yarn-version', ['node', yarn, '--version'], 30)
    check(step['exit_code'] == 0 and (OUT / 'yarn-version.log').read_text().strip() == M['yarn']['version'], 'Yarn version mismatch')
    step = run('install', ['node', yarn, 'install', '--frozen-lockfile', '--ignore-scripts', '--non-interactive', '--cache-folder', str(WORK / 'yarn-cache')], M['limits']['install_seconds'], install=True)
    source_check('after-install', 'source-after-install.json')
    check(step['exit_code'] == 0, 'Frozen install failed')
    dependencies()
    baseline = native('original-baseline', [M['baseline_native_file']])
    add_tests()
    paths = [t['source_path'] for t in M['tests'] if not t.get('diagnostic_only')]
    red = native('expanded-red', paths, focused=True)
    observe_mixed('base-compatibility')
    set_production(True)
    source_check('candidate-applied', 'source-candidate.json')
    green = native('focused-green', paths, focused=True, fixed=True)
    comparison = native('original-candidate', [M['baseline_native_file']])
    observe_mixed('candidate-compatibility')
    set_production(False)
    source_check('production-reverted', 'source-reverted.json')
    negative = native('negative-control', paths, focused=True)
    set_production(True)
    source_check('production-restored', 'source-restored.json')
    restored = native('restored-green', paths, focused=True, fixed=True)
    REPORT['native_fix_cycle_verified'] = all(s.get('expected_focused_result_verified') for s in (red, green, negative, restored))
    save()
    compile_plugin()
    set_production(False)
    source_check('broader-base')
    broader('base')
    set_production(True)
    source_check('broader-candidate')
    broader('candidate')
    changed_paths = [M['production']['path'], *paths]
    lint = run('changed-lint', ['node', 'node_modules/eslint/bin/eslint.js', '--no-ignore', '--format', 'json', '--output-file=' + str(OUT / 'changed-lint.json'), *changed_paths], 120)
    source_check('changed-lint')
    REPORT['changed_lint_exit'] = lint['exit_code']
    def original_pass(s):
        return s.get('valid_native_result') and s['exit_code'] == 0 and s['counts']['success'] is True and s['counts']['numPassedTests'] > 0
    def fingerprint(s):
        return sorted((a['name'], a['status']) for a in s.get('assertions', []))
    original_comparison = original_pass(baseline) and original_pass(comparison) and fingerprint(baseline) == fingerprint(comparison)
    REPORT['original_test_identity_and_pass_preserved'] = bool(original_comparison)
    REPORT['scoped_fix_verified'] = all(s.get('expected_focused_result_verified') for s in (red, green, negative, restored)) and bool(original_comparison) and lint['exit_code'] == 0
    REPORT['all_requested_checks_passed'] = REPORT['scoped_fix_verified'] and all(v == 0 for checks in REPORT['broader_results'].values() for v in checks.values())
    REPORT['mixed_compatibility_requires_review'] = True
    REPORT['status'] = 'scoped_fix_verified_pending_compatibility_review' if REPORT['scoped_fix_verified'] else 'not_validated'
    save()


try:
    main()
except Exception as error:
    REPORT['status'] = 'not_validated'
    REPORT['scoped_fix_verified'] = False
    REPORT['all_requested_checks_passed'] = False
    REPORT['blocker'] = {'type': type(error).__name__, 'message': str(error)}
    print('STOP: ' + type(error).__name__ + ': ' + str(error), flush=True)
finally:
    if BASE_ENTRIES:
        try:
            source_check('final', 'source-final.json')
        except Exception as error:
            REPORT['status'] = 'not_validated'
            REPORT['scoped_fix_verified'] = False
            REPORT['all_requested_checks_passed'] = False
            REPORT['source_integrity_error'] = str(error)
    REPORT['completed_utc'] = now()
    save()
    try:
        package_evidence()
    except Exception as error:
        REPORT['artifact_complete'] = False
        REPORT['artifact_packaging_error'] = str(error)
        save()
        print('Evidence packaging failed: ' + str(error), flush=True)
raise SystemExit(0 if REPORT['scoped_fix_verified'] and REPORT['artifact_complete'] else 1)
