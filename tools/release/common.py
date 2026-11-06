"""Shared release policy. Standard library only; compatible with Python 3.7."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile

REPO = Path(__file__).resolve().parents[2]
HEADERS = frozenset((
    'c.h', 'cache.h', 'comparator.h', 'db.h', 'dumpfile.h', 'env.h',
    'export.h', 'filter_policy.h', 'iterator.h', 'options.h', 'slice.h',
    'status.h', 'table_builder.h', 'table.h', 'write_batch.h',
))
OPTIONAL = ('HAVE_SNAPPY', 'HAVE_ZSTD', 'HAVE_CRC32C', 'HAVE_TCMALLOC')
OPTIONAL_POLICY = {key: {'cache': 'OFF', 'generated': None if key == 'HAVE_TCMALLOC' else 0}
                   for key in OPTIONAL}
SPEC_KEYS = ('os', 'arch', 'compiler', 'linkage', 'config', 'path_mode', 'crt')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_tag(tag):
    require(isinstance(tag, str) and re.fullmatch(
        r'agg\.[0-9]+(?:[.-][A-Za-z0-9]+)*', tag), 'Invalid release tag')
    return tag


def validate_sha(sha):
    require(isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha),
            'Expected a complete lowercase source commit SHA')
    return sha


def variants(family=None, arch=None):
    result = []
    def add(os_name, architecture, compiler, linkage, config, mode='posix'):
        crt = ('MTd' if config == 'Debug' else 'MT') if compiler == 'msvc' else None
        result.append(dict(zip(SPEC_KEYS, (
            os_name, architecture, compiler, linkage, config, mode, crt))))
    for architecture in ('x64', 'x86', 'arm64'):
        add('linux', architecture, 'gcc', 'static', 'Release')
        if architecture == 'x64':
            add('linux', architecture, 'gcc', 'shared', 'Release')
    for linkage in ('static', 'dll'):
        for config in ('Debug', 'Release'):
            for mode in ('acp', 'utf8'):
                add('windows', 'x64', 'msvc', linkage, config, mode)
    for architecture in ('x64', 'x86'):
        for config in ('Debug', 'Release'):
            for mode in ('acp', 'utf8'):
                add('windows', architecture, 'mingw', 'static', config, mode)
    for mode in ('acp', 'utf8'):
        add('windows', 'arm64', 'zig', 'static', 'Release', mode)
    return [v for v in result if
            (family is None or (v['os'] if v['os'] == 'linux' else v['compiler']) == family)
            and (arch is None or v['arch'] == arch)]


def stem(spec, tag):
    validate_tag(tag)
    name = 'leveldb-{}-{}-{}-{}-{}-{}'.format(
        tag, spec['os'], spec['arch'], spec['compiler'], spec['linkage'],
        spec['config'].lower())
    return name + ('-' + spec['path_mode'] if spec['os'] == 'windows' else '')


def asset_name(spec, tag):
    extension = '.zip' if spec['compiler'] in ('msvc', 'mingw') else '.tar.gz'
    return stem(spec, tag) + extension


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def run(args, log, cwd=REPO, env=None, timeout=3600):
    args = [str(a) for a in args]
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    print('Running {} (log: {})'.format(repr(args), log), flush=True)
    with log.open('wb') as stream:
        process = subprocess.run(args, cwd=str(cwd), env=env, stdout=stream,
                                 stderr=subprocess.STDOUT, timeout=timeout, check=False)
    text = log.read_text(encoding='utf-8', errors='replace')
    if process.returncode:
        print(text[-12000:], flush=True)
        raise RuntimeError('Command failed ({}) -- {}'.format(process.returncode, log))
    return text


def context():
    require(Path.cwd().resolve() == REPO, 'Start Python from the repository directory')
    tag = validate_tag(os.environ.get('RELEASE_TAG', ''))
    sha = validate_sha(os.environ.get('RELEASE_SHA', ''))
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=str(REPO),
                                     universal_newlines=True).strip()
    require(sha == actual, 'Checkout does not match RELEASE_SHA')
    return tag, sha


def work_root():
    root = Path(os.environ['RUNNER_TEMP']).resolve() / 'leveldb-release'
    require(root != REPO and REPO not in root.parents, 'Runner temp must be outside checkout')
    root.mkdir(parents=True, exist_ok=True)
    return root


def export_env(name, value):
    value = str(value)
    require(re.fullmatch(r'[A-Z][A-Z0-9_]*', name) and not any(
        c in value for c in '\r\n\x00'), 'Invalid environment assignment')
    with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8', newline='\n') as stream:
        stream.write(name + '=' + value + '\n')


def checked_members(archive):
    """Inspect paths before extraction, including links and their ancestors."""
    entries = []
    if zipfile.is_zipfile(str(archive)):
        with zipfile.ZipFile(str(archive)) as source:
            for item in source.infolist():
                require(not stat.S_ISLNK(item.external_attr >> 16), 'ZIP symlink is forbidden')
                entries.append((item.filename.rstrip('/'),
                                'dir' if item.is_dir() else 'file', '', item.file_size))
    else:
        with tarfile.open(str(archive), 'r:*') as source:
            for item in source.getmembers():
                require(item.isdir() or item.isfile() or item.issym(),
                        'Archive contains a special file or hard link')
                entries.append((item.name.rstrip('/'),
                                'dir' if item.isdir() else 'link' if item.issym() else 'file',
                                item.linkname, item.size))
    names = set()
    links = set(name for name, kind, _, _ in entries if kind == 'link')
    require(len(entries) <= 50000 and sum(e[3] for e in entries) <= 4 * 1024 ** 3,
            'Archive exceeds extraction limits')
    for name, kind, target, _ in entries:
        path = PurePosixPath(name)
        require(name and '\\' not in name and ':' not in name and
                not path.is_absolute() and all(p not in ('..', '.') for p in name.split('/')),
                'Unsafe archive path: ' + name)
        require(name not in names, 'Duplicate archive member: ' + name)
        names.add(name)
        require(not any(str(parent) in links for parent in path.parents),
                'Archive member is nested under a symlink')
        if kind == 'link':
            require(target and not target.startswith('/') and '\\' not in target and ':' not in target,
                    'Unsafe archive link')
            resolved = posixpath.normpath(posixpath.join(str(path.parent), target))
            require(resolved.split('/')[0] == path.parts[0] and
                    not resolved.startswith('../'), 'Archive link escapes its root')
    for name, kind, target, _ in entries:
        if kind == 'link':
            resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent), target))
            require(resolved in names, 'Dangling archive symlink: ' + name)
    return entries


def unpack(archive, destination):
    """Preserve tar symlinks; never execute downloaded contents during extraction."""
    archive, destination = Path(archive), Path(destination)
    entries = checked_members(archive)
    destination.mkdir(parents=True, exist_ok=True)
    require(not any(destination.iterdir()), 'Extraction directory must be empty')
    if zipfile.is_zipfile(str(archive)):
        with zipfile.ZipFile(str(archive)) as source:
            source.extractall(str(destination))
    else:
        with tarfile.open(str(archive), 'r:*') as source:
            for member in source.getmembers():
                output = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if member.issym():
                        output.symlink_to(member.linkname)
                    else:
                        with source.extractfile(member) as inp, output.open('wb') as out:
                            shutil.copyfileobj(inp, out)
                        output.chmod(member.mode & 0o777)
    return entries


def pack(stage, archive, root_name):
    stage, archive = Path(stage), Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if str(archive).endswith('.zip'):
        with zipfile.ZipFile(str(archive), 'w', zipfile.ZIP_DEFLATED) as target:
            for path in sorted(stage.rglob('*')):
                require(not path.is_symlink(), 'Windows ZIP cannot carry symlinks')
                if path.is_file():
                    target.write(str(path), root_name + '/' + path.relative_to(stage).as_posix())
    else:
        with tarfile.open(str(archive), 'w:gz', dereference=False) as target:
            target.add(str(stage), arcname=root_name)


def archive_bytes(archive, name):
    if zipfile.is_zipfile(str(archive)):
        with zipfile.ZipFile(str(archive)) as source:
            return source.read(name)
    with tarfile.open(str(archive), 'r:*') as source:
        with source.extractfile(name) as stream:
            return stream.read()


def inspect_package(archive, spec, tag, sha, library_version=None, consumer_required=True):
    """Apply the same fail-closed layout/metadata policy at build and publication."""
    archive = Path(archive)
    root = stem(spec, tag)
    entries = checked_members(archive)
    require(all(n == root or n.startswith(root + '/') for n, _, _, _ in entries),
            'Package has an unexpected top-level directory')
    files = {n[len(root) + 1:]: (kind, size) for n, kind, _, size in entries if kind != 'dir'}
    require({n[len('include/leveldb/'):] for n in files if n.startswith('include/leveldb/')} == HEADERS,
            'Package must contain all 15 distinct public headers')
    require(all(files['include/leveldb/' + h][0] == 'file' and
                files['include/leveldb/' + h][1] > 0 for h in HEADERS), 'Empty or linked header')
    required = {'LICENSE', 'AUTHORS', 'BUILD-INFO.json',
                'lib/cmake/leveldb/leveldbConfig.cmake',
                'lib/cmake/leveldb/leveldbConfigVersion.cmake',
                'lib/cmake/leveldb/leveldbTargets.cmake',
                'lib/cmake/leveldb/leveldbTargets-' + spec['config'].lower() + '.cmake'}
    require(required.issubset(files), 'Missing license, metadata or CMake package files')
    libraries = []
    for name in files:
        if name in required or name.startswith('include/leveldb/'):
            continue
        require(re.fullmatch(r'(lib|bin)/(lib)?leveldb(?:\.a|\.lib|\.dll|\.so(?:\.[0-9]+)*)', name),
                'Unexpected file (dependencies must not be bundled): ' + name)
        libraries.append(name)
    require(libraries, 'Missing LevelDB library')
    if spec['linkage'] == 'dll':
        require('bin/leveldb.dll' in files and 'lib/leveldb.lib' in files,
                'DLL package requires both DLL and import library')
    elif spec['linkage'] == 'shared':
        require('lib/libleveldb.so' in files, 'Shared package lacks the linker symlink')
    else:
        require(any(n.endswith(('.a', '.lib')) for n in libraries), 'Missing static library')
    raw = archive_bytes(archive, root + '/BUILD-INFO.json')
    info = json.loads(raw.decode('utf-8'))
    require(info.get('schema') == 1 and info.get('source_sha') == sha and
            info.get('release_tag') == tag and info.get('asset') == asset_name(spec, tag),
            'BUILD-INFO source/tag/asset mismatch')
    require(info.get('variant') == spec, 'BUILD-INFO variant mismatch')
    require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', info.get('library_version', '')),
            'Missing library version')
    require(library_version is None or info['library_version'] == library_version,
            'Library versions differ across assets')
    require(info.get('abi') == {'exceptions': True, 'rtti': True, 'msvc_crt': spec['crt']},
            'Exception/RTTI/CRT policy mismatch')
    require(info.get('optional_dependencies') == OPTIONAL_POLICY, 'Optional dependency detected')
    require(info.get('tools', {}).get('compiler_version') and info['tools'].get('cmake'),
            'Missing tool versions')
    tests = info.get('tests', {})
    ctest = tests.get('ctest', {})
    expected = {'leveldb_tests', 'c_test'}
    if spec['os'] == 'windows':
        expected.add('windows_path_test')
        require(tests.get('windows_paths') == {'passed': True, 'cases': 3},
                'All three Windows path cases must pass')
    if spec['linkage'] == 'static':
        expected.add('env_windows_test' if spec['os'] == 'windows' else 'env_posix_test')
    require(ctest.get('passed') is True and expected.issubset(set(ctest.get('names', []))) and
            ctest.get('tests', 0) >= len(expected), 'Missing supported CTest coverage')
    fuzz = tests.get('fuzz', [])
    require(len(fuzz) == 4 and all(t.get('passed') is True for t in fuzz) and
            {(t.get('harness'), t.get('seed'), t.get('iterations')) for t in fuzz} ==
            {('db', 1, 20), ('db', 1001, 20), ('recovery', 1, 10), ('recovery', 1001, 10)},
            'Expected two successful short rounds per fuzz harness')
    require(all(t.get('passed') is True for t in tests.get('regression_corpus', [])),
            'Fuzz corpus replay failed')
    if consumer_required:
        require(tests.get('archive_consumer', {}).get('passed') is True,
                'Archive consumer has not passed')
        require(tests.get('runtime_dependencies', {}).get('passed') is True and
                tests['runtime_dependencies'].get('scanner') in ('ldd', 'dumpbin', 'objdump'),
                'Consumer runtime dependencies have not been inspected')
    return info, hashlib.sha256(raw).hexdigest()


def verify_bundle(directory, tag, sha):
    """Require exactly 22 binaries, complete disjoint manifests, and no stale assets."""
    directory = Path(directory)
    specs = {asset_name(v, tag): v for v in variants()}
    manifests = sorted(directory.glob('manifest-*.json'))
    require(len(manifests) == 6, 'Expected three Linux manifests and three Windows manifests')
    expected_manifest_ids = {'linux-x64', 'linux-x86', 'linux-arm64', 'msvc', 'mingw', 'zig'}
    require({m.stem[len('manifest-'):] for m in manifests} == expected_manifest_ids,
            'Unexpected manifest set')
    actual = {p.name for p in directory.iterdir()}
    require(actual == set(specs) | {m.name for m in manifests}, 'Missing or unexpected artifact files')
    seen = set()
    library_version = None
    for manifest in manifests:
        data = read_json(manifest)
        require(data.get('schema') == 1 and data.get('release_tag') == tag and
                data.get('source_sha') == sha, 'Manifest source/tag mismatch')
        require(data.get('artifacts'), 'Empty manifest')
        manifest_id = manifest.stem[len('manifest-'):]
        expected_names = {asset_name(v, tag) for v in variants(
            'linux' if manifest_id.startswith('linux-') else manifest_id,
            manifest_id[len('linux-'):] if manifest_id.startswith('linux-') else None)}
        require({entry.get('name') for entry in data['artifacts']} == expected_names,
                'Manifest contains the wrong variants')
        for entry in data['artifacts']:
            name = entry.get('name')
            require(name in specs and name not in seen, 'Duplicate or unknown asset in manifests')
            seen.add(name)
            path = directory / name
            require(path.is_file() and not path.is_symlink(), 'Artifact is not a regular file')
            require(path.stat().st_size == entry.get('size') and sha256(path) == entry.get('sha256'),
                    'Archive size/hash mismatch: ' + name)
            info, info_hash = inspect_package(path, specs[name], tag, sha, library_version)
            require(info_hash == entry.get('build_info_sha256') and
                    entry.get('final_archive_consumer') is True, 'Unverified final archive: ' + name)
            library_version = info['library_version']
            require(data.get('library_version') == library_version, 'Manifest library version mismatch')
    require(seen == set(specs), 'Incomplete 22-archive asset set')
    return sorted(directory.iterdir()), library_version
