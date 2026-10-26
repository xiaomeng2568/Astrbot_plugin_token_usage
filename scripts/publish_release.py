"""Publish only from the tested GitHub Actions main checkout."""
import json
import os
import subprocess
from pathlib import Path
from build_release import main as verify, NAME

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise SystemExit('Publication requires GitHub Actions on main')
    verify(check=True)
    view = subprocess.run(['gh', 'release', 'view', 'v0.1.1', '--json', 'url'], cwd=ROOT,
                          capture_output=True, text=True)
    if view.returncode == 0:
        print('Already published: ' + json.loads(view.stdout)['url'])
        return
    subprocess.run(['gh', 'release', 'create', 'v0.1.1',
        str(ROOT/'release-assets'/NAME), str(ROOT/'release-assets'/(NAME+'.sha256')),
        '--title', 'v0.1.1 · DeepSeek 分时计价', '--notes-file', str(ROOT/'RELEASE_NOTES.md'),
        '--target', os.environ['GITHUB_SHA']], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
