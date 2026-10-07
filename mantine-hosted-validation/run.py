"""Proposed hosted-only runner. Do not execute before repository and scope approval."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tarfile
import time
import traceback

HERE = Path(__file__).resolve().parent
M = json.loads((HERE / 'manifest.json').read_text())
SOURCE = Path(sys.argv[1]).resolve()
LANE = sys.argv[2]
if LANE not in ('base', 'candidate'):
    raise SystemExit('Invalid lane')
TEMP = Path(os.environ['RUNNER_TEMP']).resolve()
OUT = TEMP / f'mantine-results-{LANE}'
TOOLS = TEMP / f'mantine-tools-{LANE}'
OUT.mkdir(exist_ok=False)
TOOLS.mkdir(exist_ok=False)
(TOOLS / 'home').mkdir()
PATCH = HERE / 'candidate.patch'
YARN = TOOLS / 'yarn.js'
FILES = [f['path'] for f in M['files']]
TEST = FILES[1]
ENV = {
    'PATH': os.environ['PATH'], 'HOME': str(TOOLS / 'home'),
    'TMPDIR': os.environ.get('TMPDIR', '/tmp'), 'CI': 'true',
    'LANG': 'C.UTF-8', 'FORCE_COLOR': '0',
    'YARN_ENABLE_SCRIPTS': 'false', 'YARN_ENABLE_GLOBAL_CACHE': 'false',
    'YARN_CACHE_FOLDER': str(TOOLS / 'yarn-cache'),
    'YARN_GLOBAL_FOLDER': str(TOOLS / 'yarn-global'),
    'npm_config_ignore_scripts': 'true', 'npm_config_yes': 'false',
    'npm_config_cache': str(TOOLS / 'npm-cache'), 'npm_config_update_notifier': 'false',
    'YARN_ENABLE_TELEMETRY': 'false',
}
REPORT = {
    'schema_version': 1, 'lane': LANE, 'status': 'running',
    'run': {k: os.environ.get(k) for k in (
        'GITHUB_REPOSITORY', 'GITHUB_REPOSITORY_ID', 'GITHUB_SHA',
        'GITHUB_WORKFLOW_REF', 'GITHUB_WORKFLOW_SHA', 'GITHUB_RUN_ID',
        'GITHUB_RUN_ATTEMPT', 'GITHUB_ACTOR', 'GITHUB_REPOSITORY_OWNER_ID',
        'GITHUB_REF', 'GITHUB_EVENT_NAME', 'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_ARCH')},
    'source_repository': M['upstream_repository'], 'base_commit': M['base_commit'],
    'base_tree': M['base_tree'], 'expected_candidate_tree': M['candidate_tree'],
    'expected_negative_tree': M['negative_tree'],
    'final_source_commit': M['final_source_commit'], 'patch_sha256': M['patch_sha256'],
    'payload_files_sha256': {}, 'scope': M['scope'],
    'install_differences': M['install_differences'],
    'child_environment_keys': sorted(ENV), 'steps': [], 'violations': [],
    'skipped': [], 'artifact_complete': True,
}
JEST = ['npm', 'run', 'jest', '--', '--runInBand']
PLAN = [
    ('build', ['npm', 'run', 'build'], 1800),
    ('typecheck', ['npm', 'run', 'typecheck'], 900),
    ('target', JEST + ['--runTestsByPath', TEST, '--json', f'--outputFile={OUT / "target.json"}'], 300),
    ('full-hooks', JEST + ['packages/@mantine/hooks', '--json', f'--outputFile={OUT / "full-hooks.json"}'], 900),
    ('changed-lint', ['npx', 'oxlint', '-c', 'oxlint.config.mjs', *FILES], 300),
    ('full-lint', ['npm', 'run', 'lint'], 900),
    ('changed-format', ['npm', 'run', 'format:write:files', '--', *FILES], 300),
]
if LANE == 'candidate':
    PLAN += [
        ('negative', JEST + ['--runTestsByPath', TEST, '--json', f'--outputFile={OUT / "negative.json"}'], 300),
        ('restored-target', JEST + ['--runTestsByPath', TEST, '--json', f'--outputFile={OUT / "restored-target.json"}'], 300),
    ]
REPORT['planned_commands'] = [{'name': n, 'argv': a, 'timeout_seconds': t} for n, a, t in PLAN]
STATE = 'base'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save():
    (OUT / 'receipt.json').write_text(json.dumps(REPORT, indent=2) + '\n')


def git(*args, data=None):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                   cwd=SOURCE, env=ENV, input=data).decode().strip()


def assert_source():
    expected_tree = M[f'{STATE}_tree']
    actual_tree = git('write-tree')
    assert actual_tree == expected_tree, (STATE, actual_tree, expected_tree)
    assert not git('diff', '--name-only'), 'Tracked working files differ from the bound index'
    assert sha(SOURCE / 'yarn.lock') == M['lock_sha256'], 'Original lock changed'
    assert sha(SOURCE / 'package.json') == M['package_sha256'], 'Original manifest changed'
    for i, f in enumerate(M['files']):
        expect = f['git_blob'] if STATE == 'candidate' or (STATE == 'negative' and i == 1) else f['base_blob']
        assert git('hash-object', '--', f['path']) == expect, f['path']
    return {'state': STATE, 'tree': actual_tree, 'lock_sha256': sha(SOURCE / 'yarn.lock'),
            'files_sha256': {f: sha(SOURCE / f) for f in FILES}}


def run(name, argv, seconds, bind=True, zero=True):
    step = {'name': name, 'argv': argv, 'cwd': str(SOURCE), 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'status': 'running', 'source': assert_source() if bind else None}
    REPORT['steps'].append(step)
    save()
    start = time.monotonic()
    log = OUT / f'{name}.log'
    with log.open('wb') as output:
        proc = subprocess.Popen(argv, cwd=SOURCE, env=ENV, stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            step['timed_out'] = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            code = proc.returncode
    if log.stat().st_size > M['artifact_per_file_expanded_limit_bytes']:
        REPORT['violations'].append(f'{name}: log exceeds expanded per-file evidence limit')
        step['log_scan_incomplete'] = True
    with log.open('rb') as captured:
        text = captured.read(M['artifact_per_file_expanded_limit_bytes']).decode(errors='replace')
    step.update(exit_code=code, elapsed_seconds=round(time.monotonic() - start, 3),
                status='passed' if code == 0 and not step.get('timed_out') else 'failed',
                log=log.name, log_sha256=sha(log), log_bytes=log.stat().st_size)
    relevant = [line for line in text.splitlines() if re.search(r'warn|skip|deprecat|YN(?!0000)\d{4}', line, re.I)]
    step['warning_and_skip_lines'] = relevant[:100]
    step['warning_and_skip_lines_total'] = len(relevant)
    if len(relevant) > 100:
        step['warning_excerpt_note'] = 'First 100 matches only; complete command output remains in log'
    if zero and (code != 0 or step.get('timed_out')):
        REPORT['violations'].append(f'{name}: original command exited {code}')
    if bind:
        step['source_after'] = assert_source()
    print(f'{name}: exit={code}, seconds={step["elapsed_seconds"]}', flush=True)
    save()
    return step, text


def inspect_jest(name, expected=None, negative=False):
    path = OUT / f'{name}.json'
    if not path.exists():
        REPORT['violations'].append(f'{name}: native Jest JSON missing')
        return
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        REPORT['violations'].append(f'{name}: native JSON is not a regular nonsymlink file')
        return
    if path.stat().st_size > M['artifact_per_file_expanded_limit_bytes']:
        REPORT['violations'].append(f'{name}: native JSON exceeds expanded per-file evidence limit')
        return
    data = json.loads(path.read_text())
    step = next(s for s in REPORT['steps'] if s['name'] == name)
    keys = ('numFailedTestSuites', 'numFailedTests', 'numPassedTestSuites', 'numPassedTests',
            'numPendingTestSuites', 'numPendingTests', 'numRuntimeErrorTestSuites',
            'numTodoTests', 'numTotalTestSuites', 'numTotalTests', 'wasInterrupted', 'success')
    step['jest'] = {key: data.get(key) for key in keys}
    step['jest_json_sha256'] = sha(path)
    assertions = [a for suite in data.get('testResults', []) for a in suite.get('assertionResults', [])]
    step['nonpassing_assertions'] = [{'fullName': a.get('fullName'), 'status': a.get('status')}
                                     for a in assertions if a.get('status') != 'passed']
    valid = not step.get('timed_out') and not data.get('wasInterrupted') and data.get('numRuntimeErrorTestSuites') == 0
    valid = valid and data.get('numPendingTests') == 0 and data.get('numTodoTests') == 0
    valid = valid and data.get('numPendingTestSuites') == 0
    if name == 'full-hooks':
        paths = sorted(str(Path(suite['name']).relative_to(SOURCE)) for suite in data['testResults'])
        digest = hashlib.sha256(('\n'.join(paths) + '\n').encode()).hexdigest()
        step['full_hooks_suite_paths_sha256'] = digest
        valid = valid and len(paths) == M['full_hooks_suite_count']
        valid = valid and data.get('numTotalTestSuites') == M['full_hooks_suite_count']
        valid = valid and digest == M['full_hooks_suite_paths_sha256']
    if negative:
        failures = sorted(a['fullName'] for a in assertions if a.get('status') == 'failed')
        valid = valid and step['exit_code'] == 1 and data.get('success') is False
        valid = valid and data.get('numTotalTests') == 43 and data.get('numPassedTests') == 39
        valid = valid and data.get('numFailedTests') == 4 and failures == sorted(M['expected_negative_failures'])
        valid = valid and data.get('numTotalTestSuites') == 1 and data.get('numFailedTestSuites') == 1
        matcher_checks = []
        for assertion in assertions:
            if assertion.get('status') != 'failed':
                continue
            details = assertion.get('failureDetails', [])
            matcher = details[0].get('matcherResult', {}) if len(details) == 1 and isinstance(details[0], dict) else {}
            message = re.sub(r'\x1b\[[0-9;]*m', '', str(matcher.get('message', ''))).strip()
            messages = assertion.get('failureMessages', [])
            native_text = re.sub(r'\x1b\[[0-9;]*m', '', str(messages[0])) if len(messages) == 1 else ''
            matched = matcher.get('pass') is False and message == M['negative_matcher_message']
            matched = matched and M['negative_matcher_message'] in native_text
            matcher_checks.append({'fullName': assertion['fullName'], 'matched': bool(matched),
                                   'native_matcher_message': message})
        step['negative_matcher_checks'] = matcher_checks
        valid = valid and len(matcher_checks) == 4 and all(x['matched'] for x in matcher_checks)
        step['expected_negative_control_matched'] = bool(valid)
    else:
        valid = valid and step['exit_code'] == 0 and data.get('success') is True
        valid = valid and data.get('numFailedTests') == 0 and data.get('numTotalTests', 0) > 0
        if expected is not None:
            valid = valid and data.get('numTotalTests') == expected and data.get('numPassedTests') == expected
    if not valid:
        REPORT['violations'].append(f'{name}: native Jest result did not satisfy the evidence gate')
    save()


def package_root_inventory(source):
    source = source.resolve(strict=True)
    if (source / 'node_modules').is_symlink():
        raise ValueError('node_modules must not be an external or linked inventory root')
    map_path = source / 'node_modules/.package-map.json'
    if map_path.is_symlink() or not stat.S_ISREG(map_path.lstat().st_mode):
        raise ValueError('node_modules/.package-map.json must be a regular nonsymlink file')
    map_bytes = map_path.read_bytes()
    try:
        package_map = json.loads(map_bytes)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f'Invalid node_modules/.package-map.json: {exc}') from exc
    if not isinstance(package_map, dict) or set(package_map) != {'packages'}:
        raise ValueError('Unsupported node_modules/.package-map.json schema')
    locations = package_map['packages']
    if not isinstance(locations, dict) or '.' not in locations:
        raise ValueError('Package map must contain package locations and the project root')
    records, install_hooks, seen = [], [], set()
    project_root_seen = False
    for locator, entry in sorted(locations.items()):
        if not isinstance(entry, dict) or not isinstance(entry.get('url'), str) or not isinstance(entry.get('dependencies'), dict):
            raise ValueError(f'Invalid package-map location entry: {locator}')
        url = entry['url']
        if not (url == '..' or url.startswith('./') or url.startswith('../')):
            raise ValueError(f'Package-map location must be relative: {locator}')
        try:
            root = (map_path.parent / url).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f'Cannot resolve package-map location {locator}: {url}') from exc
        if not root.is_relative_to(source) or not root.is_dir():
            raise ValueError(f'Package-map location escapes source or is not a directory: {locator}')
        relative = root.relative_to(source)
        if locator == '.':
            if root != source:
                raise ValueError('The project-root package-map entry does not resolve to the source root')
            project_root_seen = True
        if 'node_modules' in relative.parts:
            category = 'dependency'
        elif root == source:
            category = 'project'
        elif ((len(relative.parts) == 3 and relative.parts[0] == 'packages') or
              (len(relative.parts) == 2 and relative.parts[0] == 'apps')):
            category = 'workspace'
        else:
            raise ValueError(f'Unexpected workspace location in package map: {relative}')
        if root in seen:
            continue
        seen.add(root)
        manifest = root / 'package.json'
        manifest_relative = str(manifest.relative_to(source))
        if manifest.is_symlink() or not manifest.exists() or not stat.S_ISREG(manifest.lstat().st_mode):
            raise ValueError(f'Mapped package manifest missing or not a regular nonsymlink file: {manifest_relative}')
        raw = manifest.read_bytes()
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f'Invalid mapped package manifest {manifest_relative}: {exc}') from exc
        if not isinstance(data, dict) or not isinstance(data.get('name'), str) or not isinstance(data.get('version'), str):
            raise ValueError(f'Invalid mapped package identity: {manifest_relative}')
        scripts = data.get('scripts', {})
        if not isinstance(scripts, dict):
            raise ValueError(f'Invalid scripts object in mapped package manifest: {manifest_relative}')
        selected = {k: v for k, v in scripts.items() if k in ('preinstall', 'install', 'postinstall')}
        if selected:
            install_hooks.append({'name': data['name'], 'version': data['version'],
                                  'path': manifest_relative, 'scripts': selected})
        records.append({'root': str(relative), 'category': category,
                        'manifest_sha256': hashlib.sha256(raw).hexdigest()})
    if not project_root_seen:
        raise ValueError('Project root was not inventoried')
    records.sort(key=lambda record: record['root'])
    return {
        'complete': True, 'execution_permitted': False,
        'scope': 'Every unique installed package root declared by Yarn 4.18.0 node_modules/.package-map.json, including workspace and project roots',
        'excluded_scope': 'Package-internal test fixtures, documentation and other package.json files not declared as installed roots in the Yarn map; not an audit of every JSON file in package contents',
        'map_path': 'node_modules/.package-map.json', 'map_sha256': hashlib.sha256(map_bytes).hexdigest(),
        'mapped_location_count': len(locations), 'unique_package_root_count': len(records),
        'dependency_package_root_count': sum(record['category'] == 'dependency' for record in records),
        'workspace_and_project_root_count': sum(record['category'] != 'dependency' for record in records),
        'root_manifest_inventory_sha256': hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'entries': install_hooks,
    }


def inventory():
    REPORT['installed_hooks'] = {'complete': False, 'execution_permitted': False,
                                 'map_path': 'node_modules/.package-map.json'}
    REPORT['installed_hooks'].update(package_root_inventory(SOURCE))

def package_report():
    archive = TEMP / f'mantine-native-{LANE}.tar.gz'
    commands = {s['name'] for s in REPORT['steps']} | {n for n, _, _ in PLAN}
    allowed_names = {f'{name}.log' for name in commands} | {
        'receipt.json', 'runner-error.log', 'target.json', 'full-hooks.json',
        'negative.json', 'restored-target.json'}
    save()
    regular = []
    rejected = []
    expanded = 0
    for path in sorted(OUT.iterdir()):
        info = path.lstat()
        if path.name not in allowed_names or not stat.S_ISREG(info.st_mode) or path.is_symlink():
            rejected.append({'path': path.name, 'reason': 'Not an allowlisted regular nonsymlink report'})
            continue
        expanded += info.st_size
        if info.st_size > M['artifact_per_file_expanded_limit_bytes']:
            rejected.append({'path': path.name, 'bytes': info.st_size, 'reason': 'Expanded per-file limit exceeded'})
        regular.append(path)
    if expanded > M['artifact_expanded_limit_bytes_per_lane']:
        rejected.append({'reason': 'Expanded combined evidence limit exceeded', 'bytes': expanded})
    REPORT['artifact_expanded_bytes_before_pack'] = expanded
    REPORT['artifact_limits'] = {k: M[k] for k in (
        'artifact_per_file_expanded_limit_bytes', 'artifact_expanded_limit_bytes_per_lane',
        'artifact_expanded_limit_bytes_all_lanes', 'artifact_limit_bytes_per_lane', 'artifact_limit_bytes_all_lanes')}

    def fail_closed(reason):
        REPORT['artifact_complete'] = False
        REPORT['status'] = 'failed'
        REPORT['violations'].append(reason)
        REPORT['artifact_rejections'] = rejected
        REPORT['omitted_artifact_files'] = [
            {'path': path.name, 'bytes': path.stat().st_size, 'sha256': sha(path)}
            for path in regular if path.name != 'receipt.json']

    if rejected:
        fail_closed('Artifact inputs violate allowlist/type/expanded-size limits; only failure receipt retained')
    save()
    selected = [OUT / 'receipt.json'] if rejected else regular

    def pack(paths):
        total = 0
        with tarfile.open(archive, 'w:gz', compresslevel=9, dereference=False) as tar:
            for path in paths:
                info = path.lstat()
                assert path.name in allowed_names and stat.S_ISREG(info.st_mode) and not path.is_symlink()
                assert info.st_size <= M['artifact_per_file_expanded_limit_bytes']
                total += info.st_size
                assert total <= M['artifact_expanded_limit_bytes_per_lane']
                tar.add(path, arcname=path.name, recursive=False)

    if not rejected and (any(path.stat().st_size > M['artifact_per_file_expanded_limit_bytes'] for path in selected) or
                         sum(path.stat().st_size for path in selected) > M['artifact_expanded_limit_bytes_per_lane']):
        fail_closed('Final expanded archive inputs exceed limits; only failure receipt retained')
        save()
        selected = [OUT / 'receipt.json']
    pack(selected)
    if archive.stat().st_size > M['artifact_limit_bytes_per_lane']:
        fail_closed('Complete compressed results exceed 4,500,000 bytes; only failure receipt retained')
        save()
        pack([OUT / 'receipt.json'])
    if archive.stat().st_size > M['artifact_limit_bytes_per_lane']:
        archive.unlink()
        raise RuntimeError('Even receipt exceeds artifact limit; upload must fail')
    print(f'Artifact: {archive.name}, {archive.stat().st_size} compressed bytes', flush=True)


try:
    binding = M['execution_repository']
    expected = {
        'GITHUB_REPOSITORY': binding['full_name'], 'GITHUB_REPOSITORY_ID': str(binding['id']),
        'GITHUB_REPOSITORY_OWNER_ID': str(binding['owner_id']), 'GITHUB_REF': binding['ref'],
        'GITHUB_EVENT_NAME': 'push', 'RUNNER_ENVIRONMENT': 'github-hosted',
        'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64'}
    for key, value in expected.items():
        assert os.environ[key] == value, f'Unexpected {key}'
    assert re.fullmatch(r'[0-9a-f]{40}', os.environ['GITHUB_SHA'])
    assert os.environ['GITHUB_WORKFLOW_SHA'] == os.environ['GITHUB_SHA']
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    assert event['after'] == os.environ['GITHUB_SHA'] and event['ref'] == binding['ref']
    assert not event.get('deleted')
    assert event['repository']['id'] == binding['id']
    assert event['repository']['owner']['id'] == binding['owner_id']
    actual_workflow = HERE.parent / '.github/workflows/mantine-frozen-native.yml'
    payload_head = subprocess.check_output(['git', '-C', str(HERE.parent), 'rev-parse', 'HEAD'], env=ENV).decode().strip()
    assert payload_head == os.environ['GITHUB_SHA']
    REPORT['payload_tree'] = subprocess.check_output(
        ['git', '-C', str(HERE.parent), 'rev-parse', 'HEAD^{tree}'], env=ENV).decode().strip()
    payload_paths = subprocess.check_output(
        ['git', '-C', str(HERE.parent), 'ls-files'], env=ENV).decode().splitlines()
    expected_paths = ['.github/workflows/mantine-frozen-native.yml'] + [
        'mantine-hosted-validation/' + name for name in ('manifest.json', 'candidate.patch', 'run.py', 'REVIEW.md')]
    assert sorted(payload_paths) == sorted(expected_paths), 'Payload tree contains unexpected code or workflows'
    assert not subprocess.check_output(
        ['git', '-C', str(HERE.parent), 'status', '--porcelain', '--untracked-files=no'], env=ENV).strip()
    REPORT['payload_files_sha256'][str(actual_workflow.relative_to(HERE.parent))] = sha(actual_workflow)
    assert git('rev-parse', 'HEAD') == M['base_commit']
    assert git('rev-parse', 'HEAD^{tree}') == M['base_tree']
    assert sha(PATCH) == M['patch_sha256']
    for name in ('manifest.json', 'candidate.patch', 'run.py', 'REVIEW.md'):
        REPORT['payload_files_sha256']['mantine-hosted-validation/' + name] = sha(HERE / name)
    assert not (SOURCE / 'node_modules').exists(), 'Source must begin without installed dependencies'
    assert not (SOURCE / '.buildinfo.json').exists(), 'Build cache must be absent'
    assert not list(SOURCE.glob('packages/*/*/esm')), 'Generated ESM outputs must be absent'
    assert not list(SOURCE.glob('packages/*/*/cjs')), 'Generated CJS outputs must be absent'
    assert not list(SOURCE.glob('packages/*/*/lib')), 'Generated declarations must be absent'
    assert_source()
    if LANE == 'candidate':
        git('apply', '--check', '--index', str(PATCH))
        git('apply', '--index', str(PATCH))
        STATE = 'candidate'
        assert_source()
        assert set(git('diff', '--cached', '--name-only', 'HEAD').splitlines()) == set(FILES)
        for f in M['files']:
            assert sha(SOURCE / f['path']) == f['sha256']
    else:
        assert not git('status', '--porcelain', '--untracked-files=no')
    REPORT['starting_source'] = assert_source()
    suite_paths = sorted(SOURCE.glob('packages/@mantine/hooks/src/**/*.test.*'))
    modifier_matches = []
    pattern = M['full_hooks_source_modifier_audit']['pattern']
    for path in suite_paths:
        content = path.read_text()
        for match in re.finditer(pattern, content):
            modifier_matches.append({'path': str(path.relative_to(SOURCE)),
                                     'line': content.count('\n', 0, match.start()) + 1, 'text': match.group(0)})
    REPORT['full_hooks_source_modifier_audit'] = {'files_read': len(suite_paths), 'matches': modifier_matches}
    assert len(suite_paths) == M['full_hooks_suite_count']
    assert modifier_matches == M['full_hooks_source_modifier_audit']['matches'], 'Unexpected skip/todo/focus marker needs review'
    affected_hooks = []
    selected_scripts = {
        'package.json': ['build', 'typecheck', 'jest', 'lint', 'oxlint', 'stylelint', 'format:write:files'],
        'apps/mantine.dev/package.json': ['typecheck'],
        'apps/help.mantine.dev/package.json': ['typecheck']}
    for rel, selected in selected_scripts.items():
        scripts = json.loads((SOURCE / rel).read_text()).get('scripts', {})
        for name in selected:
            for prefix in ('pre', 'post'):
                hook = prefix + name
                if hook in scripts:
                    affected_hooks.append({'path': rel, 'hook': hook, 'command': scripts[hook]})
    REPORT['selected_command_hooks_suppressed_by_ignore_scripts'] = affected_hooks
    assert not affected_hooks, 'Existing pre/post command hooks would be suppressed; pause for review'
    workspace_hooks = []
    for rel in git('ls-files', '*package.json').splitlines():
        data = json.loads((SOURCE / rel).read_text())
        scripts = data.get('scripts', {})
        selected = {k: v for k, v in scripts.items() if k in (
            'preinstall', 'install', 'postinstall', 'prebuild', 'postbuild',
            'pretypecheck', 'posttypecheck', 'prejest', 'postjest', 'prelint', 'postlint',
            'preoxlint', 'postoxlint', 'prestylelint', 'poststylelint',
            'preformat:write:files', 'postformat:write:files')}
        if selected:
            workspace_hooks.append({'path': rel, 'scripts': selected})
    REPORT['workspace_hook_inventory'] = workspace_hooks
    _, version = run('node-version', ['node', '--version'], 30)
    assert version.strip() == M['node_version']
    run('npm-version', ['npm', '--version'], 30)
    step, _ = run('yarn-download', ['curl', '--fail', '--silent', '--show-error', '--location',
                      '--proto', '=https', '--proto-redir', '=https', '--max-time', '120',
                      M['yarn_url'], '--output', str(YARN)], 150)
    assert step['exit_code'] == 0 and sha(YARN) == M['yarn_sha256']
    _, version = run('yarn-version', ['node', str(YARN), '--version'], 30)
    assert version.strip() == M['yarn_version']
    step, _ = run('install', ['node', str(YARN), 'install', '--immutable', '--mode=skip-build'], 1200)
    assert step['exit_code'] == 0, 'Install failed; no native commands may start'
    ENV['npm_config_offline'] = 'true'
    ENV['YARN_ENABLE_NETWORK'] = 'false'
    REPORT['check_environment_keys'] = sorted(ENV)
    inventory()
    for binary in ('tsx', 'tsc', 'jest', 'oxlint', 'stylelint', 'oxfmt'):
        assert (SOURCE / 'node_modules/.bin' / binary).exists(), f'Missing locked tool: {binary}'
    for name, argv, seconds in PLAN:
        if name == 'negative':
            git('restore', '--source', M['base_commit'], '--staged', '--worktree', '--', FILES[0])
            STATE = 'negative'
            assert_source()
        if name == 'restored-target':
            # Reapply only the frozen production-source hunk; test bytes stay untouched.
            git('apply', '--index', '--include=' + FILES[0], str(PATCH))
            STATE = 'candidate'
            assert_source()
        step, output = run(name, argv, seconds, zero=name != 'negative')
        if name == 'build':
            match = re.search(r'Built:\s*(\d+),\s*skipped:\s*(\d+)', output)
            step['build_summary'] = {'built': int(match[1]), 'skipped': int(match[2])} if match else None
            if step['exit_code'] == 0 and (not match or int(match[1]) < 1 or int(match[2]) != 0):
                REPORT['violations'].append('build: fresh-run nonzero built / zero cache-skips summary absent')
        if name in ('target', 'restored-target'):
            inspect_jest(name, 43 if LANE == 'candidate' else 36)
        if name == 'full-hooks':
            inspect_jest(name)
        if name == 'negative':
            inspect_jest(name, negative=True)
    REPORT['ending_source'] = assert_source()
except Exception as exc:
    REPORT['violations'].append(f'{type(exc).__name__}: {exc}')
    (OUT / 'runner-error.log').write_text(traceback.format_exc())
finally:
    executed = {s['name'] for s in REPORT['steps']}
    REPORT['skipped'] = [{'name': n, 'reason': 'Not reached after prerequisite failure; not a pass'}
                         for n, _, _ in PLAN if n not in executed]
    REPORT['status'] = 'failed' if REPORT['violations'] or REPORT['skipped'] else 'passed'
    REPORT['completed_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    package_report()
raise SystemExit(0 if REPORT['status'] == 'passed' else 1)
