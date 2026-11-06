"""Build, test, install, archive and reconsume every requested release variant."""
import argparse
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import execute_main
from common import (OPTIONAL, OPTIONAL_POLICY, REPO, asset_name, context, inspect_package, pack,
                    require, run, sha256, stem, unpack, variants, work_root, write_json)


def cache_values(build):
    values = {}
    for line in (build / 'CMakeCache.txt').read_text(encoding='utf-8').splitlines():
        match = re.match(r'([^/#][^:]*):[^=]+=(.*)$', line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def cmake_literal(value):
    value = str(value).replace('\\', '/')
    require(']]' not in value and not any(c in value for c in '\r\n\x00'), 'Unsafe CMake path')
    return '[[' + value + ']]'


def toolchain(spec, area, env):
    """Return CMake arguments shared by library and its external consumer."""
    compiler = spec['compiler']
    flags = []
    if compiler == 'msvc':
        # Let CMake choose the hosted Visual Studio generator; no setup action.
        flags += ['-A', 'x64', '-DCMAKE_MSVC_RUNTIME_LIBRARY=' +
                  ('MultiThreadedDebug' if spec['config'] == 'Debug' else 'MultiThreaded')]
    elif compiler == 'gcc':
        flags += ['-G', 'Unix Makefiles', '-DCMAKE_C_COMPILER=/usr/bin/gcc',
                  '-DCMAKE_CXX_COMPILER=/usr/bin/g++',
                  '-DCMAKE_CXX_STANDARD_LIBRARIES=-lstdc++fs']
        if spec['arch'] == 'x86':
            flags += ['-DCMAKE_C_FLAGS=-m32', '-DCMAKE_CXX_FLAGS=-m32']
    elif compiler == 'mingw':
        directory = Path(os.environ['RELEASE_MINGW_' + spec['arch'].upper()])
        env['PATH'] = str(directory) + os.pathsep + env['PATH']
        flags += ['-G', 'Ninja', '-DCMAKE_MAKE_PROGRAM=' + os.environ['RELEASE_NINJA'],
                  '-DCMAKE_C_COMPILER=' + str(directory / 'gcc.exe'),
                  '-DCMAKE_CXX_COMPILER=' + str(directory / 'g++.exe')]
    else:
        area.mkdir(parents=True, exist_ok=True)
        wrappers = {}
        for name, invocation in (('cc', 'cc -target aarch64-windows-gnu'),
                                 ('cxx', 'c++ -target aarch64-windows-gnu'),
                                 ('ar', 'ar'), ('ranlib', 'ranlib')):
            wrapper = area / (name + '.cmd')
            wrapper.write_bytes(('@echo off\r\n"%LEVELDB_ZIG_EXE%" ' + invocation +
                                 ' %*\r\nexit /b %errorlevel%\r\n').encode('ascii'))
            wrappers[name] = wrapper
        path = area / 'zig-toolchain.cmake'
        content = ['set(CMAKE_SYSTEM_NAME Windows)', 'set(CMAKE_SYSTEM_PROCESSOR ARM64)']
        for key, name in (('CMAKE_C_COMPILER', 'cc'), ('CMAKE_CXX_COMPILER', 'cxx'),
                          ('CMAKE_AR', 'ar'), ('CMAKE_RANLIB', 'ranlib')):
            content.append('set({} {})'.format(key, cmake_literal(wrappers[name])))
        path.write_text('\n'.join(content) + '\n', encoding='utf-8')
        flags += ['-G', 'Ninja', '-DCMAKE_MAKE_PROGRAM=' + os.environ['RELEASE_NINJA'],
                  '-DCMAKE_TOOLCHAIN_FILE=' + str(path)]
    return flags


def compiler_metadata(build):
    files = list((build / 'CMakeFiles').glob('*/CMakeCXXCompiler.cmake'))
    require(len(files) == 1, 'Expected one compiler identification file')
    text = files[0].read_text(encoding='utf-8')
    result = {}
    for variable, key in (('CMAKE_CXX_COMPILER_VERSION', 'compiler_version'),
                          ('CMAKE_CXX_COMPILER_ID', 'compiler_id'),
                          ('CMAKE_CXX_COMPILER', 'compiler_path')):
        match = re.search(r'set\(' + variable + r' "([^"]+)"\)', text)
        require(match, 'Missing compiler identification: ' + variable)
        result[key] = match.group(1)
    return result


def verify_configuration(build, spec):
    cache = cache_values(build)
    header = (build / 'include/port/port_config.h').read_text(encoding='utf-8')
    for flag in OPTIONAL:
        require(cache.get(flag) == 'OFF', 'Dependency cache is not explicitly OFF: ' + flag)
        if flag != 'HAVE_TCMALLOC':
            require(re.search(r'^#define\s+' + flag + r'\s+0\s*$', header, re.MULTILINE),
                    'Generated header is not zero: ' + flag)
    for flag in ('LEVELDB_DISABLE_EXCEPTIONS', 'LEVELDB_DISABLE_RTTI'):
        require(cache.get(flag) == 'OFF', 'Exception/RTTI defaults changed')
    require(cache.get('BUILD_SHARED_LIBS') == ('OFF' if spec['linkage'] == 'static' else 'ON'),
            'Incorrect library kind')
    require(cache.get('CMAKE_SIZEOF_VOID_P', str(4 if spec['arch'] == 'x86' else 8)) ==
            str(4 if spec['arch'] == 'x86' else 8), 'Unexpected architecture')
    # Pointer size is stored in compiler identification on some CMake versions.
    compiler_file = next((build / 'CMakeFiles').glob('*/CMakeCXXCompiler.cmake'))
    compiler_text = compiler_file.read_text(encoding='utf-8')
    pointer_size = re.search(r'set\(CMAKE_CXX_SIZEOF_DATA_PTR "?([48])"?\)', compiler_text)
    require(pointer_size and int(pointer_size.group(1)) == (4 if spec['arch'] == 'x86' else 8),
            'Compiler target pointer size mismatch')
    if spec['os'] == 'windows':
        require(cache.get('LEVELDB_WINDOWS_UTF8_PATHS') ==
                ('ON' if spec['path_mode'] == 'utf8' else 'OFF'), 'Incorrect Windows path mode')
    if spec['compiler'] == 'msvc':
        crt = 'MultiThreadedDebug' if spec['config'] == 'Debug' else 'MultiThreaded'
        require(cache.get('CMAKE_MSVC_RUNTIME_LIBRARY') == crt, 'Incorrect MSVC CRT cache')
        projects = list(build.glob('**/leveldb.vcxproj'))
        require(len(projects) == 1, 'MSVC project missing')
        runtimes = {e.text for e in ET.parse(str(projects[0])).getroot().iter()
                    if e.tag.endswith('}RuntimeLibrary')}
        require(runtimes == {crt}, 'MSVC library compile runtime does not match ' + crt)
    for key in ('CMAKE_C_FLAGS', 'CMAKE_CXX_FLAGS', 'CMAKE_CXX_FLAGS_DEBUG',
                'CMAKE_CXX_FLAGS_RELEASE'):
        value = cache.get(key, '')
        require(not re.search(r'-fno-(exceptions|rtti)|/GR-|_HAS_EXCEPTIONS=0|/EHs-c-', value),
                'Exceptions or RTTI disabled in compiler flags')
    return cache


def executable(build, name, spec):
    suffix = '.exe' if spec['os'] == 'windows' else ''
    parent = build / spec['config'] if spec['compiler'] == 'msvc' else build
    path = parent / (name + suffix)
    require(path.is_file(), 'Missing executable: ' + str(path))
    return path


def test_variant(build, spec, logs, env, ctest):
    tests = {}
    gtest = logs / 'gtest'
    gtest.mkdir(parents=True)
    test_env = env.copy()
    test_env['GTEST_OUTPUT'] = 'xml:' + gtest.as_posix() + '/'
    junit = logs / 'ctest.xml'
    run([ctest, '--test-dir', build, '-C', spec['config'], '--no-tests=error',
         '--output-on-failure', '--parallel', '1', '--timeout', '900', '--output-junit', junit],
        logs / 'ctest.log', env=test_env, timeout=7200)
    cases = list(ET.parse(str(junit)).getroot().iter('testcase'))
    require(cases and all(c.find('failure') is None and c.find('error') is None and
                         c.find('skipped') is None and c.get('status', 'run') != 'notrun'
                         for c in cases), 'CTest did not pass all registered tests')
    tests['ctest'] = {'passed': True, 'tests': len(cases), 'names': [c.get('name') for c in cases]}
    suite = ET.parse(str(gtest / 'leveldb_tests.xml')).getroot()
    require(int(suite.get('tests', '0')) >= (100 if spec['linkage'] == 'static' else 1),
            'Full static test suite was not built')
    if spec['os'] == 'windows':
        path_suite = ET.parse(str(gtest / 'windows_path_test.xml')).getroot()
        require(int(path_suite.get('tests', '0')) == 3 and
                all(int(path_suite.get(k, '0')) == 0 for k in ('failures', 'errors', 'disabled')) and
                not list(path_suite.iter('skipped')), 'Three Windows path tests must run and pass')
        tests['windows_paths'] = {'passed': True, 'cases': 3}
    tests['fuzz'] = []
    tests['regression_corpus'] = []
    for harness, count in (('db', 20), ('recovery', 10)):
        binary = executable(build, 'leveldb_fuzz_' + harness, spec)
        for seed in (1, 1001):
            output = run([binary, str(count), str(seed)],
                         logs / ('fuzz-{}-{}.log'.format(harness, seed)), env=env, timeout=1200)
            require('leveldb_fuzz_' + harness + ': OK' in output, 'Missing fuzz completion marker')
            tests['fuzz'].append({'harness': harness, 'iterations': count, 'seed': seed, 'passed': True})
    corpus = REPO / 'fuzz/regression_seeds.txt'
    for index, line in enumerate(corpus.read_text(encoding='utf-8').splitlines()):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        require(len(fields) == 2 and fields[0] in ('db', 'recovery') and
                re.fullmatch(r'[0-9]{1,20}', fields[1]) and int(fields[1]) < 2 ** 64,
                'Invalid regression seed')
        harness, seed = fields
        output = run([executable(build, 'leveldb_fuzz_' + harness, spec), '1', seed],
                     logs / ('corpus-{}.log'.format(index)), env=env, timeout=1200)
        require('leveldb_fuzz_' + harness + ': OK' in output, 'Corpus replay did not finish')
        tests['regression_corpus'].append({'harness': harness, 'seed': int(seed), 'passed': True})
    return tests


def inspect_runtime(binary, prefix, spec, env, logs):
    if spec['os'] == 'linux':
        command, scanner = ['ldd'], 'ldd'
        targets = [binary] + [p for p in (prefix / 'lib').glob('*.so*') if p.is_file()]
    elif spec['compiler'] == 'mingw':
        tool = Path(os.environ['RELEASE_MINGW_' + spec['arch'].upper()]) / 'objdump.exe'
        require(tool.is_file(), 'MinGW objdump is required for dependency inspection')
        command, scanner = [tool, '-p'], 'objdump'
        targets = [binary] + list((prefix / 'bin').glob('*.dll'))
    else:
        candidates = list(Path('C:/Program Files/Microsoft Visual Studio').glob(
            '*/*/VC/Tools/MSVC/*/bin/Host*/*/dumpbin.exe'))
        preferred = 'hostarm64/arm64' if spec['arch'] == 'arm64' else 'hostx64/x64'
        candidates.sort(key=lambda p: (preferred in p.as_posix().lower(), p.as_posix()), reverse=True)
        if not candidates and shutil.which('dumpbin'):
            candidates = [Path(shutil.which('dumpbin'))]
        require(candidates, 'Hosted dumpbin is required for Windows dependency inspection')
        command, scanner = [candidates[0], '/DEPENDENTS'], 'dumpbin'
        targets = [binary] + list((prefix / 'bin').glob('*.dll'))
    seen = set()
    for target in targets:
        resolved = target.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        output = run(command + [target], logs / ('dependencies-' + target.name + '.log'), env=env)
        require(not re.search(r'(?i)(snappy|zstd|crc32c|tcmalloc)[^\s]*\.(dll|so)', output),
                'Optional runtime dependency detected')
    return {'passed': True, 'scanner': scanner, 'files': sorted(p.name for p in seen)}


def consume(archive, source, area, flags, spec, env, logs, cmake):
    unpack(archive, area / 'extracted')
    prefix = area / 'extracted' / archive.name.rsplit('.zip', 1)[0]
    if archive.name.endswith('.tar.gz'):
        prefix = area / 'extracted' / archive.name[:-len('.tar.gz')]
    build = area / 'consumer-build'
    run([cmake, '-S', source / 'tools/release/consumer', '-B', build] + flags + [
        '-DCMAKE_BUILD_TYPE=' + spec['config'], '-DLEVELDB_RELEASE_PREFIX=' + str(prefix),
        '-DRELEASE_UTF8_PATHS=' + ('ON' if spec['path_mode'] == 'utf8' else 'OFF')],
        logs / 'consumer-configure.log', env=env)
    run([cmake, '--build', build, '--config', spec['config'], '--parallel', '2'],
        logs / 'consumer-build.log', env=env)
    consumer_env = env.copy()
    consumer_env['PATH'] = str(prefix / 'bin') + os.pathsep + env['PATH']
    if spec['os'] == 'linux':
        consumer_env['LD_LIBRARY_PATH'] = str(prefix / 'lib') + os.pathsep + env.get('LD_LIBRARY_PATH', '')
    output = run([executable(build, 'release_smoke', spec), area / 'smoke-db'],
                 logs / 'consumer-run.log', env=consumer_env, timeout=300)
    require('release consumer: PASS' in output, 'Consumer did not report completion')
    return inspect_runtime(executable(build, 'release_smoke', spec), prefix, spec, consumer_env, logs)


def collect_diagnostics(build, logs):
    if not build.exists():
        return
    for pattern in ('CMakeCache.txt', 'CMakeFiles/CMakeConfigureLog.yaml',
                    'Testing/Temporary/*.log', 'compile_commands.json'):
        for path in build.glob(pattern):
            if path.is_file():
                output = logs / 'diagnostics' / path.relative_to(build)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(path), str(output))


