"""Resolve the checkout's commit identity without interpolating inputs into a shell."""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import execute_main
from common import REPO, export_env, require, validate_sha, validate_tag


def main():
    require(Path.cwd().resolve() == REPO, 'Start Python from the repository directory')
    tag = validate_tag(os.environ.get('RELEASE_TAG', ''))
    event_sha = validate_sha(os.environ.get('GITHUB_SHA', ''))
    def resolve(ref):
        return subprocess.check_output(['git', 'rev-parse', ref], cwd=str(REPO),
                                       universal_newlines=True).strip()
    sha = validate_sha(resolve('HEAD'))
    require(resolve(event_sha + '^{commit}') == sha, 'Checkout differs from the event commit')
    ref = os.environ.get('GITHUB_REF', '')
    if ref.startswith('refs/tags/'):
        require(ref == 'refs/tags/' + tag and resolve(ref + '^{commit}') == sha,
                'Tag and checkout commit disagree')
    export_env('RELEASE_SHA', sha)
    print('Validated {} at {}'.format(tag, sha))


if __name__ == '__main__':
    execute_main(main)
