#!/usr/bin/env python3
"""Run the reviewed original commands in a fresh GitHub-hosted workspace only.
No application server, browser, real API test, deployment, or network-policy edit.
The normal compiler/test worker processes are part of the original toolchain.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from classify_native_reports import focused, aggregate_summary, require_no_error_fields
from read_scope import jsonc

HERE = Path(__file__).resolve().parent
MAX_LOG_BYTES = 10_000_000
MAX_REPORT_BYTES = 100_000_000
SOURCE_TEST = 'packages/node-engine/form/__tests__/use-watch.test.tsx'
LIVE_CASES = [
 {'file': 'src/domain/__tests__/schemas/http-real.test.ts', 'fullName': 'WorkflowRuntime http schema should execute a workflow with HTTP request'},
 {'file': 'src/domain/__tests__/schemas/http-real.test.ts', 'fullName': 'WorkflowRuntime http schema should handle HTTP request with different inputs'},
 {'file': 'src/domain/__tests__/schemas/llm-real.test.ts', 'fullName': 'workflow runtime real llm test should execute workflow'},
]


def need(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def no_env(root):
    hits = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
        hits += [str(Path(current, name).relative_to(root)) for name in dirs + files if name == '.env' or name.startswith('.env.')]
    need(not hits, 'Unexpected dotenv input: ' + ', '.join(hits[:10]))


def original_source_guard(root, plan, variant, injected=False):
    expected = plan[variant]
    need(git(root, 'rev-parse', 'HEAD') == expected['commit'], 'Source HEAD changed')
    need(git(root, 'rev-parse', 'HEAD^{tree}') == expected['tree'], 'Source Git tree changed')
    need(not git(root, 'diff', '--name-only', 'HEAD'), 'Tracked source/config/lock changed')
    for item in plan['lock_metadata']:
        need(git(root, 'hash-object', item['path']) == item['git_blob'], 'Frozen lock changed: ' + item['path'])
    for item in plan['safety_config_blobs']:
        need(git(root, 'hash-object', item['path']) == item['git_blob'], 'Reviewed safety config changed')
    untracked = git(root, 'ls-files', '--others', '--exclude-standard').splitlines()
    need(untracked == ([SOURCE_TEST] if injected else []), 'Unexpected untracked source files: ' + repr(untracked[:10]))
    if injected:
        need(digest(root / SOURCE_TEST) == plan['focused_test_sha256'], 'Injected regression test changed')
    installed_lock = root / 'common/temp/pnpm-lock.yaml'
    if installed_lock.exists():
        need(installed_lock.read_bytes() == (root / 'common/config/rush/pnpm-lock.yaml').read_bytes(), 'Actual installed temporary lock differs from original lock')
    no_env(root)


def reviewed_lock_urls(root, plan):
    """Distinguish two blob/line-pinned deprecation links from dependency sources.
    The ordinary URL host allowlist is deliberately unchanged. No YAML dependency
    is added to the runner. Exact original blob and line bytes bind each exception.
    """
    exceptions = plan.get('lock_metadata_url_exceptions', [])
    observed = []
    matched = set()
    for lock in plan['lock_metadata']:
        raw = (root / lock['path']).read_bytes()
        oid = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        need(oid == lock['git_blob'], 'Lock bytes differ from the reviewed source: ' + lock['path'])
        for line_number, line in enumerate(raw.decode().splitlines(), 1):
            urls = re.findall(r'(?:https?|git\+https?|git|ssh)://[^\s\'"<>]+', line)
            exact = [(index, item) for index, item in enumerate(exceptions)
                     if item['path'] == lock['path'] and item['line'] == line_number]
            if exact:
                need(len(exact) == 1, 'Duplicate metadata URL exception')
                index, item = exact[0]
                need(item['git_blob'] == oid and item['line_sha256'] == hashlib.sha256(line.encode()).hexdigest(),
                     'Reviewed metadata URL row has changed')
                need(item['metadata_key'] == 'deprecated' and line.startswith('    deprecated: ')
                     and item['url'] == 'https://www.npmjs.com/support' and urls == [item['url']],
                     'Metadata exception is not the exact reviewed deprecation support link')
                matched.add(index)
                observed.append({'lock': lock['path'], 'line': line_number, 'git_blob': oid,
                                 'classification': 'exact_reviewed_deprecation_text_not_download', 'url': item['url']})
                continue
            for url in urls:
                parsed = urlsplit(url)
                need(parsed.scheme == 'https' and parsed.hostname in ('registry.npmjs.org', 'registry.yarnpkg.com') and not parsed.username and not parsed.password,
                     f"Unreviewed lockfile remote source at {lock['path']}:{line_number}; stop for source review")
                observed.append({'lock': lock['path'], 'line': line_number,
                                 'classification': 'allowed_registry_source', 'host': parsed.hostname})
    need(matched == set(range(len(exceptions))), 'A reviewed metadata URL exception was not found')
    return observed


def preflight(root, plan, variant, out):
    for key, value in plan['context'].items():
        need(os.environ.get(key) == value, 'Wrong hosted context: ' + key)
    need(os.environ.get('GITHUB_ACTIONS') == 'true', 'Hosted GitHub Actions only; no local execution')
    harness = HERE.parents[1]
    need(git(harness, 'rev-parse', 'HEAD') == os.environ.get('GITHUB_SHA'), 'Harness checkout is not the exact event commit')
    need(not git(harness, 'status', '--porcelain'), 'Harness checkout is dirty')
    for path, expected_hash in plan['harness_files'].items():
        need(digest(harness / path) == expected_hash, 'Reviewed harness file changed: ' + path)
    original_source_guard(root, plan, variant)
    rush = jsonc((root / 'rush.json').read_text())
    need(rush['rushVersion'] == '5.150.0' and rush['pnpmVersion'] == '10.6.5', 'Wrong original package-manager pins')
    need(not any(rush['eventHooks'].values()), 'Unexpected Rush event hooks')
    cache = jsonc((root / 'common/config/rush/build-cache.json').read_text())
    need(cache['buildCacheEnabled'] is False, 'Build cache must remain disabled by the original config')
    need(not jsonc((root / 'common/config/rush/rush-plugins.json').read_text())['plugins'], 'Unexpected Rush plugin')
    need(not (root / 'common/temp').exists(), 'Checkout already contains Rush temporary state')
    for row in plan['projects']:
        folder = root / row['path']
        for relative in ('node_modules', '.rush/temp', '.eslintcache', 'coverage'):
            need(not (folder / relative).exists(), 'Not fresh: ' + str(folder / relative))
        package = json.loads((folder / 'package.json').read_text())
        need(package.get('scripts', {}) == row['scripts'], 'Reviewed project script map changed')
        hooks = {'preinstall', 'install', 'postinstall', 'prepare'}
        for name in ('build', 'build:fast', 'lint', 'ts-check', 'test', 'test:cov'):
            hooks |= {'pre' + name, 'post' + name}
        need(not (hooks & package.get('scripts', {}).keys()), 'Unexpected package lifecycle hook')
    config = root / 'common/config/rush/.npmrc'
    active = [line.strip() for line in config.read_text().splitlines() if line.strip() and not line.lstrip().startswith(('#', ';'))]
    need(active == ['registry=https://registry.npmjs.org/', 'always-auth=false'], 'Unexpected npm registry or auth configuration')
    publish = root / 'common/config/rush/.npmrc-publish'
    need(not [line for line in publish.read_text().splitlines() if line.strip() and not line.lstrip().startswith(('#', ';'))], 'Unexpected publish configuration')
    # Original fixed locks; only two exact reviewed deprecation-text rows are metadata.
    lock_url_review = reviewed_lock_urls(root, plan)
    save(out / 'preflight.json', {'variant': variant, 'source': plan[variant], 'fresh_source': True,
         'cache_restored': False, 'original_build_cache_enabled': False, 'all_original_locks_verified': True,
         'dotenv_inputs_present': False, 'real_tests_enabled': False, 'credentials_passed': False,
         'live_api_cases_not_executed': LIVE_CASES, 'e2e_executed': False, 'lock_url_review': lock_url_review})


def run(command, cwd, env, out, name, timeout):
    log = out / (name + '.log')
    need(not log.exists(), 'Refusing to overwrite earlier execution evidence')
    started = time.time()
    with log.open('wb') as stream:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        reason = None
        while process.poll() is None:
            if time.time() - started > timeout:
                reason = 'timeout'
            if log.stat().st_size > MAX_LOG_BYTES:
                reason = 'log_size_limit'
            if reason:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(1)
        code = process.wait()
    result = {'command': command, 'working_directory': str(cwd), 'exit': code, 'stop_reason': reason,
              'seconds': round(time.time() - started, 3), 'log_bytes': log.stat().st_size, 'log_sha256': digest(log)}
    save(out / (name + '.execution.json'), result)
    print(json.dumps({'stage': name, 'exit': code, 'stop_reason': reason}), flush=True)
    need(reason is None, 'Stopped ' + name + ': ' + str(reason))
    need(code >= 0, 'Native process killed by signal: ' + name)
    return code


def collect_project(report_path, junit_path, project, source_root, process_exit):
    for path in (report_path, junit_path):
        need(path.exists() and path.stat().st_size <= MAX_REPORT_BYTES, 'Missing/oversize native report for ' + project['name'])
    report = json.loads(report_path.read_text())
    need(isinstance(report, dict), 'Native JSON must be an object')
    require_no_error_fields(report, 'Replay top-level report')
    files = report.get('testResults')
    need(isinstance(files, list) and bool(files), 'Replay has no native test files')
    inventory, expected_xml, file_names = [], {}, set()
    statuses = Counter()
    for file in files:
        require_no_error_fields(file, 'Replay file result')
        need(file.get('message') == '', 'Replay suite execution error or missing message')
        source = Path(file['name']).resolve()
        relative = source.relative_to(source_root.resolve()).as_posix()
        project_relative = source.relative_to((source_root / project['path']).resolve()).as_posix()
        need(project_relative not in file_names, 'Duplicate native file result')
        file_names.add(project_relative)
        assertions = file.get('assertionResults')
        need(isinstance(assertions, list), 'Missing native assertion results')
        counts, xml_rows = Counter(), []
        file_status = Counter()
        for index, case in enumerate(assertions):
            require_no_error_fields(case, 'Replay testcase')
            ancestors, title, status = case.get('ancestorTitles'), case.get('title'), case.get('status')
            need(isinstance(ancestors, list) and all(isinstance(x, str) for x in ancestors) and isinstance(title, str), 'Invalid native testcase identity')
            need(case.get('fullName') == ' '.join([*ancestors, title]), 'Native fullName differs from structured identity')
            need(status in ('passed', 'failed', 'skipped', 'pending', 'todo'), 'Unknown native testcase status')
            messages = case.get('failureMessages')
            need(isinstance(messages, list) and all(isinstance(x, str) for x in messages), 'Invalid native failure messages')
            need(bool(messages) == (status == 'failed'), 'Native failure messages disagree with status')
            key = (tuple(ancestors), title)
            counts[key] += 1
            statuses[status] += 1
            file_status[status] += 1
            item = {'project': project['name'], 'file': relative, 'fullName': case['fullName'],
                    'ancestorTitles': ancestors, 'title': title, 'occurrence': counts[key],
                    'native_index': index, 'status': status}
            if project['path'] == 'packages/runtime/js-core':
                item['live_api_case'] = any(relative.endswith(x['file']) and item['fullName'] == x['fullName'] for x in LIVE_CASES)
            inventory.append(item)
            xml_rows.append((' > '.join([*ancestors, title]), 'skipped' if status in ('skipped', 'pending', 'todo') else status))
        need(file.get('status') == ('failed' if file_status['failed'] else 'passed'), 'Native file status mismatch')
        expected_xml[project_relative] = xml_rows
    need(bool(inventory), 'Replay collected zero native test cases')
    for key, wanted in [('numTotalTests', len(inventory)), ('numPassedTests', statuses['passed']),
                        ('numFailedTests', statuses['failed']), ('numPendingTests', statuses['skipped'] + statuses['pending']),
                        ('numTodoTests', statuses['todo'])]:
        need(type(report.get(key)) is int and report[key] == wanted, 'Native replay JSON counts mismatch: ' + key)
    suite_counts = [report.get(k) for k in ('numTotalTestSuites', 'numPassedTestSuites', 'numFailedTestSuites', 'numPendingTestSuites')]
    need(all(type(v) is int and v >= 0 for v in suite_counts) and suite_counts[0] >= len(files) and suite_counts[0] == sum(suite_counts[1:]), 'Native replay suite counts inconsistent')
    need(report.get('numRuntimeErrorTestSuites', 0) == 0, 'Replay runtime-error suite')
    native_success = suite_counts[2] == 0 and statuses['failed'] == 0
    need(report.get('success') is native_success, 'Replay success flag inconsistent')
    need(process_exit in (0, 1) and (process_exit != 0 or native_success), 'Replay exit/status mismatch')
    xml = ET.parse(junit_path).getroot()
    need(xml.tag == 'testsuites' and len(xml) == len(files), 'Wrong JUnit suite inventory')
    seen, xml_total, xml_failed = set(), 0, 0
    for suite in xml:
        relative = suite.get('name')
        need(suite.tag == 'testsuite' and relative in expected_xml and relative not in seen, 'Duplicate/unknown JUnit file suite')
        seen.add(relative)
        rows, failures, skipped = [], 0, 0
        for case in suite:
            need(case.tag == 'testcase' and case.get('classname') == relative, 'Wrong JUnit testcase file')
            tags = [x.tag for x in case]
            need(not ('error' in tags or (tags.count('skipped') and tags.count('failure'))), 'JUnit error or incompatible status')
            need(all(x in ('failure', 'skipped', 'system-out', 'system-err', 'properties') for x in tags), 'Unknown JUnit testcase child')
            status = 'failed' if 'failure' in tags else 'skipped' if 'skipped' in tags else 'passed'
            failures += status == 'failed'; skipped += status == 'skipped'
            rows.append((case.get('name'), status))
        # Lists retain both reporter order and repeated names; no deduplication.
        need(rows == expected_xml[relative], 'JSON/JUnit per-case identity or status differs')
        for key, value in [('tests', len(rows)), ('failures', failures), ('errors', 0), ('skipped', skipped)]:
            need(suite.get(key) == str(value), 'JUnit suite declared counts mismatch')
        xml_total += len(rows); xml_failed += failures
    for key, value in [('tests', xml_total), ('failures', xml_failed), ('errors', 0)]:
        need(xml.get(key) == str(value), 'JUnit root declared counts mismatch')
    return {'purpose': 'separate original-script reporter replay; never added to aggregate/focused counts',
            'project': project['name'], 'process_exit': process_exit, 'native_success': report['success'],
            'native_top_level_counts': {k: v for k, v in report.items() if k.startswith('num')},
            'json_junit_case_identity_agreement': True, 'cases': inventory,
            'live_api_cases_not_executed': LIVE_CASES if project['path'] == 'packages/runtime/js-core' else []}


def installed_tool_metadata(package_file, command_file, expected_name, expected_version, bin_name, allowed_root):
    """Read installed package metadata and bind it to the actual installed command.
    No bootstrap --version call, package import, or configuration-only version claim.
    """
    metadata_path = package_file.resolve(strict=True)
    metadata_path.relative_to(allowed_root.resolve(strict=True))
    data = json.loads(metadata_path.read_text())
    need(data.get('name') == expected_name and data.get('version') == expected_version,
         'Unexpected installed package name/version: ' + expected_name)
    declared_bins = data.get('bin')
    entry = declared_bins.get(bin_name) if isinstance(declared_bins, dict) else declared_bins
    need(isinstance(entry, str) and bool(entry), 'Installed package has no expected binary: ' + expected_name)
    declared_binary = (metadata_path.parent / entry).resolve(strict=True)
    declared_binary.relative_to(metadata_path.parent)
    actual_binary = command_file.resolve(strict=True)
    need(declared_binary.is_file() and actual_binary == declared_binary,
         'Installed command does not resolve to its package-declared binary: ' + bin_name)
    return {'name': data['name'], 'version': data['version'], 'metadata_path': str(metadata_path),
            'metadata_sha256': digest(metadata_path), 'command_path': str(command_file),
            'resolved_binary': str(actual_binary), 'binary_sha256': digest(actual_binary),
            'evidence': 'installed package metadata bound to actual binary, not source config'}


def record_installed_tool_versions(root, run_root, out):
    rush_install = root / 'common/temp/install-run/@microsoft+rush@5.150.0'
    rush = installed_tool_metadata(rush_install / 'node_modules/@microsoft/rush/package.json',
                                   rush_install / 'node_modules/.bin/rush',
                                   '@microsoft/rush', '5.150.0', 'rush', rush_install)
    pnpm_local = root / 'common/temp/pnpm-local'
    need(pnpm_local.is_symlink(), 'Rush pnpm-local link is missing')
    pnpm_local.resolve(strict=True).relative_to((run_root / 'rush-global').resolve(strict=True))
    pnpm = installed_tool_metadata(pnpm_local / 'node_modules/pnpm/package.json',
                                   pnpm_local / 'node_modules/.bin/pnpm',
                                   'pnpm', '10.6.5', 'pnpm', run_root / 'rush-global')
    save(out / 'installed-tool-versions.json', {'rush': rush, 'pnpm': pnpm})
    return {'rush': rush, 'pnpm': pnpm}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--variant', choices=['baseline', 'candidate'], required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    root, out = args.source.resolve(), args.output.resolve()
    need(not out.exists(), 'Report directory must start empty')
    out.mkdir(parents=True)
    plan = json.loads((HERE / 'native-plan.json').read_text())
    preflight(root, plan, args.variant, out)
    run_root = Path(os.environ['RUNNER_TEMP']) / ('flowgram-native-' + args.variant)
    need(not run_root.exists(), 'Run home/cache already exists')
    run_root.mkdir()
    for name in ('home', 'tmp'):
        (run_root / name).mkdir()
    env = {'PATH': os.environ['PATH'], 'HOME': str(run_root / 'home'), 'TMPDIR': str(run_root / 'tmp'),
           'CI': 'true', 'GITHUB_ACTIONS': 'true', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'TERM': 'dumb', 'NO_COLOR': '1', 'FORCE_COLOR': '0', 'ENABLE_REAL_TESTS': 'false',
           'RUSH_GLOBAL_FOLDER': str(run_root / 'rush-global'), 'RUSH_PNPM_STORE_PATH': str(run_root / 'pnpm-store'),
           'npm_config_cache': str(run_root / 'npm-cache'),
           'PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD': '1', 'PUPPETEER_SKIP_DOWNLOAD': 'true'}
    # Deliberately no API_KEY/API_HOST/MODEL_NAME/token/SSH/Git credential variables.
    node_version = subprocess.check_output(['node', '--version'], env=env, text=True).strip()
    need(re.fullmatch(r'v18\.20\.[3-9]\d*', node_version) is not None, 'Expected supported Node 18.20.3+ runtime')
    save(out / 'runtime.json', {'node': node_version, 'npm': subprocess.check_output(['npm', '--version'], env=env, text=True).strip(),
                               'rush_expected': '5.150.0', 'pnpm_expected': '10.6.5', 'environment_keys': sorted(env)})
    bootstrap = ['node', 'common/scripts/install-run-rush.js']
    code = run(bootstrap + ['install'], root, env, out, 'install', 1800)
    original_source_guard(root, plan, args.variant)
    need(code == 0, 'Original frozen-lock Rush install failed')
    need((root / 'common/temp/pnpm-lock.yaml').is_file(), 'Actual installed temporary lock is missing')
    save(out / 'installed-lock.json', {'original_sha256': digest(root / 'common/config/rush/pnpm-lock.yaml'), 'installed_temp_sha256': digest(root / 'common/temp/pnpm-lock.yaml'), 'byte_identical': True})
    record_installed_tool_versions(root, run_root, out)
    need(run(['node', '-p', "JSON.stringify({vitest:require('vitest/package.json').version,react:require('react/package.json').version,typescript:require('typescript/package.json').version})"], root / 'packages/node-engine/form', env, out, 'form-tool-versions', 120) == 0, 'Cannot record native form tool versions')
    stages, cache_warnings = {}, []
    operation_state = []
    for project in plan['projects']:
        temp = root / project['path'] / '.rush/temp'
        if temp.exists():
            operation_state.extend({'path': str(p.relative_to(root)), 'sha256': digest(p)} for p in sorted(temp.rglob('*')) if p.is_file())
    save(out / 'operation-state-before-first-stage.json', {'files': operation_state, 'earlier_operations': [], 'cache_restore_configured': False})
    for stage, extra in [('build', []), ('lint', ['--verbose']), ('ts-check', []), ('test:cov', [])]:
        name = stage.replace(':', '-')
        original_source_guard(root, plan, args.variant)
        # Each original Rush operation executes only once in this never-cached source checkout.
        stages[stage] = run(bootstrap + [stage] + extra, root, env, out, name, 1800 if stage == 'build' else 1200)
        original_source_guard(root, plan, args.variant)
        summary = aggregate_summary(out / (name + '.log'), stages[stage])
        summary['incremental_skip_or_cache_warning'] = bool(re.search(r'FROM CACHE|\[\s*SKIPPED\b|restored from cache|skipping[^\n]*unchanged|projects?[^\n]* (?:was|were) skipped', (out / (name + '.log')).read_text(errors='replace'), re.I))
        if summary['incremental_skip_or_cache_warning']:
            cache_warnings.append(stage)
        summary['fresh_operation_history'] = 'This stage has not run earlier in this fresh job; cache restore is not configured.'
        save(out / (name + '.summary.json'), summary)
        save(out / 'stages.json', stages)
    replays = []
    for index, project in enumerate(plan['vitest_projects']):
        name = 'replay-' + str(index + 1).zfill(2)
        folder = root / project['path']
        report = out / (name + '.json')
        junit = out / (name + '.xml')
        need(not report.exists() and not junit.exists(), 'Stale native report output')
        # All 21 reviewed scripts are exactly `vitest run --coverage`.
        command = ['npm', 'run', 'test:cov', '--', '--reporter=default', '--reporter=json', '--reporter=junit',
                   '--outputFile.json=' + str(report), '--outputFile.junit=' + str(junit)]
        code = run(command, folder, env, out, name, 600)
        original_source_guard(root, plan, args.variant)
        try:
            item = collect_project(report, junit, project, root, code)
            need(junit.is_file() and junit.stat().st_size <= MAX_REPORT_BYTES, 'Missing/oversize JUnit')
        except Exception as error:
            item = {'project': project['name'], 'process_exit': code, 'report_error': str(error)}
        replays.append(item)
        save(out / 'reporter-replays.json', replays)
    injected = args.variant == 'baseline'
    if injected:
        target = root / SOURCE_TEST
        need(not target.exists(), 'Baseline already contains regression test')
        shutil.copyfile(HERE / 'use-watch.test.tsx', target)
        need(digest(target) == plan['focused_test_sha256'], 'Injected test bytes mismatch')
    original_source_guard(root, plan, args.variant, injected)
    folder = root / 'packages/node-engine/form'
    command = ['npm', 'test', '--', '__tests__/use-watch.test.tsx', '--reporter=default', '--reporter=json', '--reporter=junit',
               '--outputFile.json=' + str(out / 'focused.json'), '--outputFile.junit=' + str(out / 'focused.xml')]
    code = run(command, folder, env, out, 'focused', 600)
    original_source_guard(root, plan, args.variant, injected)
    try:
        result = focused(out / 'focused.json', out / 'focused.xml', out / 'focused.log', code,
                         'baseline-negative' if injected else 'candidate-positive', HERE / 'expected.json', root / SOURCE_TEST)
    except Exception as error:
        result = {'classification': 'blocked_or_unexpected_failure', 'reason': str(error), 'process_exit': code}
    save(out / 'focused-classification.json', result)
    files = [{'path': str(p.relative_to(out)), 'bytes': p.stat().st_size, 'sha256': digest(p)}
             for p in sorted(out.iterdir()) if p.is_file()]
    save(out / 'manifest.json', {'source': plan[args.variant], 'variant': args.variant,
         'harness_commit': os.environ['GITHUB_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
         'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'plan_sha256': digest(HERE / 'native-plan.json'),
         'files': files, 'e2e_executed': False, 'live_api_executed': False,
         'complete_repository_ci_claim': False, 'all_native_stages_exit_zero': all(x == 0 for x in stages.values()), 'incremental_skip_or_cache_warnings': cache_warnings})
    need(result['classification'] in ('expected_semantic_negative', 'focused_pass'), 'Focused native evidence does not pass semantic gate')
    need(not cache_warnings, 'Unexpected Rush incremental skip/cache evidence: ' + repr(cache_warnings))
    need(all(x == 0 for x in stages.values()), 'One or more original native stages failed; inspect both lanes')
    need(all(x.get('process_exit') == 0 and not x.get('report_error') for x in replays), 'One or more reporter replays failed')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('NATIVE_GATE_BLOCKED: ' + str(error), file=sys.stderr)
        sys.exit(1)
