"""Rebuild the exact release archive from pinned upstream binaries and local text."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = {
    'BepInEx.zip': (
        'https://builds.bepinex.dev/projects/bepinex_be/788/BepInEx-Unity.IL2CPP-win-x64-6.0.0-be.788+5b766a3.zip',
        'f4cc496bd098a0df4164b81e3737297707f13a47c2478dba2f60eefab784817a',
    ),
    'LanguageMod.zip': (
        'https://github.com/Nameland/computer-tycoon-language-pack/releases/download/v0.1.1/ComputerTycoon.LanguageMod-0.1.1.zip',
        '91ec60c960e75d2ed8c2547f843cc64511f0242dedf15dfad75cd97704a3ad37',
    ),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def download(item):
    name, (url, expected) = item
    cache = ROOT / '.build-cache'
    cache.mkdir(exist_ok=True)
    target = cache / name
    if not target.exists() or sha(target.read_bytes()) != expected:
        request = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 ComputerTycoon-RU-package',
            'Referer': 'https://builds.bepinex.dev/' if 'bepinex.dev' in url else 'https://github.com/',
        })
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read()
        assert sha(data) == expected, f'Unexpected upstream checksum: {name}'
        target.write_bytes(data)
    print('Verified upstream: ' + name, flush=True)
    return name, target


def index(archive):
    return {info.filename.replace('\\', '/').lstrip('./'): info.filename
            for info in archive.infolist() if not info.is_dir()}


def main():
    release = json.loads((ROOT / 'release.json').read_text())
    manifest = json.loads((ROOT / 'package_manifest.json').read_text())
    with ThreadPoolExecutor(max_workers=2) as pool:
        archives = dict(pool.map(download, UPSTREAM.items()))
    payload = {}
    with zipfile.ZipFile(archives['BepInEx.zip']) as loader, zipfile.ZipFile(archives['LanguageMod.zip']) as mod:
        loader_names, mod_names = index(loader), index(mod)
        for target, record in manifest['runtime_files'].items():
            relative = target.removeprefix('Files_to_game/')
            basename = Path(relative).name
            if relative.endswith('/Languages/ru.json'):
                data = (ROOT / 'languages/ru.json').read_bytes()
            elif relative.startswith('BepInEx/config/'):
                data = (ROOT / 'config' / basename).read_bytes()
            elif relative.startswith('BepInEx/plugins/'):
                candidates = [key for normalized, key in mod_names.items() if normalized.split('/')[-1] == basename]
                matches = [mod.read(key) for key in candidates if sha(mod.read(key)) == record['sha256']]
                assert len(matches) == 1, f'Expected one matching plugin file: {basename}'
                data = matches[0]
            else:
                key = relative if relative in loader_names else relative.lstrip('./')
                data = loader.read(loader_names[key])
            assert len(data) == record['size'] and sha(data) == record['sha256'], target
            payload[target] = data
    payload['INSTALL_RU.txt'] = (ROOT / 'INSTALL_RU.txt').read_bytes()
    payload['SOURCES.md'] = (ROOT / 'SOURCES.md').read_bytes()
    for path in sorted((ROOT / 'Licenses').iterdir()):
        if path.is_file():
            payload['Licenses/' + path.name] = path.read_bytes()
    assert len(payload) == manifest['archive_entries']
    target = ROOT / release['asset']
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 31, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    assert target.stat().st_size == release['size_bytes']
    assert sha(target.read_bytes()) == release['sha256'], 'Rebuilt archive differs from the reviewed archive'
    print('Rebuilt exact package: ' + target.name)


if __name__ == '__main__':
    main()
