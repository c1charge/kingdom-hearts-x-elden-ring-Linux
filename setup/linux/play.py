#!/usr/bin/env python3
"""Launch the mod with Windows me3 under an explicitly selected Proton."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib
from common import ROOT, atomic_json, context, platform_args, proton_env, run, win_path, executable
from install import pe64


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    platform_args(p)
    p.add_argument('--me3', help='Windows me3.exe from the full me3 >=0.13.0 Windows distribution')
    p.add_argument('--profile', help='source me3 profile; defaults to er/profiles/kh-sora.me3')
    return p


def version_ok(output):
    match = re.search(r'(?mi)^me3(?:\.exe)?[ \t]+v?(\d+)\.(\d+)\.(\d+)[ \t]*$', output)
    return bool(match and tuple(map(int, match.groups())) >= (0, 13, 0))


def check_me3_version(ctx, me3, env, logs):
    log = logs / 'linux-launch.log'
    version = run([ctx['proton'], 'run', win_path(me3), '--version'], env=env, log=log)
    if version_ok(version):
        return
    # If me3 printed a version, honor the result. Do not run a fallback to evade
    # an old/prerelease version; fallback is only for missing console output.
    if re.search(r'(?mi)^me3(?:\.exe)?[ \t]+', version):
        raise ValueError('me3 version is prerelease, older than 0.13.0, or unrecognized; refusing launch')
    source = ROOT / 'setup/linux/capture-version.c'
    helper = ROOT / 'tools/bin/kh-capture-version.exe'
    stamp = helper.with_suffix('.source-sha256')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if not helper.exists() or not stamp.exists() or stamp.read_text().strip() != digest:
        compiler = executable('x86_64-w64-mingw32-gcc')
        helper.parent.mkdir(parents=True, exist_ok=True)
        run([compiler, '-O2', '-Wall', '-Wextra', '-municode', source, '-o', helper], log=log)
        pe64(helper)
        stamp.write_text(digest + '\n')
    pe64(helper)
    logs.mkdir(parents=True, exist_ok=True)
    report = logs / 'me3-version.txt'
    if report.is_symlink():
        raise ValueError('refusing symlink for me3 version report')
    report.unlink(missing_ok=True)
    run([ctx['proton'], 'run', win_path(helper), win_path(me3), win_path(report)], env=env, log=log)
    if not report.is_file():
        raise ValueError('Proton produced no me3 version report; inspect er/logs/linux-launch.log')
    version = report.read_text(encoding='utf-8-sig', errors='replace')
    if not version_ok(version):
        raise ValueError('me3 version report is unknown, prerelease, or older than 0.13.0; inspect er/logs/me3-version.txt; refusing launch')
    print('Verified ' + version.strip())


def profile_text(source, root=ROOT):
    text = Path(source).read_text()
    data = tomllib.loads(text)
    save = data.get('savefile', '')
    if not isinstance(save, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.sl2', save, re.I) or save.lower() == 'er0000.sl2':
        raise ValueError('profile must specify a dedicated savefile basename such as KHSora.sl2; ER0000.sl2 is forbidden')
    if data.get('profileVersion') != 'v1' or data.get('start_online', False) is not False:
        raise ValueError('profile must be v1 and offline (start_online false or absent)')
    if not any(item.get('game') == 'eldenring' for item in data.get('supports', [])):
        raise ValueError('profile must support eldenring')
    natives = data.get('natives', [])
    paths = []
    for entry in natives:
        value = entry.get('path', '')
        if not value or re.match(r'^[A-Za-z]:', value):
            raise ValueError('source native paths must be host paths, relative to the source profile or absolute')
        paths.append((Path(source).parent / value).resolve())
    expected = (root / 'er/mods/kh_er_loader.dll').resolve()
    if expected not in paths:
        raise ValueError('profile must load this mod directory\'s er/mods/kh_er_loader.dll')
    # Write a separate profile; preserve the original and its dedicated save name. Every path
    # remains relative to the SOURCE profile, then becomes a quoted absolute Wine path.
    def replace(match):
        value = tomllib.loads('path = ' + match.group(2))['path']
        if re.match(r'^[A-Za-z]:', value):
            raise ValueError('use Linux paths in the source profile')
        path = (Path(source).parent / value).resolve()
        return match.group(1) + json.dumps(win_path(path), ensure_ascii=False) + match.group(3)
    text = re.sub(r'(?m)^([ \t]*path[ \t]*=[ \t]*)("(?:\\.|[^"\\])*"|\'[^\'\n]*\')([ \t]*(?:#[^\n]*)?)$', replace, text)
    if 'start_online' not in data:
        text = 'start_online = false\n' + text
    # Validate roundtrip: reject exotic/multiline path syntax instead of moving relative paths incorrectly.
    generated = tomllib.loads(text)
    for section in ['natives', 'packages']:
        for entry in generated.get(section, []):
            if 'path' in entry and not re.match(r'^[A-Za-z]:[\\/]', entry['path']):
                raise ValueError('profile paths must use ordinary single-line TOML strings')
    return text, save


def launch_plan(ctx, me3, profile, save):
    env = proton_env(ctx['steam'], ctx['proton'], ctx['prefix'], ctx['er'].parent)
    command = [str(ctx['proton']), 'waitforexitandrun', win_path(me3), 'launch', '-g', 'eldenring',
               '--exe', win_path(ctx['er']), '-p', win_path(profile), '--savefile', save]
    return command, env


def main(argv=None):
    args = parser().parse_args(argv)
    ctx = context(args)
    config_file = ROOT / 'er/linux-launch.json'
    saved = json.loads(config_file.read_text()) if config_file.exists() else {}
    me3 = args.me3 or saved.get('me3') or shutil.which('me3.exe')
    if not me3:
        raise ValueError('Windows me3.exe missing. Install its full >=0.13.0 distribution and use --me3 FILE')
    me3 = Path(me3).expanduser().resolve()
    if not me3.is_file():
        raise ValueError(f'me3 not found: {me3}')
    pe64(me3)
    source = Path(args.profile).expanduser().resolve() if args.profile else ROOT / 'er/profiles/kh-sora.me3'
    text, save = profile_text(source)
    profile = ROOT / 'er/profiles/kh-sora-linux.me3'
    command, env = launch_plan(ctx, me3, profile, save)
    if args.dll_overrides is not None:
        env['WINEDLLOVERRIDES'] = args.dll_overrides
    logs = ROOT / 'er/logs'
    env['PROTON_LOG_DIR'] = str(logs)
    plan = {'command': command, 'working_directory': str(ROOT / 'er'),
            'environment': {k: env[k] for k in ['STEAM_COMPAT_CLIENT_INSTALL_PATH', 'STEAM_COMPAT_DATA_PATH',
                'STEAM_COMPAT_INSTALL_PATH', 'SteamAppId', 'SteamGameId', 'PROTON_LOG', 'PROTON_LOG_DIR']},
            'profile': text, 'savefile': save, 'version_check': 'me3 >=0.13.0 required at execution',
            'offline': True, 'injection': 'unverified; inspect er/logs/kh-er.log after launch'}
    print(json.dumps(plan, indent=2))
    if args.dry_run:
        return 0
    from install import protect_games
    protect_games(ctx)
    for dll in ['kh_er.dll', 'kh_er_loader.dll']:
        pe64(ROOT / 'er/mods' / dll)
    state_file = ROOT / 'build/linux-install.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    if state.get('status') != 'complete':
        raise ValueError('run setup-linux.sh successfully first (pack/schema validation and DLL self-check required)')
    er_stat = ctx['er'].stat()
    if state.get('inputs', {}).get('er') != [str(ctx['er']), er_stat.st_size, er_stat.st_mtime_ns]:
        raise ValueError('Elden Ring changed since setup; validate a fresh installation before launching')
    # Steam must already be running. Do not invoke steam -applaunch or the EAC bootstrap.
    processes = Path('/proc')
    steam_running = False
    for p in processes.glob('[0-9]*/comm'):
        try:
            steam_running |= p.read_text().strip() == 'steam'
        except OSError:
            continue
    if not steam_running:
        raise ValueError('Start native Steam and sign in before launching the mod')
    check_me3_version(ctx, me3, env, logs)
    for output in [profile, config_file]:
        if output.is_symlink():
            raise ValueError(f'refusing to overwrite symlink: {output}')
    profile.write_text(text)
    atomic_json(config_file, {'me3': str(me3)})
    try:
        run(command, env=env, cwd=ROOT / 'er', log=logs / 'linux-launch.log')
    finally:
        print('Logs: er/logs/linux-launch.log, er/logs/steam-1245620.log, er/logs/kh-er.log. A successful process exit does not verify mod injection.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, KeyError, tomllib.TOMLDecodeError) as error:
        print(f'Launch stopped: {error}', file=sys.stderr)
        sys.exit(1)
