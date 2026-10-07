#!/usr/bin/env python3
"""Fail-closed classifier for unmodified Vitest 3.2.x JSON/JUnit reporters.

These reports are evidence, not a substitute runner. Unknown report formatting
blocks classification; a nonzero exit alone never establishes the regression.
"""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
FORBIDDEN_RUNTIME = re.compile(
    r'Unhandled Errors|Unhandled Rejection|Uncaught Exception|Cannot find module|'
    r'Failed to resolve import|SyntaxError:|ReferenceError:|TypeError:|Timed out|'
    r'WARNING: Some tests are still running', re.I)
ERROR_HEADER = re.compile(r'(?m)^\s*(?:Caused by:\s*)?([A-Za-z_$][\w$]*Error|Error):')
SOURCE_SUFFIX = 'packages/node-engine/form/__tests__/use-watch.test.tsx'
JUNIT_FILE = '__tests__/use-watch.test.tsx'


def fail(message):
    raise ValueError(message)


def require_no_error_fields(obj, scope):
    for key in ('errors', 'error', 'unhandledErrors', 'testExecError', 'testExecutionError',
                'hookErrors', 'runtimeErrors', 'suiteErrors'):
        if obj.get(key):
            fail(f'{scope} contains {key}')
    if obj.get('wasInterrupted') or obj.get('interrupted'):
        fail(scope + ' was interrupted')


def integer(obj, key, wanted):
    if type(obj.get(key)) is not int or obj[key] != wanted:
        fail('Inconsistent native count: ' + key)


def assertion_values(message):
    clean = ANSI.sub('', message)
    lines = clean.splitlines()
    if not lines:
        fail('Missing native assertion message')
    match = re.fullmatch(r'AssertionError:\s*expected (.+?) to be (.+?)(?:\s+//[^\n]*)?', lines[0])
    if not match or ERROR_HEADER.findall(clean) != ['AssertionError']:
        fail('Failure contains another error or is not exactly the expected toBe AssertionError')
    try:
        received, expected = (ast.literal_eval(part.strip()) for part in match.groups())
    except (ValueError, SyntaxError) as error:
        raise ValueError('Unknown native assertion value rendering') from error
    if not isinstance(received, str) or not isinstance(expected, str):
        fail('Regression must fail on rendered string text')
    return received, expected


def assert_failure(message, case, source_file):
    clean = ANSI.sub('', message)
    if FORBIDDEN_RUNTIME.search(clean):
        fail('Runtime/import/timeout failure is not a stale-text regression')
    if assertion_values(clean) != (case['receivedText'], case['expectedText']):
        fail('Wrong received/expected stale-text semantics: ' + case['fullName'])
    # Match the complete absolute source pathname, including a path boundary.
    # A same-basename or suffix-only frame in another directory is insufficient.
    frames = re.findall(r'(?m)^\s*at\s+(?:.*?\()?((?:file://)?/[^\n()]+?):(\d+):(\d+)\)?\s*$', clean)
    target_frames = [(path.removeprefix('file://'), int(line), int(column))
                     for path, line, column in frames if path.endswith('/use-watch.test.tsx')]
    wanted = str(source_file.resolve())
    if not target_frames or any(path != wanted or line != case['assertionLine'] or column < 1
                                for path, line, column in target_frames):
        fail('Assertion stack does not identify the complete frozen test path and line')
    # JSON failures contain a stack, not a second nested error or arbitrary prose.
    if any(line.strip() and not re.match(r'^\s*at\s+', line) for line in clean.splitlines()[1:]):
        fail('Unknown assertion stack format; review actual native evidence first')


