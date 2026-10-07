"""Proposed hosted-only RED verification; never runs a production patch or UI suite."""
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
    'schema_version': 1, 'status': 'not_validated', 'started_utc': None,
    'scope': M['scope'], 'source_repository': M['upstream_repository'],
    'base_commit': M['base_commit'], 'base_tree': M['base_tree'],
    'source_with_test_tree': M['source_with_test_tree'],
    'run': {key: os.environ.get(key) for key in (
        'GITHUB_REPOSITORY', 'GITHUB_REPOSITORY_ID', 'GITHUB_REPOSITORY_OWNER_ID',
        'GITHUB_SHA', 'GITHUB_WORKFLOW_SHA', 'GITHUB_WORKFLOW_REF', 'GITHUB_REF',
        'GITHUB_EVENT_NAME', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT',
        'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_ARCH')},
    'steps': [], 'source_checks': [], 'artifact_complete': True,
    'artifact_omissions': [], 'child_environment_keys': sorted(ENV),
    'production_patch_applied': False, 'native_test_started': False,
    'expected_red_validated': False, 'whole_project_green': False,
}
BASE_ENTRIES = []
TEST_ADDED = False
ALLOWED_EVIDENCE = {'receipt.json', 'source-initial.json', 'source-after-install.json',
                    'source-final.json', 'node-version.log', 'npm-version.log',
                    'yarn-version.log', 'install.log', 'foundation-red.log',
                    'foundation-red.json', 'dependency-metadata.json'}
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


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


