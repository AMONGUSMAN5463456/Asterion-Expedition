#!/usr/bin/env python3
"""Build and verify a clean, reproducible source ZIP; never include local saves."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from asterion import __version__


def sources():
    files = []
    for folder in ('asterion', 'assets', 'docs', 'tests', 'tools'):
        files.extend(path for path in (ROOT / folder).rglob('*')
                     if path.is_file() and not path.is_symlink()
                     and not any(part.startswith('.') or part == '__pycache__'
                                 for part in path.relative_to(ROOT).parts)
                     and path.suffix not in ('.pyc', '.pyo', '.log'))
    for name in ('LICENSE', 'README.md', 'START_HERE.txt', 'THIRD_PARTY_NOTICES.md',
                 'Start-Mac.command', 'Start-Windows.bat', 'Start-Linux.sh',
                 'bootstrap.py', 'main.py', 'requirements.txt'):
        files.append(ROOT / name)
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    prefix = f'asterion-expedition-v{__version__}'
    archive = args.output / f'{prefix}.zip'
    entries = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in sources()}
    manifest = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                       for name, data in entries.items())
    (ROOT / 'MANIFEST.sha256').write_text(manifest, encoding='utf-8')
    entries['MANIFEST.sha256'] = manifest.encode('utf-8')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, data in sorted(entries.items()):
            entry = zipfile.ZipInfo(f'{prefix}/{name}', date_time=(2026, 9, 17, 0, 0, 0))
            executable = name in ('Start-Mac.command', 'Start-Linux.sh', 'bootstrap.py', 'main.py')
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(entry, data)
    with zipfile.ZipFile(archive) as verification:
        assert verification.testzip() is None, 'Archive CRC failure'
        assert len(verification.namelist()) == len(entries)
        for line in manifest.splitlines():
            digest, name = line.split('  ', 1)
            assert hashlib.sha256(verification.read(f'{prefix}/{name}')).hexdigest() == digest, name
        assert all(name.startswith(prefix + '/') and '/.venv/' not in name
                   and '__pycache__' not in name for name in verification.namelist())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n')
    print(json.dumps(dict(archive=str(archive.resolve()), version=__version__,
        bytes=archive.stat().st_size, files=len(entries), sha256=digest,
        crc_and_manifest='passed'), indent=2))


if __name__ == '__main__':
    main()
