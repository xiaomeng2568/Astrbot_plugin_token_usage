"""Reproducible plugin archive and offline source-integrity verification."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = 'astrbot_plugin_token_usage'
NAME = PLUGIN + '-v0.1.1.zip'


def sources():
    result = {}
    for path in ROOT.rglob('*'):
        if not path.is_file() or any(part in {'.git', '__pycache__', 'release-assets'} for part in path.parts):
            continue
        if path.name == 'MANIFEST.sha256':
            continue
        if path.suffix in {'.py', '.json', '.md', '.yaml', '.txt'} or path.name in {'LICENSE', '.gitignore'}:
            result[path.relative_to(ROOT).as_posix()] = path.read_bytes().replace(b'\r\n', b'\n')
    return result


def main(check=False):
    archive = ROOT / 'release-assets' / NAME
    files = sources()
    manifest = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                       for name, data in sorted(files.items())).encode()
    files['MANIFEST.sha256'] = manifest
    if not check:
        archive.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(PLUGIN + '/' + name, (2026, 10, 26, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                z.writestr(info, data)
        (ROOT / 'MANIFEST.sha256').write_bytes(manifest)
        Path(str(archive) + '.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + NAME + '\n', encoding='utf-8')
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == {PLUGIN + '/' + n for n in files}
        for name, data in files.items():
            assert z.read(PLUGIN + '/' + name) == data, name
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == Path(str(archive) + '.sha256').read_text().split()[0]
    schema = json.loads((ROOT / '_conf_schema.json').read_text(encoding='utf-8'))
    assert 'oauth_sol_standard' not in schema
    assert 'version: v0.1.1' in (ROOT / 'metadata.yaml').read_text(encoding='utf-8')
    print('v0.1.1 source, manifest and archive verified')


if __name__ == '__main__':
    main('--check' in sys.argv)