def junit_counts(path, expected=None, baseline=None):
    root = ET.parse(path).getroot()
    if root.tag != 'testsuites' or len(root.findall('testsuite')) != 1:
        fail('JUnit must contain exactly one file suite')
    suite = root.find('testsuite')
    if any(n.tag != 'testsuite' for n in root) or suite.get('name') != JUNIT_FILE:
        fail('JUnit file identity is not the focused repository test')
    cases = suite.findall('testcase')
    if any(n.tag != 'testcase' for n in suite):
        fail('JUnit suite contains a non-testcase error or unexpected report node')
    counts = {'total': len(cases), 'failed': 0, 'errors': 0, 'skipped': 0}
    identities = set()
    for node in cases:
        name = node.get('name')
        if node.get('classname') != JUNIT_FILE or name in identities:
            fail('Duplicate JUnit testcase or wrong file classname')
        identities.add(name)
        status_nodes = [n for n in node if n.tag in ('failure', 'error', 'skipped')]
        if len(status_nodes) > 1:
            fail('JUnit has duplicate or incompatible statuses on one case')
        if any(n.tag not in ('failure', 'error', 'skipped', 'system-out', 'system-err', 'properties') for n in node):
            fail('Unexpected JUnit testcase child')
        if expected is not None:
            lookup = {'useWatch > ' + k.removeprefix('useWatch '): row for k, row in expected.items()}
            if name not in lookup:
                fail('Unknown JUnit testcase identity')
            case = lookup[name]
            wanted = case['baselineStatus'] if baseline else case['candidateStatus']
            actual = 'failed' if node.find('failure') is not None else 'passed'
            if wanted != actual:
                fail('JUnit per-case status disagrees with JSON/expected status')
            failure = node.find('failure')
            if failure is not None:
                if failure.get('type') != 'AssertionError':
                    fail('JUnit failure is not an assertion failure')
                message = failure.get('message')
                if not isinstance(message, str) or assertion_values('AssertionError: ' + message) != (case['receivedText'], case['expectedText']):
                    fail('JUnit has wrong stale-text assertion semantics')
        for tag, key in (('failure', 'failed'), ('error', 'errors'), ('skipped', 'skipped')):
            counts[key] += bool(node.findall(tag))
    counts['passed'] = counts['total'] - counts['failed'] - counts['errors'] - counts['skipped']
    if expected is not None and len(identities) != len(expected):
        fail('JUnit testcase inventory is incomplete')
    for elem in (root, suite):
        for key, value in (('tests', counts['total']), ('failures', counts['failed']), ('errors', counts['errors'])):
            if elem.get(key) != str(value):
                fail('JUnit declared counts disagree with actual cases')
    if suite.get('skipped') != str(counts['skipped']):
        fail('JUnit skipped count disagrees')
    return counts


def focused(json_path, junit_path, log_path, exit_code, lane, expected_path, source_file):
    if lane not in ('baseline-negative', 'candidate-positive'):
        fail('Unknown focused lane')
    if not source_file.resolve().as_posix().endswith('/' + SOURCE_SUFFIX):
        fail('Focused source path is outside the frozen repository location')
    expected_cases = json.loads(expected_path.read_text())['cases']
    expected = {row['fullName']: row for row in expected_cases}
    if len(expected) != 19 or len(expected_cases) != 19:
        fail('Expected regression inventory must contain 19 unique cases')
    report = json.loads(json_path.read_text())
    require_no_error_fields(report, 'Top-level native report')
    if not isinstance(report.get('testResults'), list) or len(report['testResults']) != 1:
        fail('Collection/import/runner failure or extra test file')
    file_result = report['testResults'][0]
    require_no_error_fields(file_result, 'Native file result')
    if file_result.get('message') != '':
        fail('Native suite contains an execution error message or is missing its message field')
    if Path(file_result.get('name', '')).resolve() != source_file.resolve():
        fail('Native report does not identify the complete frozen regression file')
    assertions = file_result.get('assertionResults')
    if not isinstance(assertions, list):
        fail('Native assertion collection is missing')
    found = {}
    for assertion in assertions:
        require_no_error_fields(assertion, 'Native testcase')
        ancestors, title = assertion.get('ancestorTitles'), assertion.get('title')
        if ancestors != ['useWatch'] or not isinstance(title, str):
            fail('Missing native testcase identity')
        name = ' '.join([*ancestors, title])
        if assertion.get('fullName') != name or name in found or name not in expected:
            fail('Duplicate, unknown, or inconsistent native testcase identity')
        found[name] = assertion
    if set(found) != set(expected):
        fail('Collection incomplete; nonzero exit is not a reproduced regression')
    native_counts = Counter(a.get('status') for a in assertions)
    if set(native_counts) - {'passed', 'failed'}:
        fail('Skipped/todo/pending/unknown regression result')
    baseline = lane == 'baseline-negative'
    expected_failed, expected_passed = (14, 5) if baseline else (0, 19)
    if exit_code != (1 if baseline else 0):
        fail('Unexpected native process exit, crash, timeout, or setup failure')
    if file_result.get('status') != ('failed' if baseline else 'passed'):
        fail('Native file-suite status disagrees with the expected lane')
    if (native_counts['passed'], native_counts['failed']) != (expected_passed, expected_failed):
        fail('Native regression status totals do not match the prediction')
    for key, value in [('numTotalTests', 19), ('numPassedTests', expected_passed),
                       ('numFailedTests', expected_failed), ('numPendingTests', 0),
                       ('numTodoTests', 0), ('numPendingTestSuites', 0),
                       ('numTotalTestSuites', 2), ('numFailedTestSuites', 2 if baseline else 0),
                       ('numPassedTestSuites', 0 if baseline else 2)]:
        integer(report, key, value)
    if report.get('numRuntimeErrorTestSuites', 0) != 0:
        fail('Native report contains runtime-error suites')
    snapshot = report.get('snapshot', {})
    if snapshot.get('failure') or snapshot.get('unmatched', 0) or snapshot.get('uncheckedCount', 0):
        fail('Unexpected snapshot failure/change in focused regression run')
    if report.get('success') is not (not baseline):
        fail('Native success flag disagrees with the expected lane')
    log = ANSI.sub('', log_path.read_text(errors='replace'))
    if FORBIDDEN_RUNTIME.search(log):
        fail('Runner/import/timeout/unhandled error in native output')
    for error_type in ERROR_HEADER.findall(log):
        if error_type != 'AssertionError':
            fail('Non-assertion error in native output: ' + error_type)
    for name, assertion in found.items():
        case = expected[name]
        want = case['baselineStatus'] if baseline else case['candidateStatus']
        if assertion['status'] != want:
            fail('Unexpected individual testcase status: ' + name)
        messages = assertion.get('failureMessages')
        if not isinstance(messages, list):
            fail('Missing native failureMessages array')
        if want == 'passed':
            if messages:
                fail('Passing testcase includes failures')
        else:
            if len(messages) != 1 or not isinstance(messages[0], str):
                fail('Expected exactly one stale-text assertion failure')
            assert_failure(messages[0], case, source_file)
    xml = junit_counts(junit_path, expected, baseline)
    if xml != {'total': 19, 'failed': expected_failed, 'errors': 0, 'skipped': 0, 'passed': expected_passed}:
        fail('Native JSON and JUnit counts disagree')
    return {'lane': lane, 'classification': 'expected_semantic_negative' if baseline else 'focused_pass',
            'process_exit': exit_code, 'native_json_counts': dict(native_counts), 'native_junit_counts': xml,
            'cases': [{'identity': name, 'native_full_name': found[name]['fullName'],
                       'status': found[name]['status']} for name in expected]}


