"""Validate the committed package and publish it as a GitHub Release.

Uses the repository-scoped GITHUB_TOKEN only inside GitHub Actions.
Existing published assets are never overwritten or deleted.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'lucinis/Computer-Tycoon-ru-v0.9.9.16'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check():
    release = json.loads((ROOT / 'release.json').read_text())
    manifest = json.loads((ROOT / 'package_manifest.json').read_text())
    asset_path = PurePosixPath(release['asset'])
    assert not asset_path.is_absolute() and '..' not in asset_path.parts
    asset = ROOT / asset_path
    assert asset.stat().st_size == release['size_bytes']
    assert sha(asset.read_bytes()) == release['sha256']
    with zipfile.ZipFile(asset) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert len(names) == len(set(names)) == manifest['archive_entries']
        assert all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts for n in names)
        assert z.read('INSTALL_RU.txt') == (ROOT / 'INSTALL_RU.txt').read_bytes()
        assert sha(z.read('INSTALL_RU.txt')) == manifest['instruction_sha256']
        actual_runtime = {n for n in names if n.startswith('Files_to_game/')}
        assert actual_runtime == set(manifest['runtime_files'])
        for name, record in manifest['runtime_files'].items():
            data = z.read(name)
            assert len(data) == record['size'] and sha(data) == record['sha256'], name
        ru = z.read('Files_to_game/BepInEx/plugins/ComputerTycoon.LanguageMod/Languages/ru.json')
        assert ru == (ROOT / 'languages/ru.json').read_bytes()
        json.loads(ru)
        cfg = configparser.ConfigParser()
        cfg.read_string(z.read('Files_to_game/BepInEx/config/BepInEx.cfg').decode())
        assert cfg.getboolean('Logging.Console', 'Enabled') is False
        cfg.read_string(z.read('Files_to_game/BepInEx/config/computer.tycoon.languagepacks.cfg').decode())
        assert cfg.get('General', 'Language') == 'ru'
    checksum = ROOT / 'downloads/SHA256SUMS.txt'
    assert checksum.read_text() == f"{release['sha256']}  {asset.name}\n"
    print(f"Verified: {asset.name}, {asset.stat().st_size} bytes, SHA256 {release['sha256']}")
    return release, [asset, checksum]


def request(method, url, *, payload=None, body=None, content_type='application/json', missing_ok=False):
    parsed = urllib.parse.urlsplit(url)
    assert parsed.scheme == 'https' and parsed.hostname in {'api.github.com', 'uploads.github.com'}
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'ComputerTycoon-RU-release',
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if body is not None:
        headers['Content-Type'] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise RuntimeError(f'GitHub API {method} failed with HTTP {error.code}: ' + error.read(2000).decode('utf-8', 'replace')) from None


def publish(release, files):
    assert os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
    commit = os.environ['GITHUB_SHA']
    assert re.fullmatch('[0-9a-f]{40}', commit)
    tag = release['tag']
    assert re.fullmatch('[A-Za-z0-9._-]+', tag)
    api = f'https://api.github.com/repos/{REPOSITORY}'
    current = request('GET', api + '/releases/tags/' + tag, missing_ok=True)
    if current is None:
        existing_tag = request('GET', api + '/git/ref/tags/' + tag, missing_ok=True)
        if existing_tag is not None:
            assert existing_tag['object']['sha'] == commit, 'Tag already points to another commit'
        current = request('POST', api + '/releases', payload={
            'tag_name': tag,
            'target_commitish': commit,
            'name': release['title'],
            'body': (ROOT / release['notes']).read_text(),
            'draft': True,
            'prerelease': False,
        })
    elif current['draft']:
        assert current['target_commitish'] == commit, 'Existing draft belongs to another commit'

    assets = {a['name']: a for a in current['assets']}
    for path in files:
        data = path.read_bytes()
        expected = 'sha256:' + sha(data)
        if path.name in assets:
            asset = assets[path.name]
            assert asset.get('digest') == expected and asset['size'] == len(data), 'Existing asset differs; refusing to overwrite'
            continue
        assert current['draft'], 'Refusing to add files to a published release'
        upload = current['upload_url'].split('{', 1)[0] + '?name=' + urllib.parse.quote(path.name)
        created = request('POST', upload, body=data, content_type='application/zip' if path.suffix == '.zip' else 'text/plain')
        assert created['size'] == len(data) and created['state'] == 'uploaded'
        assert created.get('digest') in (None, expected), 'Uploaded asset hash differs'

    if current['draft']:
        current = request('PATCH', api + '/releases/' + str(current['id']), payload={'draft': False, 'make_latest': 'true'})
    print('Published: ' + current['html_url'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    release, files = check()
    if not args.check:
        publish(release, files)
