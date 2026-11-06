"""Find a successful same-commit/version branch preview using the Actions token."""
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import context, execute_main, require


def find_preview(tag, sha):
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository),
            'Invalid GitHub repository')
    token = os.environ.get('GITHUB_TOKEN', '')
    require(token, 'Missing Actions GITHUB_TOKEN')
    api = os.environ.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/')
    require(api.startswith('https://'), 'GitHub API must use HTTPS')
    base = api + '/repos/' + repository + '/actions/'

    def pages(path, key, parameters=None):
        page = 1
        while True:
            query = dict(parameters or {}, per_page=100, page=page)
            request = Request(base + path + '?' + urlencode(query), headers={
                'Authorization': 'Bearer ' + token,
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
            })
            # API/transport failures deliberately fail prepare rather than imply no match.
            with urlopen(request, timeout=60) as response:
                items = json.load(response)[key]
            yield from items
            if len(items) < 100:
                break
            page += 1

    for run in pages('workflows/release.yml/runs', 'workflow_runs', {
            'event': 'push', 'branch': 'ci/release-agg4',
            'head_sha': sha, 'status': 'success'}):
        if not (run.get('event') == 'push' and
                run.get('head_branch') == 'ci/release-agg4' and
                run.get('head_sha') == sha and
                run.get('status') == 'completed' and
                run.get('conclusion') == 'success' and
                run.get('path') == '.github/workflows/release.yml' and
                (run.get('head_repository') or {}).get('full_name') == repository):
            continue
        run_id = run.get('id')
        require(type(run_id) is int and run_id > 0, 'Invalid workflow run ID')
        # This same-commit workflow uploads the marker only after verify-only succeeds.
        # Full manifests and archive bytes are checked again by validate and publish.
        for artifact in pages('runs/{}/artifacts'.format(run_id), 'artifacts'):
            if (artifact.get('name') == 'release-validation-' + tag and
                    artifact.get('expired') is False):
                return str(run_id)
    return ''


def main():
    tag, sha = context()
    run_id = ''
    if (os.environ.get('GITHUB_EVENT_NAME') == 'push' and
            os.environ.get('GITHUB_REF', '').startswith('refs/tags/agg.')):
        require(os.environ['GITHUB_REF'] == 'refs/tags/' + tag,
                'Tag ref differs from RELEASE_TAG')
        run_id = find_preview(tag, sha)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8', newline='\n') as stream:
        stream.write('run_id=' + run_id + '\n')
    print('Preview run: ' + (run_id or 'none; build fresh'))


if __name__ == '__main__':
    execute_main(main)