def aggregate_summary(log_path, process_exit):
    lines = ANSI.sub('', log_path.read_text(errors='replace')).splitlines()
    summaries = []
    for number, line in enumerate(lines, 1):
        if re.search(r'\b(?:Test Files|Tests)\s+\d+\s+(?:passed|failed|skipped)', line):
            summaries.append({'line_number': number, 'native_text': line,
                              'counts': [{'status': status, 'count': int(count)}
                                         for count, status in re.findall(r'(\d+) (passed|failed|skipped|todo)', line)]})
    return {'process_exit': process_exit, 'source': 'native_default_reporter_stdout',
            'summary_lines_not_aggregated': summaries, 'grand_total': None,
            'note': 'Rush can duplicate native per-project summaries; no inferred global total.'}


def compare_stages(baseline, candidate):
    comparisons = []
    for name in ('build', 'lint', 'ts-check', 'test:cov'):
        before, after = baseline.get(name), candidate.get(name)
        if before is None or after is None:
            classification = 'missing_or_blocked_stage'
        elif before == 0 and after == 0:
            classification = 'both_passed'
        elif before == 0:
            classification = 'candidate_only_failure'
        elif after == 0:
            classification = 'baseline_observed_failure_candidate_passed'
        else:
            classification = 'failed_in_both_not_attributed'
        comparisons.append({'stage': name, 'baseline_exit': before, 'candidate_exit': after,
                            'classification': classification})
    return {'comparisons': comparisons,
            'all_native_stages_passed': all(x['classification'] == 'both_passed' for x in comparisons),
            'e2e_executed': False, 'complete_repository_ci_claim': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', type=Path, required=True)
    parser.add_argument('--junit', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--exit-code', type=int, required=True)
    parser.add_argument('--lane', required=True)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--source-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = focused(args.json, args.junit, args.log, args.exit_code, args.lane, args.expected, args.source_file)
        print(json.dumps(result, indent=2))
    except Exception as error:
        print(json.dumps({'classification': 'blocked_or_unexpected_failure', 'reason': str(error), 'process_exit': args.exit_code}))
        sys.exit(1)


if __name__ == '__main__':
    main()
