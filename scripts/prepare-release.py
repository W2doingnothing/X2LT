# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Prepare allowlisted release assets; never archive a working directory wholesale."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from local_video import __version__


def verify_dependencies():
    for line in (ROOT / 'requirements-local-video-lock.txt').read_text().splitlines():
        if line and not line.startswith('#'):
            name, expected = line.split('==')
            if importlib.metadata.version(name) != expected:
                raise RuntimeError(f'Dependency differs from lock: {name}')


def require_clean():
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain']):
        raise RuntimeError('Commit all release source changes before building/packaging')


def artifact_hashes(built):
    files = [built / '本地视频下载器.exe'] + sorted((built / 'local-video-runtime').rglob('*'))
    result = {}
    for path in files:
        if path.is_symlink():
            raise RuntimeError('Release artifact is a symlink')
        if path.is_file():
            result[path.relative_to(built).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def validate_build_info(info, commit, version, hashes):
    if not info.get('clean') or info.get('commit') != commit or info.get('version') != version or info.get('artifacts') != hashes:
        raise RuntimeError('Build is dirty, stale or changed; rebuild from the current committed source')


def record_build(built):
    require_clean()
    verify_dependencies()
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip()
    info = {'commit': commit, 'version': __version__, 'clean': True, 'artifacts': artifact_hashes(built)}
    (built / 'build-info.json').write_text(json.dumps(info, indent=2), encoding='utf8')


def zip_tree(source, target, prefix=''):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise ValueError('Release must not contain symlinks')
            if path.is_file():
                archive.write(path, prefix + path.relative_to(source).as_posix())


def zip_sources(cache, target, sources):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ['SOURCES.json'] + [entry['file'] for entry in sources]:
            if Path(name).name != name or (cache / name).is_symlink():
                raise ValueError('Unsafe source artifact')
            archive.write(cache / name, name)


def fetch_sources():
    """Retrieve exact locked sdists from PyPI, verifying its published SHA256."""
    cache = ROOT / '.release-work' / 'third-party-sources'
    cache.mkdir(parents=True, exist_ok=True)
    sources = []
    for line in (ROOT / 'requirements-local-video-lock.txt').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        name, version = line.split('==')
        with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
            data = json.load(response)
        source = next(item for item in data['urls'] if item['packagetype'] == 'sdist')
        filename = source['filename']
        if Path(filename).name != filename:
            raise ValueError('Unsafe source filename')
        target = cache / filename
        expected = source['digests']['sha256']
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            with urllib.request.urlopen(source['url'], timeout=60) as response:
                body = response.read()
            if hashlib.sha256(body).hexdigest() != expected:
                raise ValueError('Source hash mismatch')
            target.write_bytes(body)
        sources.append({'name': name, 'version': version, 'file': filename, 'url': source['url'], 'sha256': expected})
        print(name, version, 'verified', flush=True)
    (cache / 'SOURCES.json').write_text(json.dumps(sources, indent=2), encoding='utf8')
    out = ROOT / '.release'
    out.mkdir(exist_ok=True)
    zip_sources(cache, out / f'X2LT-{__version__}-third-party-sources.zip', sources)


def package():
    require_clean()
    verify_dependencies()
    out = ROOT / '.release'
    out.mkdir(exist_ok=True)
    built = ROOT / '.dist-local' / '本地视频下载器'
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip()
    info = json.loads((built / 'build-info.json').read_text(encoding='utf8'))
    validate_build_info(info, commit, __version__, artifact_hashes(built))
    # mkdtemp creates a new stage; no recursive deletion of user-selected paths.
    stage_root = ROOT / '.release-work'
    stage_root.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='windows-', dir=stage_root))
    for name in ('本地视频下载器.exe', 'local-video-runtime'):
        source = built / name
        if source.is_dir():
            shutil.copytree(source, stage / name)
        else:
            shutil.copy2(source, stage / name)
    for name in ('start-local-video.cmd', 'README.md', 'CHANGELOG.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, stage / name)
    shutil.copytree(ROOT / 'third-party-licenses', stage / 'third-party-licenses')
    shutil.copy2(built / 'build-info.json', stage / 'build-info.json')
    zip_tree(stage, out / f'X2LT-{__version__}-windows-x64.zip', f'X2LT-{__version__}/')
    subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=zip', f'--prefix=X2LT-{__version__}/',
                    f'--output={out / ("X2LT-" + __version__ + "-source.zip")}', 'HEAD'], check=True)
    sums = []
    for suffix in ('windows-x64.zip', 'source.zip', 'third-party-sources.zip', 'web-sources.zip'):
        path = out / f'X2LT-{__version__}-{suffix}'
        sums.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}')
    (out / 'SHA256SUMS.txt').write_text('\n'.join(sums) + '\n', encoding='ascii')
    print('stage:', stage)
    print('\n'.join(sums))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch-third-party', action='store_true')
    parser.add_argument('--record-build', type=Path)
    args = parser.parse_args()
    if args.record_build:
        record_build(args.record_build)
    elif args.fetch_third_party:
        fetch_sources()
    else:
        package()