def source_check(label, inventory=None):
    check(git('rev-parse', 'HEAD').decode().strip() == M['base_commit'], 'Source HEAD drift')
    check(git('rev-parse', 'HEAD^{tree}').decode().strip() == M['base_tree'], 'Base tree drift')
    check(indexed_entries(SOURCE) == BASE_ENTRIES, 'Original index drift')
    current = []
    for path, mode, expected in BASE_ENTRIES:
        target = SOURCE / path
        check(target.resolve().is_relative_to(SOURCE), 'Source path escaped checkout')
        value = regular(target).read_bytes()
        check(blob(value) == expected, 'Original tracked blob changed: ' + path)
        actual_mode = '100755' if target.stat().st_mode & stat.S_IXUSR else '100644'
        check(actual_mode == mode, 'Original tracked executable mode changed: ' + path)
        current.append([path, mode, expected])
    logical = list(current)
    if TEST_ADDED:
        test_path = regular(SOURCE / M['test']['source_path'])
        value = test_path.read_bytes()
        check(digest(value) == M['test']['sha256'] and blob(value) == M['test']['git_blob'],
              'New test bytes drift')
        actual_mode = '100755' if test_path.stat().st_mode & stat.S_IXUSR else '100644'
        check(actual_mode == '100644', 'New test executable mode drift')
        logical.append([M['test']['source_path'], '100644', blob(value)])
    allowed_paths = {entry[0] for entry in logical}
    for folder, dirs, files in os.walk(SOURCE, followlinks=False):
        retained = []
        for name in dirs:
            child = Path(folder) / name
            if name == 'node_modules' or child == SOURCE / '.git':
                continue
            check(not child.is_symlink(), 'Unexpected source directory symlink: ' + str(child))
            retained.append(name)
        dirs[:] = retained
        for name in files:
            path = str((Path(folder) / name).relative_to(SOURCE))
            check(path in allowed_paths, 'Unexpected added source file outside dependency directories: ' + path)
    expected_tree = M['source_with_test_tree'] if TEST_ADDED else M['base_tree']
    check(tree_hash(logical) == expected_tree, 'Logical complete source tree mismatch')
    check(file_sha(SOURCE / 'yarn.lock') == M['lock_sha256'], 'Frozen lock drift')
    check(file_sha(SOURCE / 'package.json') == M['package_sha256'], 'Root manifest drift')
    serialized = (json.dumps(current, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    result = {'label': label, 'utc': now(), 'original_blob_count': len(current),
              'original_inventory_sha256': digest(serialized), 'logical_tree': expected_tree,
              'test_added': TEST_ADDED, 'lock_sha256': M['lock_sha256'],
              'all_original_blobs_and_modes_unchanged': True}
    if inventory:
        (OUT / inventory).write_bytes(serialized)
        result['inventory_file'] = inventory
    REPORT['source_checks'].append(result)
    save()
    return result


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


def inspect_red(step):
    path = regular(OUT / 'foundation-red.json')
    check(path.stat().st_size <= M['limits']['artifact_file_bytes'], 'Native Jest JSON too large')
    native = json.loads(path.read_text())
    keys = ('numFailedTestSuites', 'numFailedTests', 'numPassedTestSuites', 'numPassedTests',
            'numPendingTestSuites', 'numPendingTests', 'numRuntimeErrorTestSuites',
            'numTodoTests', 'numTotalTestSuites', 'numTotalTests', 'wasInterrupted', 'success')
    REPORT['native_jest'] = {key: native[key] for key in keys if key in native}
    REPORT['native_jest_fields_absent'] = [key for key in keys if key not in native]
    REPORT['native_jest_json_sha256'] = file_sha(path)
    suites = native.get('testResults', [])
    check(len(suites) == 1, 'Expected exactly one actual native suite')
    suite = suites[0]
    assertions = suite.get('assertionResults', [])
    REPORT['native_assertions'] = [{'fullName': item.get('fullName'), 'status': item.get('status')}
                                    for item in assertions]
    save()
    check(step['exit_code'] == 1 and native.get('success') is False,
          'Native exit/result does not match expected RED')
    check(('wasInterrupted' not in native or native['wasInterrupted'] is False)
          and ('numRuntimeErrorTestSuites' not in native or native['numRuntimeErrorTestSuites'] == 0),
          'Interrupted or runtime-error run is not assertion RED')
    expected_counts = {'numTotalTestSuites': 1, 'numFailedTestSuites': 1,
                       'numPassedTestSuites': 0, 'numPendingTestSuites': 0,
                       'numTotalTests': 7, 'numFailedTests': 4,
                       'numPassedTests': 3, 'numPendingTests': 0}
    check(all(native.get(key) == value for key, value in expected_counts.items()),
          'Native counts differ from four failures and three controls')
    check(('numTodoTests' not in native or native['numTodoTests'] == 0) and not suite.get('testExecError'),
          'Todo/setup/import error is not expected RED')
    check(Path(suite['name']).resolve() == (SOURCE / M['test']['source_path']).resolve(),
          'Native suite path mismatch')
    check(suite.get('status') == 'failed', 'Native suite status mismatch')
    actual_names = [item.get('fullName') for item in assertions]
    check(len(actual_names) == len(set(actual_names)) == 7, 'Duplicate/missing assertions')
    cases = {case['full_name']: case for case in M['test']['cases']}
    check(set(actual_names) == set(cases), 'Unexpected assertion name')
    matcher_checks = []
    for item in assertions:
        case = cases[item['fullName']]
        check(item.get('status') == case['predicted_status'], 'Actual assertion status differs from hypothesis')
        messages = item.get('failureMessages', [])
        if case['predicted_status'] == 'passed':
            check(not messages, 'Passing control has failure output')
            continue
        check(len(messages) == 1 and isinstance(messages[0], str), 'Expected one native matcher failure')
        message = ANSI.sub('', messages[0])
        diagnostic = 'expect(received).toBe(expected) // Object.is equality'
        expected = re.findall(r'^\s*Expected:\s*("[^"\n]*")\s*$', message, re.M)
        received = re.findall(r'^\s*Received:\s*("[^"\n]*")\s*$', message, re.M)
        matched = (diagnostic in message and expected == [json.dumps(case['expected'])]
                   and received == [json.dumps(case['predicted_received'])])
        matcher_checks.append({'fullName': item['fullName'], 'matched': matched,
                               'native_expected_lines': expected, 'native_received_lines': received})
        REPORT['matcher_checks'] = matcher_checks
        save()
        check(matched, 'Native matcher diagnostic differs; cannot claim expected RED')
    check(len(matcher_checks) == 4, 'Missing zero-option matcher failures')
    REPORT['expected_red_validated'] = True
    REPORT['status'] = 'expected_red_validated'
    REPORT['interpretation'] = 'Original implementation failed four zero-option assertions; three controls passed. No fix, UI integration pass or full-project pass is established.'
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
        REPORT['artifact_omissions'].append({'reason': 'Compressed evidence exceeded 4,500,000 bytes; only receipt uploaded'})
        REPORT['artifact_members'] = []
        save()
        write_bundle([OUT / 'receipt.json'])
    check(bundle.stat().st_size <= M['limits']['artifact_compressed_bytes'], 'Receipt bundle exceeds compressed ceiling')
    print('evidence bundle bytes=' + str(bundle.stat().st_size) + ', sha256=' + file_sha(bundle), flush=True)


def main():
    global BASE_ENTRIES, TEST_ADDED
    REPORT['started_utc'] = now()
    expected_env = {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REPOSITORY': 'dvd233/flowgram.ai',
                    'GITHUB_REPOSITORY_ID': '1352889832', 'GITHUB_REPOSITORY_OWNER_ID': '111864431',
                    'GITHUB_REF': 'refs/heads/verify/semi-currency-native-20261007',
                    'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64'}
    check(all(os.environ.get(key) == value for key, value in expected_env.items()), 'Execution destination guard failed')
    check(re.fullmatch('[0-9a-f]{40}', os.environ.get('GITHUB_SHA', '')) is not None, 'Invalid event SHA')
    check(os.environ.get('GITHUB_WORKFLOW_SHA') == os.environ['GITHUB_SHA'], 'Workflow SHA mismatch')
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    check(SOURCE == workspace / 'source' and HERE == workspace / 'payload/semi-hosted-validation',
          'Unexpected checkout locations')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    check(event.get('after') == os.environ['GITHUB_SHA'] and event.get('ref') == expected_env['GITHUB_REF']
          and not event.get('deleted'), 'Event payload SHA/ref mismatch')
    check(git('rev-parse', 'HEAD', cwd=HERE).decode().strip() == os.environ['GITHUB_SHA'], 'Payload checkout SHA mismatch')
    commit_header = git('cat-file', '-p', 'HEAD', cwd=workspace / 'payload').split(b'\n\n', 1)[0]
    parents = [line.removeprefix(b'parent ').decode('ascii') for line in commit_header.splitlines()
               if line.startswith(b'parent ')]
    check(parents == [M['parent_commit']], 'Payload must have exactly the declared parent')
    REPORT['payload_parent'] = parents[0]
    payload_entries = indexed_entries(workspace / 'payload')
    check(sorted(path for path, _, _ in payload_entries) == sorted(M['payload_paths']), 'Payload must be exactly five files')
    for path, mode, expected in payload_entries:
        target = workspace / 'payload' / path
        check(mode == '100644' and blob(regular(target).read_bytes()) == expected,
              'Payload working bytes or indexed mode differ: ' + path)
    REPORT['payload_tree'] = git('rev-parse', 'HEAD^{tree}', cwd=HERE).decode().strip()
    REPORT['payload_files'] = [{'path': path, 'git_blob': sha,
                                'sha256': file_sha(workspace / 'payload' / path)}
                               for path, _, sha in payload_entries]
    BASE_ENTRIES = indexed_entries(SOURCE)
    check(len(BASE_ENTRIES) == M['base_blob_count'] and tree_hash(BASE_ENTRIES) == M['base_tree'],
          'Complete indexed upstream tree does not match reviewed source')
    check(not (SOURCE / M['test']['source_path']).exists()
          and not (SOURCE / M['test']['source_path']).is_symlink(), 'New test path already exists')
    check(not (SOURCE / 'node_modules').exists() and not (SOURCE / 'node_modules').is_symlink(),
          'Unexpected preexisting node_modules')
    source_check('initial', 'source-initial.json')
    audit_inputs()
    REPORT['tool_versions'] = {}
    for name in ('node', 'npm'):
        step = run(name + '-version', [name, '--version'], 30)
        actual = (OUT / (name + '-version.log')).read_text().strip()
        executable = shutil.which(name, path=ENV['PATH'])
        REPORT['tool_versions'][name] = {'version': actual, 'executable': executable,
                                         'resolved_executable': str(Path(executable).resolve()) if executable else None}
        save()
        check(step['exit_code'] == 0 and bool(actual), name + ' version command failed')
        if name == 'node':
            check(actual == M['node_version'], 'Exact Node version differs; no automatic tooling substitution')
    yarn = tooling()
    step = run('yarn-version', ['node', yarn, '--version'], 30)
    check(step['exit_code'] == 0 and (OUT / 'yarn-version.log').read_text().strip() == M['yarn']['version'],
          'Exact Yarn version differs')
    install = ['node', yarn, 'install', '--frozen-lockfile', '--ignore-scripts', '--non-interactive',
               '--cache-folder', str(WORK / 'yarn-cache')]
    step = run('install', install, M['limits']['install_seconds'], install=True)
    source_check('after-install', 'source-after-install.json')
    check(step['exit_code'] == 0, 'Original frozen install failed; no fallback')
    dependencies()
    value = regular(HERE / 'InputNumber.currency.test.js').read_bytes()
    check(digest(value) == M['test']['sha256'] and blob(value) == M['test']['git_blob'], 'Proposal test hash mismatch')
    test_path = SOURCE / M['test']['source_path']
    check(test_path.parent.resolve().is_relative_to(SOURCE), 'Test parent escaped source')
    with test_path.open('xb') as stream:
        stream.write(value)
    test_path.chmod(0o644)
    TEST_ADDED = True
    source_check('before-native-test')
    REPORT['native_test_started'] = True
    save()
    argv = ['npm', 'run', 'test:unit', '--', '--runInBand', '--watch=false', '--notify=false',
            '--runTestsByPath', M['test']['source_path'], '--json', '--outputFile=' + str(OUT / 'foundation-red.json')]
    step = run('foundation-red', argv, M['limits']['test_seconds'])
    source_check('after-native-test')
    inspect_red(step)


try:
    main()
except Exception as error:
    REPORT['status'] = 'not_validated'
    REPORT['expected_red_validated'] = False
    REPORT['blocker'] = {'type': type(error).__name__, 'message': str(error)}
    print('STOP: ' + type(error).__name__ + ': ' + str(error), flush=True)
finally:
    if BASE_ENTRIES:
        try:
            source_check('final', 'source-final.json')
        except Exception as error:
            REPORT['status'] = 'not_validated'
            REPORT['expected_red_validated'] = False
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

raise SystemExit(0 if REPORT['expected_red_validated'] and REPORT['artifact_complete'] else 1)
