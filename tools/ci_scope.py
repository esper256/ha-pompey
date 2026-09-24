"""Skip expensive checks only for known documentation-only changes; fail closed."""
import os
from pathlib import PurePosixPath
import subprocess


def needs_code_checks(paths):
    def documentation(path):
        parts = PurePosixPath(path).parts
        return path.endswith('.md') and (
            len(parts) == 1 or parts[0] == 'docs' or path in {'pompey/DOCS.md', 'pompey/CHANGELOG.md'}
        )
    return any(not documentation(path) for path in paths)


def main():
    base = os.environ.get('BASE', '')
    event = os.environ['EVENT']
    # Schedules/manual runs always exercise the full matrix. Unknown bases do too.
    code = True
    if event in {'pull_request', 'push'} and base and set(base) != {'0'}:
        revision = f"{base}...{os.environ['HEAD']}" if event == 'pull_request' else f"{base}..{os.environ['HEAD']}"
        result = subprocess.run(['git', 'diff', '--no-renames', '--name-only', '-z', revision], capture_output=True, check=True)
        paths = [p for p in result.stdout.decode().split('\0') if p]
        code = needs_code_checks(paths)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'code={str(code).lower()}\n')


if __name__ == '__main__':
    main()
