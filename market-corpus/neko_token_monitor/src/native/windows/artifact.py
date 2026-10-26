"""Bind the shipped Windows runtime to its native sources; reject stale binaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ['native/windows/CatFood.csproj', 'native/windows/Program.cs', 'native/windows/WindowOwnership.cs']
REQUIRED = ['CatFood.exe', 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(directory):
    value = json.loads((directory / 'build-info.json').read_text(encoding='utf-8-sig'))
    assert value['platform'] == 'windows-x64'
    assert value['sources'] == {name: digest(ROOT / name) for name in SOURCES}, 'Windows native sources changed without rebuilding the shipped program'
    for name, expected in value['files'].items():
        path = Path(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert digest(directory / path) == expected, 'Windows runtime file mismatch: ' + name
    assert all(name in value['files'] for name in REQUIRED)
    assert any(name.endswith('WebView2Loader.dll') for name in value['files']), 'WebView2 native loader missing'
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['stamp', 'check'])
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.action == 'stamp':
        files = {p.relative_to(args.directory).as_posix(): digest(p) for p in sorted(args.directory.rglob('*'))
                 if p.is_file() and (p.suffix.lower() in ('.exe', '.dll', '.config'))}
        value = {'platform': 'windows-x64', 'source_commit': os.environ.get('GITHUB_SHA'),
                 'sources': {name: digest(ROOT / name) for name in SOURCES}, 'files': files}
        (args.directory / 'build-info.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    value = check(args.directory)
    print(json.dumps({'platform': value['platform'], 'files_verified': len(value['files']), 'source_commit': value['source_commit']}))


if __name__ == '__main__':
    main()
