"""Validate a complete release; publish a new draft only after all checks pass."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import execute_main
from common import (REPO, context, require, sha256, validate_sha, verify_bundle, work_root)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        require(urllib.parse.urlsplit(new_url).scheme == 'https', 'Non-HTTPS API redirect')
        result = super().redirect_request(request, response, code, message, headers, new_url)
        if result and urllib.parse.urlsplit(request.full_url).netloc != urllib.parse.urlsplit(new_url).netloc:
            result.remove_header('Authorization')
        return result


class GitHub:
    def __init__(self, repository, token):
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository), 'Invalid repository')
        require(token and not any(c in token for c in '\r\n'), 'GITHUB_TOKEN is required')
        self.repository = repository
        self.token = token
        self.base = 'https://api.github.com/repos/' + repository
        self.opener = urllib.request.build_opener(SafeRedirect())

    def open(self, method, url, data=None, headers=None):
        host = urllib.parse.urlsplit(url)
        require(host.scheme == 'https' and host.netloc in ('api.github.com', 'uploads.github.com'),
                'Unexpected GitHub endpoint')
        request_headers = {'Authorization': 'Bearer ' + self.token,
                           'Accept': 'application/vnd.github+json',
                           'X-GitHub-Api-Version': '2022-11-28',
                           'User-Agent': 'leveldb-release-actions'}
        request_headers.update(headers or {})
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        return self.opener.open(request, timeout=180)

    def json(self, method, route, payload=None, missing=False):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        try:
            with self.open(method, self.base + route, data,
                           {'Content-Type': 'application/json'} if data is not None else {}) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            # Do not print request objects or authorization headers.
            raise RuntimeError('GitHub {} {} returned HTTP {}'.format(method, route, error.code)) from None

    def pages(self, route):
        for page in range(1, 101):
            separator = '&' if '?' in route else '?'
            values = self.json('GET', route + separator + 'per_page=100&page=' + str(page))
            require(isinstance(values, list), 'Expected paginated GitHub list')
            for value in values:
                yield value
            if len(values) < 100:
                return
        raise RuntimeError('GitHub pagination limit reached')

    def require_new_release(self, tag):
        require(self.json('GET', '/releases/tags/' + urllib.parse.quote(tag, safe=''),
                          missing=True) is None, 'A release already exists for this tag; refusing to overwrite')
        # Explicitly include drafts as well as published releases.
        require(not any(r.get('tag_name') == tag for r in self.pages('/releases')),
                'A release or draft already exists for this tag; refusing to overwrite')

    def verify_remote_tag(self, tag, sha):
        reference = self.json('GET', '/git/ref/tags/' + urllib.parse.quote(tag, safe=''))
        obj = reference['object']
        for _ in range(8):
            if obj['type'] == 'commit':
                require(validate_sha(obj['sha']) == sha, 'Remote tag targets a different source commit')
                return
            require(obj['type'] == 'tag', 'Tag does not point to a commit')
            obj = self.json('GET', '/git/tags/' + validate_sha(obj['sha']))['object']
        raise RuntimeError('Annotated tag chain is too deep')

    def upload(self, release_id, path):
        url = 'https://uploads.github.com/repos/{}/releases/{}/assets?name={}'.format(
            self.repository, release_id, urllib.parse.quote(path.name, safe=''))
        with path.open('rb') as stream:
            with self.open('POST', url, stream, {
                'Content-Type': 'application/octet-stream', 'Content-Length': str(path.stat().st_size),
            }) as response:
                return json.load(response)

    def verify_assets(self, release_id, files):
        expected = {p.name: p for p in files}
        assets = list(self.pages('/releases/{}/assets'.format(release_id)))
        require(len(assets) == len(expected) and {a['name'] for a in assets} == set(expected),
                'Server asset names/count do not match the validated release')
        for asset in assets:
            path = expected[asset['name']]
            expected_hash = sha256(path)
            require(asset.get('state') == 'uploaded' and asset.get('size') == path.stat().st_size,
                    'Server asset is incomplete: ' + path.name)
            digest = asset.get('digest')
            require(digest in (None, 'sha256:' + expected_hash), 'Server digest mismatch: ' + path.name)
            # Verify the actual server bytes, including when GitHub omits its digest field.
            downloaded = hashlib.sha256()
            size = 0
            with self.open('GET', self.base + '/releases/assets/' + str(asset['id']),
                           headers={'Accept': 'application/octet-stream'}) as response:
                for block in iter(lambda: response.read(1024 * 1024), b''):
                    downloaded.update(block)
                    size += len(block)
            require(size == path.stat().st_size and downloaded.hexdigest() == expected_hash,
                    'Downloaded server asset differs: ' + path.name)
            print('Verified server asset ' + path.name, flush=True)


def validated_notes(tag, sha, version, files):
    notes = REPO / 'docs' / 'releases' / (tag + '.md')
    require(notes.is_file(), 'Missing version-specific release notes: ' + str(notes))
    body = notes.read_text(encoding='utf-8').strip()
    require(body, 'Release notes must not be empty')
    archives = [p for p in files if p.name.endswith(('.zip', '.tar.gz'))]
    manifests = [p for p in files if p.name.startswith('manifest-')]
    # These statements follow only from verify_bundle's checked records.
    return body + '\n\n### Release artifact verification\n\n' + (
        'Source commit: `{}`. Library version: `{}`.\n\n'
        'The release validator checked {} binary archives and {} manifests against this '
        'source commit and release tag. Each archive contains all 15 public headers, '
        'the LevelDB library, a relocatable CMake package, LICENSE, AUTHORS and BUILD-INFO.json. '
        'BUILD-INFO records the toolchain, architecture, configuration, Windows path mode, '
        'MSVC CRT, disabled optional dependencies and executed test results.\n\n'
        'The validated records require supported CTest suites, two short rounds of each '
        'fuzz harness, and a public API consumer compiled and run from the final extracted archive. '
        'Windows records additionally require all three path tests, non-ASCII consumer paths '
        'and the actual LOG file. The consumer runtime dependencies were inspected. '
        'The six manifests remain in Actions artifacts; SHA256SUMS covers the {} public binary archives.\n'
    ).format(sha, version, len(archives), len(manifests), len(archives))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', required=True, type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    tag, sha = context()
    files, version = verify_bundle(args.artifacts, tag, sha)
    body = validated_notes(tag, sha, version, files)
    root = work_root()
    logs = root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    public_files = [path for path in files if path.name.endswith(('.zip', '.tar.gz'))]
    checksum = root / 'SHA256SUMS'
    with checksum.open('w', encoding='ascii', newline='\n') as stream:
        for path in public_files:
            stream.write('{}  {}\n'.format(sha256(path), path.name))
    (logs / 'validated-release-notes.txt').write_text(body, encoding='utf-8')
    (logs / 'validation.txt').write_text(
        'Validated {}\nSource {}\nVersion {}\n22 archives; 6 manifests; all checks passed\n'.format(
            tag, sha, version), encoding='utf-8')
    print('Validated 22 archives, six manifests, source {}, version {}'.format(sha, version))
    if args.verify_only:
        return
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('GITHUB_EVENT_NAME') == 'push' and
            os.environ.get('GITHUB_REF') == 'refs/tags/' + tag,
            'Only a tag-push Actions run may publish')
    api = GitHub(os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GITHUB_TOKEN', ''))
    api.verify_remote_tag(tag, sha)
    api.require_new_release(tag)
    release = api.json('POST', '/releases', {
        'tag_name': tag, 'target_commitish': sha, 'name': tag, 'body': body,
        'draft': True, 'prerelease': False, 'generate_release_notes': False,
    })
    release_id = release['id']
    require(release.get('draft') is True and release.get('tag_name') == tag,
            'Unexpected draft release response')
    # Leave this draft for inspection if any upload or verification fails. Never overwrite/retry it.
    print('Created draft release {}. Failures will leave it as a draft.'.format(release_id))
    all_files = public_files + [checksum]
    for path in all_files:
        asset = api.upload(release_id, path)
        require(asset.get('name') == path.name and asset.get('size') == path.stat().st_size,
                'Unexpected asset upload response')
    api.verify_assets(release_id, all_files)
    api.verify_remote_tag(tag, sha)
    published = api.json('PATCH', '/releases/' + str(release_id), {'draft': False, 'make_latest': 'true'})
    require(published.get('draft') is False and published.get('tag_name') == tag,
            'Release did not become public')
    print('Published ' + published['html_url'])


if __name__ == '__main__':
    execute_main(main)
