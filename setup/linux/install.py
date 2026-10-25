#!/usr/bin/env python3
"""Native Linux orchestration of the existing, validated asset pipeline."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import sys
import urllib.request
import zipfile
from common import (ROOT, ER_ID, atomic_json, context, executable, platform_args,
                    proton_env, run, win_path, linux_path)

PACK_REQUIRED = ['manifest.json', 'sora.glb', 'materials/er/skin.vs.dxil', 'vfx/er/shaders.json',
                 'ui/manifest.json', 'audio/manifest.json', 'party/n_ex001/manifest.json', 'party/n_ex002/manifest.json']
DLL_FILES = ['kh_er.dll', 'kh_er_loader.dll', 'kh_er.schemas.json']


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    platform_args(p)
    p.add_argument('--kh3', help='KH3 Steam installation directory')
    p.add_argument('--dxc', help='Windows x64 dxc.exe (keep dxcompiler.dll and dxil.dll adjacent)')
    p.add_argument('--shaderc', help='native kh-shaderc or Windows kh-shaderc.exe; otherwise use release binary or Cargo')
    p.add_argument('--helper', help='Windows API helper built from setup/linux/windows-helper.c')
    p.add_argument('--dll-dir', help='Windows-built mod DLLs and kh_er.schemas.json; default er/mods')
    p.add_argument('--no-download', action='store_true')
    p.add_argument('--rebuild-pack', action='store_true')
    p.add_argument('--workers', type=int, default=max(1, min(4, (os.cpu_count() or 2)-1)))
    return p


def fingerprint(ctx):
    exe = Path(ctx['kh3']['gameRoot']) / ctx['kh3']['mainExe']
    def stamp(p):
        s = p.stat()
        return [str(p), s.st_size, s.st_mtime_ns]
    return {'kh3': stamp(exe), 'er': stamp(ctx['er']),
            'paks': [stamp(p) for p in sorted((Path(ctx['kh3']['gameRoot']) / ctx['kh3']['paksDir']).glob('*.pak'))]}


def resume_plan(state, identity, done, rebuild=False):
    if state and state.get('inputs') != identity:
        raise ValueError('Game inputs changed since setup. Use a fresh mod directory to avoid stale extracted caches.')
    return ['--resume'] if done and not rebuild else []


def pe64(file):
    with Path(file).open('rb') as stream:
        head = stream.read(64)
        if len(head) < 64 or head[:2] != b'MZ':
            raise ValueError(f'not a Windows PE executable: {file}')
        stream.seek(int.from_bytes(head[60:64], 'little'))
        header = stream.read(6)
        if header != b'PE\0\0\x64\x86':
            raise ValueError(f'not an x86-64 Windows PE: {file}')


def download_dxc(destination, pin):
    """TLS and pinned SHA-256 checked before extraction; no proprietary downloads."""
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination.parent / 'dxc-linux-download.zip'
    try:
        with urllib.request.urlopen(pin['url'], timeout=120) as response, archive.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != pin['sha256']:
            raise ValueError(f'DXC SHA-256 mismatch: {digest}; archive rejected')
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                target = (destination / member.filename).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise ValueError('unsafe DXC archive member')
            z.extractall(destination)
    finally:
        archive.unlink(missing_ok=True)


def helper_source_hash(root):
    return hashlib.sha256((root / 'setup/linux/windows-helper.c').read_bytes()).hexdigest()


def helper_needs_build(root):
    helper = root / 'tools/bin/kh-windows-helper.exe'
    stamp = helper.with_suffix('.source-sha256')
    return (not helper.is_file() or not stamp.is_file()
            or stamp.read_text().strip() != helper_source_hash(root))


def setup_overrides(value):
    # Replace only the compiler entry; preserve unrelated inherited or explicit overrides.
    entries = [entry for entry in (value or '').split(';') if entry.strip()]
    kept = []
    for entry in entries:
        names, sep, mode = entry.partition('=')
        names = [n.strip() for n in names.split(',') if n.strip().lower().removesuffix('.dll') != 'd3dcompiler_47']
        if names:
            kept.append(','.join(names) + (sep + mode if sep else ''))
    return ';'.join([*kept, 'd3dcompiler_47=n,b'])


def probe_disassembler(bridge, helper, env, ctx, log):
    fix = (f'Install Microsoft d3dcompiler_47 in the selected Elden Ring prefix: '
           f'protontricks {ER_ID} d3dcompiler_47 (ensure Protontricks targets {ctx["prefix"]}). '
           'Then rerun setup. A custom --helper must be rebuilt from current windows-helper.c.')
    try:
        output = run([*bridge, helper, 'probe'], env=env, log=log)
    except (OSError, RuntimeError) as error:
        raise ValueError(f'D3D disassembler preflight failed: {error}. {fix}') from error
    # Match the semantic-row grammar the material translator actually consumes.
    rows = re.findall(r'^// (\w+)\s+(\d+)\s+(\w+)\s+(\d+)\s+(\w+)\s+(\w+)\s*(.*?)\s*$', output, re.M)
    if not any(row[0] == 'TEXCOORD' and row[1] == '0' for row in rows) or not any(row[0] == 'SV_Position' for row in rows):
        raise ValueError(f'D3D disassembler output lacks translator-compatible input signatures. {fix}')
    paths = re.findall(r'^KH_COMPILER_PATH=(.+)$', output, re.M)
    if len(paths) != 1:
        raise ValueError(f'Disassembler helper did not identify its compiler DLL. {fix}')
    dll = linux_path(paths[0].strip(), ctx['prefix'])
    return {'dll_sha256': hashlib.sha256(dll.read_bytes()).hexdigest(),
            'proton': str(ctx['proton']), 'overrides': env['WINEDLLOVERRIDES'],
            'helper_sha256': hashlib.sha256(Path(helper).read_bytes()).hexdigest(),
            'proton_stamp': [ctx['proton'].stat().st_size, ctx['proton'].stat().st_mtime_ns]}


def refresh_shader_cache(root, identity, node, env, log):
    stamp = root / 'build/linux-disassembler.json'
    previous = json.loads(stamp.read_text()) if stamp.exists() else None
    journal = root / 'build/packgen/done_steps.json'
    pack_done = root / 'build/packgen/pack_done.json'
    cached = any((root / 'build/cache').glob('char_shaders*')) or (root / 'build/cache/vfx_shaders').exists()
    changed = previous != identity and (previous is not None or journal.exists() or pack_done.exists() or cached)
    if changed:
        # Resolve the current registry instead of hardcoding step ids. Keep expensive
        # extraction, conversion and research; rerun all consumers of shader assembly.
        listing = run([node, 'tools/packgen.js', '--list'], env=env, log=log)
        keep = {parts[1] for line in listing.splitlines()
                if len(parts := line.split()) == 2 and parts[0] in ('extract', 'convert', 'research')}
        if journal.exists():
            done = json.loads(journal.read_text())
            atomic_json(journal, {'steps': [s for s in done['steps'] if s in keep]})
        for folder in ['char_shaders', 'char_shaders_outfit', 'char_shaders_own', 'vfx_shaders']:
            for assembly in (root / 'build/cache' / folder).rglob('*.asm'):
                assembly.unlink()
        pack_done.unlink(missing_ok=True)
        (root / 'build/gen/rust_runtime/khg_schemas.rs').unlink(missing_ok=True)
        print('Disassembler changed or legacy shader cache detected: invalidated shader assembly; rerunning pack/replay/shaders/gen, retaining extraction/conversion/research.')
    atomic_json(stamp, identity)
    return changed


def requirements(args, root=ROOT):
    commands = ['node']
    if not all((root / 'node_modules' / name / 'package.json').is_file() for name in ['ajv', 'iced-x86', '@wasm-audio-decoders/ogg-vorbis', 'gltf-validator']):
        commands += ['npm']
    if not args.helper and helper_needs_build(root):
        commands += ['x86_64-w64-mingw32-gcc']
    if not args.shaderc and not (root / 'target/release/kh-shaderc').is_file() and not (root / 'tools/bin/kh-shaderc.exe').is_file():
        commands += ['cargo']
    return commands


def protect_games(ctx):
    games = [ctx['er'].parent.resolve()]
    if 'kh3' in ctx:
        games.append(Path(ctx['kh3']['gameRoot']).resolve())
    # Reject output aliases into games, not just textual path prefixes.
    outputs = [ROOT, *[ROOT / name for name in ['build', 'audit', 'assets', 'assets_converted', 'sheets', 'tools', 'er', 'node_modules', 'target']]]
    for base in list(outputs):
        if base.is_dir() and base != ROOT:
            for current, dirs, files in os.walk(base, followlinks=False):
                outputs.extend(Path(current) / n for n in dirs + files if (Path(current) / n).is_symlink())
    for output in outputs:
        if any(output.resolve().is_relative_to(game) for game in games):
            raise ValueError(f'mod output aliases original game files: {output}')


def check_er_version(command, env, log):
    try:
        output = run(command, env=env, log=log)
    except (RuntimeError, OSError) as error:
        print(f'WARNING: Unable to determine Elden Ring version ({error}). '
              'Continuing setup; confirm your executable is 2.7.1.0 or 2.7.2.0.')
        return
    versions = [line.strip() for line in output.splitlines()
                if re.fullmatch(r'\d+\.\d+\.\d+\.\d+', line.strip())]
    if len(set(versions)) != 1 or versions[0] == '0.0.0.0':
        print('WARNING: Unable to determine Elden Ring version from the helper output. '
              'Continuing setup; confirm your executable is 2.7.1.0 or 2.7.2.0.')
        return
    if versions[0] not in ('2.7.1.0', '2.7.2.0'):
        raise ValueError(f'unsupported Elden Ring version {versions[0]}; expected 2.7.1.0 or 2.7.2.0')


def main(argv=None):
    args = parser().parse_args(argv)
    if args.workers < 1:
        raise ValueError('--workers must be positive')
    ctx = context(args, need_kh=True)
    identity = fingerprint(ctx)
    state_file = ROOT / 'build/linux-install.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else None
    done = ROOT / 'build/packgen/done_steps.json'
    resume = resume_plan(state, identity, done.exists(), args.rebuild_pack)
    tools = {name: shutil.which(name) for name in requirements(args)}
    mods = ROOT / 'er/mods'
    dll_dir = Path(args.dll_dir).expanduser().resolve() if args.dll_dir else mods
    missing = [str(dll_dir / f) for f in DLL_FILES if not (dll_dir / f).is_file()]
    helper = Path(args.helper).expanduser().resolve() if args.helper else ROOT / 'tools/bin/kh-windows-helper.exe'
    shaderc = Path(args.shaderc).expanduser().resolve() if args.shaderc else next((p for p in [ROOT / 'target/release/kh-shaderc', ROOT / 'tools/bin/kh-shaderc.exe'] if p.is_file()), ROOT / 'target/release/kh-shaderc')
    dxc = Path(args.dxc).expanduser().resolve() if args.dxc else ROOT / 'tools/bin/dxc/bin/x64/dxc.exe'
    plan = {'platform': {k: v for k, v in ctx.items() if k != 'kh3'}, 'kh3': ctx['kh3'],
            'dependencies': tools, 'missing_windows_artifacts': missing, 'helper': helper, 'shaderc': shaderc,
            'dxc': dxc, 'download_dxc': not dxc.exists() and not args.no_download,
            'resume': bool(resume), 'pack_phases': ['extract', 'convert', 'research', 'pack', 'replay', 'shaders'],
            'validation': ['Elden Ring version', 'pack completeness', 'schema match', 'DLL self-check under Proton'],
            'compatibility': 'experimental; injection and gameplay unverified'}
    print(json.dumps(plan, default=str, indent=2))
    if args.dry_run:
        return 0
    protect_games(ctx)
    for name in tools:
        executable(name)
    if missing:
        raise ValueError('Windows-built artifacts required: ' + ', '.join(missing) + '. Use a release or --dll-dir; MSVC cross-build is not implemented.')
    for file in ['kh_er.dll', 'kh_er_loader.dll']:
        pe64(dll_dir / file)
    # Keep the Windows loader compatibility guard from selfcheck.ps1.
    loader = (dll_dir / 'kh_er_loader.dll').read_bytes()
    if b'2.7.1.0 or 2.7.2.0' not in loader or b'2.7.1.0 (app 1.17.1) only' in loader:
        raise ValueError('loader does not identify supported Elden Ring 2.7.1.0 / 2.7.2.0')
    schemas = json.loads((dll_dir / 'kh_er.schemas.json').read_text())['files']
    if not schemas:
        raise ValueError('empty DLL schema record')
    if dll_dir != mods:
        for f in DLL_FILES:
            target = mods / f
            if target.exists() and target.read_bytes() != (dll_dir / f).read_bytes():
                raise ValueError(f'{target} already differs; back it up and remove it before importing DLLs')
    env = proton_env(ctx['steam'], ctx['proton'], ctx['prefix'], ctx['er'].parent, args.dll_overrides)
    env['WINEDLLOVERRIDES'] = setup_overrides(env.get('WINEDLLOVERRIDES'))
    env.update(KH_LINUX_PROTON=str(ctx['proton']), KH_LINUX_PYTHON=sys.executable,
               KH_WINDOWS_HELPER=str(helper), KH_LINUX_DXC=str(dxc), KH_ER_GAME=str(ctx['er'].parent))
    log = ROOT / 'build/packgen/logs/linux-setup.log'
    env['PROTON_LOG_DIR'] = str(log.parent)
    node = executable('node')
    version = run([node, '-p', 'process.versions.node'], log=log).strip()
    if int(version.split('.')[0]) < 20:
        raise ValueError('Node.js 20+ required')
    packages = ['ajv', 'iced-x86', '@wasm-audio-decoders/ogg-vorbis', 'gltf-validator']
    if not all((ROOT / 'node_modules' / p / 'package.json').is_file() for p in packages):
        if args.no_download:
            raise ValueError('npm packages missing and --no-download was given; install locked packages with npm ci')
        run(['npm', 'ci', '--omit=dev', '--no-audit', '--no-fund'], log=log)
    src = ROOT / 'node_modules/@wasm-audio-decoders/ogg-vorbis/dist/ogg-vorbis-decoder.min.js'
    dst = ROOT / 'tools/third_party/ogg-vorbis-decoder/ogg-vorbis-decoder.min.js'
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(src, dst)
    if (not args.helper and helper_needs_build(ROOT)) or not helper.is_file():
        if args.helper:
            raise ValueError(f'--helper missing: {helper}')
        helper.parent.mkdir(parents=True, exist_ok=True)
        run(['x86_64-w64-mingw32-gcc', '-O2', '-Wall', '-Wextra', '-municode',
             ROOT / 'setup/linux/windows-helper.c', '-lversion', '-o', helper], log=log)
        helper.with_suffix('.source-sha256').write_text(helper_source_hash(ROOT) + '\n')
    pe64(helper)
    bridge = [sys.executable, ROOT / 'setup/linux/bridge.py']
    disassembler = probe_disassembler(bridge, helper, env, ctx, log)
    if refresh_shader_cache(ROOT, disassembler, node, env, log):
        resume = ['--resume'] if done.exists() and not args.rebuild_pack else []
    check_er_version([*bridge, helper, 'version', ctx['er']], env, log)
    if not dxc.is_file():
        if args.dxc or args.no_download:
            raise ValueError(f'DXC missing: {dxc}')
        download_dxc(ROOT / 'tools/bin/dxc', json.loads((ROOT / 'setup/dxc-pin.json').read_text()))
    pe64(dxc)
    for f in ['dxcompiler.dll', 'dxil.dll']:
        if not (dxc.parent / f).is_file():
            raise ValueError(f'DXC requires adjacent {f}')
    wrapper = ROOT / 'tools/bin/dxc-linux'
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    import shlex
    wrapper.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'setup/linux/bridge.py')) + ' --dxc "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    env.update(KH_DXC=str(wrapper), KH_SHADERC=str(shaderc))
    run([*bridge, dxc, '--version'], env=env, log=log)
    if not shaderc.is_file():
        if args.shaderc:
            raise ValueError(f'--shaderc missing: {shaderc}')
        run(['cargo', 'build', '--locked', *(['--offline'] if args.no_download else []), '--release', '-j', str(args.workers), '-p', 'kh-shaderc'], env=env, log=log)
    with shaderc.open('rb') as stream:
        magic = stream.read(4)
    if magic[:2] == b'MZ':
        pe64(shaderc)
        env['KH_LINUX_SHADERC'] = str(shaderc)
        shader_wrapper = ROOT / 'tools/bin/shaderc-linux'
        shader_wrapper.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'setup/linux/bridge.py')) + ' --shaderc "$@"\n')
        shader_wrapper.chmod(shader_wrapper.stat().st_mode | stat.S_IXUSR)
        env['KH_SHADERC'] = str(shader_wrapper)
    elif magic != b'\x7fELF' or not os.access(shaderc, os.X_OK):
        raise ValueError('--shaderc must be native Linux ELF or an x86-64 Windows PE')
    # Before any extraction, require the documented workspace budget.
    pack = ROOT / 'build/sora_pack'
    pack_done = ROOT / 'build/packgen/pack_done.json'
    complete = pack_done.exists() and all((pack / f).is_file() for f in PACK_REQUIRED)
    if (not complete or args.rebuild_pack) and shutil.disk_usage(ROOT).free < 80 * 1024**3:
        raise ValueError('Asset generation requires about 80 GiB free; move the mod checkout to a larger filesystem')
    atomic_json(ROOT / 'tools/config.json', ctx['kh3'])
    atomic_json(state_file, {'inputs': identity, 'status': 'in-progress'})
    # Keep the same key lookup, stamp, pack resume, generator and schema checks as Windows.
    exe = Path(ctx['kh3']['gameRoot']) / ctx['kh3']['mainExe']
    stamp = json.dumps(identity['kh3'])
    stamp_file = ROOT / 'audit/triage/linux-setup-exe.json'
    key_file = ROOT / 'audit/triage/pak_key.json'
    if not key_file.exists() or not stamp_file.exists() or stamp_file.read_text() != stamp:
        run([node, 'tools/pak_footers.js'], env=env, log=log)
        run([node, 'tools/pak_key.js'], env=env, log=log)
        stamp_file.parent.mkdir(parents=True, exist_ok=True)
        stamp_file.write_text(stamp)
    key = json.loads(key_file.read_text())
    if not key.get('key_file_offset'):
        raise ValueError('no verified pak key offset found')
    if key.get('verified_paks', 0) < key.get('total_paks', 0):
        print('WARNING: some pak indexes did not open; see audit/triage/pak_key.json')
    if args.rebuild_pack:
        done.unlink(missing_ok=True)
        pack_done.unlink(missing_ok=True)
    if not complete or args.rebuild_pack:
        run([node, 'tools/packgen.js', 'extract', 'convert', 'research', 'pack', 'replay', 'shaders',
             '--er', ctx['er'].parent, '--workers', str(args.workers), *resume], env=env, log=log)
        missing_pack = [f for f in PACK_REQUIRED if not (pack / f).is_file()]
        if missing_pack:
            raise ValueError('pack incomplete: ' + ', '.join(missing_pack))
        atomic_json(pack_done, {'done': True, 'kh3': str(exe)})
    gen = ROOT / 'build/gen/rust_runtime'
    if args.rebuild_pack or not (gen / 'khg_schemas.rs').is_file() or not (pack / 'gen/schemas.json').is_file():
        run([node, 'tools/packgen.js', 'gen'], env=env, log=log)
    have = json.loads((pack / 'gen/schemas.json').read_text())['files']
    mismatch = [f for f, schema in schemas.items() if have.get(f) != schema]
    if mismatch:
        raise ValueError('pack/DLL schema mismatch: ' + ', '.join(mismatch) + '; rebuild DLLs on Windows from these generated tables')
    mods.mkdir(parents=True, exist_ok=True)
    if dll_dir != mods:
        for f in DLL_FILES:
            if not (mods / f).exists():
                shutil.copy2(dll_dir / f, mods / f)
    for folder in ['saves', 'logs']:
        (ROOT / 'er' / folder).mkdir(parents=True, exist_ok=True)
    ini = mods / 'kh-er.ini'
    if not ini.exists():
        ctl = mods / 'kh-er.ctl'
        if ctl.exists():
            run([node, 'tools/ctl_to_ini.js', ctl], env=env, log=log)
        else:
            shutil.copy2(ROOT / 'setup/templates/kh-er.ini', ini)
    # Default pack location is shared by both platforms; do not overwrite existing custom cfg.
    cfg = mods / 'kh-er.cfg'
    if cfg.exists() and any(line.strip().startswith('pack=') for line in cfg.read_text().splitlines()):
        raise ValueError('custom er/mods/kh-er.cfg pack path exists; preserve it, or back it up and remove it to use this generated pack')
    run([*bridge, helper, 'selfcheck', mods], env=env, log=log)
    atomic_json(state_file, {'inputs': identity, 'status': 'complete', 'proton': str(ctx['proton'])})
    print('Asset installation and DLL self-check passed. Proton injection/gameplay remain unverified. Start Steam, then ./play-linux.sh --dry-run.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, KeyError, zipfile.BadZipFile) as error:
        print(f'Setup stopped: {error}\nFix the prerequisite and rerun; completed packgen steps are retained.', file=sys.stderr)
        sys.exit(1)
