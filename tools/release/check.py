"""Lightweight offline syntax and release-policy checks; does not build or provision."""
import ast
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HEADERS, OPTIONAL_POLICY, REPO, asset_name, require, validate_tag, variants


def main():
    scripts = sorted((REPO / 'tools/release').glob('*.py'))
    for script in scripts:
        text = script.read_text(encoding='utf-8')
        if sys.version_info >= (3, 8):
            ast.parse(text, filename=str(script), feature_version=(3, 7))
        else:
            ast.parse(text, filename=str(script))
        compile(text, str(script), 'exec', dont_inherit=True)
    require(len(variants()) == 22, 'Expected 22 release variants')
    require([len(variants(family)) for family in ('linux', 'msvc', 'mingw', 'zig')] == [4, 8, 8, 2],
            'Unexpected variant counts')
    names = [asset_name(spec, 'agg.4') for spec in variants()]
    require(len(set(names)) == 22, 'Duplicate archive names')
    require({p.name for p in (REPO / 'include/leveldb').glob('*.h')} == HEADERS,
            'Public header policy is out of date')
    require(OPTIONAL_POLICY['HAVE_TCMALLOC']['generated'] is None,
            'TCMALLOC has no generated configuration macro')
    for spec in variants():
        if spec['os'] == 'windows':
            require('-' + spec['path_mode'] + '.' in asset_name(spec, 'agg.4'),
                    'Missing Windows path suffix')
        if spec['compiler'] == 'msvc':
            require(spec['crt'] == ('MTd' if spec['config'] == 'Debug' else 'MT'),
                    'MSVC CRT policy is incorrect')
    for tag in ('agg.4', 'agg.12', 'agg.4-rc1'):
        validate_tag(tag)
    for tag in ('../agg.4', 'agg.4\n', 'agg.4;whoami', 'agg.4$(id)', '-agg.4', 'agg.x'):
        try:
            validate_tag(tag)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('Unsafe release tag was accepted')
    workflow = (REPO / '.github/workflows/release.yml').read_text(encoding='utf-8')
    require(workflow.count('contents: write') == 1, 'Exactly one job may write releases')
    require(re.findall(r'^  ([a-z]+):\n(?=    name:)', workflow, re.MULTILINE) ==
            ['linux', 'msvc', 'mingw', 'zig', 'validate', 'publish'], 'Job chain changed')
    require('max-parallel: 1' in workflow and 'submodules: recursive' in workflow,
            'Missing serial matrix or submodules')
    require('actions/checkout@v5' in workflow and 'actions/upload-artifact@v4' in workflow and
            'actions/download-artifact@v4' in workflow, 'Action versions differ from policy')
    require(not re.search(r'run:.*\$\{\{', workflow), 'Workflow inputs must not enter shell source')
    print('PASS: {} Python files parse as Python 3.7 and compile; 22 unique variants (4/8/8/2).'.format(
        len(scripts)))
    print('PASS: 15 headers, ACP/UTF8 names, MT/MTd, tag validation and workflow policy checks.')
    print('Workflow YAML was inspected as text; no platform builds or runner tests were executed.')


if __name__ == '__main__':
    main()