def build_one(spec, tag, sha, source, root, cmake, ctest, submodules):
    name = stem(spec, tag)
    area = root / 'variants' / name
    build = area / 'build'
    logs = root / 'logs' / name
    logs.mkdir(parents=True)
    env = os.environ.copy()
    temporary = area / 'tmp'
    temporary.mkdir(parents=True)
    for variable in ('TMP', 'TEMP', 'TMPDIR', 'TEST_TMPDIR'):
        env[variable] = str(temporary)
    flags = toolchain(spec, root / 'wrappers', env)
    install = area / 'install-original'
    try:
        arguments = [cmake, '-S', source, '-B', build] + flags + [
            '-DCMAKE_BUILD_TYPE=' + spec['config'], '-DCMAKE_INSTALL_PREFIX=' + str(install),
            '-DCMAKE_INSTALL_LIBDIR=lib', '-DCMAKE_INSTALL_BINDIR=bin',
            '-DCMAKE_INSTALL_INCLUDEDIR=include', '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
            '-DBUILD_SHARED_LIBS=' + ('OFF' if spec['linkage'] == 'static' else 'ON'),
            '-DLEVELDB_INSTALL=ON', '-DLEVELDB_BUILD_TESTS=ON', '-DLEVELDB_BUILD_FUZZ_TESTS=ON',
            '-DLEVELDB_BUILD_BENCHMARKS=OFF', '-DLEVELDB_DISABLE_EXCEPTIONS=OFF',
            '-DLEVELDB_DISABLE_RTTI=OFF', '-DLEVELDB_WINDOWS_UTF8_PATHS=' +
            ('ON' if spec['path_mode'] == 'utf8' else 'OFF')]
        arguments += ['-D' + flag + '=OFF' for flag in OPTIONAL]
        run(arguments, logs / 'configure.log', env=env)
        cache = verify_configuration(build, spec)
        tools = compiler_metadata(build)
        if spec['compiler'] == 'gcc':
            require(tools['compiler_version'].split('.')[0] == '8', 'Linux packages require GCC 8')
            require(platform.machine().lower() == ('aarch64' if spec['arch'] == 'arm64' else 'x86_64'),
                    'Linux release must execute on the requested native host (x86 uses multilib)')
        tools.update({
            'cmake': run([cmake, '--version'], logs / 'cmake-version.log').strip(),
            'ctest': run([ctest, '--version'], logs / 'ctest-version.log').strip(),
            'python': sys.version, 'host': platform.platform(),
            'git': run(['git', '--version'], logs / 'git-version.log').strip(),
        })
        if spec['compiler'] == 'zig':
            tools['zig'] = run([os.environ['LEVELDB_ZIG_EXE'], 'version'], logs / 'zig-version.log').strip()
        if spec['compiler'] in ('zig', 'mingw'):
            tools['ninja'] = run([os.environ['RELEASE_NINJA'], '--version'], logs / 'ninja-version.log').strip()
        run([cmake, '--build', build, '--config', spec['config'], '--parallel', '2'],
            logs / 'build.log', env=env, timeout=7200)
        tests = test_variant(build, spec, logs, env, ctest)
        run([cmake, '--install', build, '--config', spec['config']], logs / 'install.log', env=env)
        # Retire the installation prefix so broken absolute exports cannot pass consumption.
        stage = area / 'stage-relocated'
        install.rename(stage)
        for license_file in ('LICENSE', 'AUTHORS'):
            shutil.copyfile(str(source / license_file), str(stage / license_file))
        for export in (stage / 'lib/cmake/leveldb').glob('*.cmake'):
            text = export.read_text(encoding='utf-8').replace('\\', '/')
            for forbidden in (source, build, install, stage):
                require(str(forbidden).replace('\\', '/') not in text, 'Non-relocatable CMake export')
            require(not re.search(r'(?i)\b(snappy|zstd|crc32c|tcmalloc)\b', text),
                    'Optional dependency leaked into CMake package')
        version = cache['CMAKE_PROJECT_VERSION']
        info = {'schema': 1, 'source_sha': sha, 'release_tag': tag, 'library_version': version,
                'asset': asset_name(spec, tag), 'variant': spec,
                'abi': {'exceptions': True, 'rtti': True, 'msvc_crt': spec['crt']},
                'optional_dependencies': OPTIONAL_POLICY,
                'tools': tools, 'source_submodules': submodules, 'tests': tests,
                'cmake_cache': {k: v for k, v in cache.items() if
                    k.startswith(('CMAKE_C_FLAGS', 'CMAKE_CXX_FLAGS')) or
                    k in ('CMAKE_MSVC_RUNTIME_LIBRARY', 'LEVELDB_WINDOWS_UTF8_PATHS')}}
        write_json(stage / 'BUILD-INFO.json', info)
        archive = root / 'artifacts' / asset_name(spec, tag)
        pack(stage, archive, name)
        inspect_package(archive, spec, tag, sha, consumer_required=False)
        tests['runtime_dependencies'] = consume(
            archive, source, area / 'candidate', flags, spec, env, logs / 'candidate', cmake)
        tests['archive_consumer'] = {'passed': True, 'checks': [
            'find_package', 'write-close-reopen-read-cleanup', 'exceptions-rtti'] +
            (['non-ascii-path', 'actual-LOG-visible'] if spec['os'] == 'windows' else [])}
        write_json(stage / 'BUILD-INFO.json', info)
        pack(stage, archive, name)
        _, info_hash = inspect_package(archive, spec, tag, sha)
        # Reconsume the final bytes, including the final BUILD-INFO. Only then attest in manifest.
        consume(archive, source, area / 'final', flags, spec, env, logs / 'final', cmake)
        return {'name': archive.name, 'size': archive.stat().st_size, 'sha256': sha256(archive),
                'build_info_sha256': info_hash, 'final_archive_consumer': True}, version
    finally:
        collect_diagnostics(build, logs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--family', required=True, choices=('linux', 'msvc', 'mingw', 'zig'))
    args = parser.parse_args()
    tag, sha = context()
    root = work_root()
    source = root / 'source'
    require(not source.exists(), 'Build workspace must be fresh')
    shutil.copytree(str(REPO), str(source), ignore=shutil.ignore_patterns('.git', '__pycache__'))
    submodules = subprocess.check_output(['git', 'submodule', 'status', '--recursive'],
                                        cwd=str(REPO), universal_newlines=True)
    require(all(line.startswith(' ') for line in submodules.splitlines()),
            'Submodules must be initialized at their pinned commits')
    arch = os.environ.get('RELEASE_ARCH') if args.family == 'linux' else None
    require(args.family != 'linux' or arch in ('x64', 'x86', 'arm64'), 'Invalid architecture')
    specs = variants(args.family, arch)
    require(specs, 'Empty release variant selection')
    artifacts = []
    version = None
    for spec in specs:
        entry, current_version = build_one(spec, tag, sha, source, root,
            os.environ['RELEASE_CMAKE'], os.environ['RELEASE_CTEST'], submodules)
        require(version is None or current_version == version, 'Variant library version mismatch')
        version = current_version
        artifacts.append(entry)
    identifier = args.family + ('-' + arch if arch else '')
    write_json(root / 'artifacts' / ('manifest-' + identifier + '.json'), {
        'schema': 1, 'source_sha': sha, 'release_tag': tag, 'library_version': version,
        'artifacts': artifacts})


if __name__ == '__main__':
    execute_main(main)
