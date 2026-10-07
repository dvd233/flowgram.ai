"""Read verified source ZIPs without executing repository code or extracting files."""
import json
from pathlib import Path
import zipfile


def jsonc(text):
    out, i, quoted, escaped = [], 0, False, False
    while i < len(text):
        c = text[i]
        if quoted:
            out.append(c)
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
            i += 1
        elif c == '"':
            quoted = True
            out.append(c)
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('Unterminated JSONC comment')
            out.append(' ')
            i = end + 2
        else:
            out.append(c)
            i += 1
    clean = ''.join(out)
    out, i, quoted, escaped = [], 0, False, False
    while i < len(clean):
        c = clean[i]
        if quoted:
            out.append(c)
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
            out.append(c)
        elif c == ',':
            next_char = i + 1
            while next_char < len(clean) and clean[next_char].isspace():
                next_char += 1
            if next_char == len(clean) or clean[next_char] not in '}]':
                out.append(c)
        else:
            out.append(c)
        i += 1
    return json.loads(''.join(out))


def make_mapping(archive):
    with zipfile.ZipFile(archive) as z:
        read = lambda path: z.read('source/' + path).decode()
        rush = jsonc(read('rush.json'))
        commands = jsonc(read('common/config/rush/command-line.json'))
        project_rows = []
        for project in rush['projects']:
            file = project['projectFolder'] + '/package.json'
            package = json.loads(read(file))
            project_rows.append({'path': file, 'project': project['packageName'],
                                 'scripts': package.get('scripts', {}),
                                 'script_counts_are_not_test_counts': True})
        counts = {}
        for command in ('build', 'lint', 'ts-check', 'test:cov'):
            active = [row for row in project_rows if command in row['scripts']]
            noop = [row['project'] for row in active if row['scripts'][command] in ('exit', 'exit 0')]
            counts[command] = {'registered': len(project_rows), 'script_defined': len(active),
                               'no_op_projects': noop,
                               'missing_script_projects': [row['project'] for row in project_rows
                                                           if command not in row['scripts']]}
        return {'rush_version': rush['rushVersion'], 'pnpm_version': rush['pnpmVersion'],
                'node_range': rush['nodeSupportedVersionRange'], 'event_hooks': rush['eventHooks'],
                'commands': [c for c in commands['commands']
                             if c['name'] in ('test', 'test:cov', 'lint', 'ts-check')],
                'projects': project_rows, 'command_execution_map': counts}


if __name__ == '__main__':
    root = Path(__file__).parent
    result = make_mapping(root / 'artifacts/candidate/scope-candidate.zip')
    (root / 'native-project-command-map.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'projects'}, indent=2))
