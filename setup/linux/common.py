"""Linux platform discovery and process support; standard library only."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ER_ID = '1245620'
KH_ID = '2552450'


def vdf(text):
    """Parse Valve's quoted/unquoted key-value format, including comments/escapes."""
    tokens = re.findall(r'//[^\n]*|"(?:\\.|[^"\\])*"|[{}]|[^\s{}"]+', text)
    tokens = [t for t in tokens if not t.startswith('//')]
    def unquote(t):
        if not t.startswith('"'):
            return t
        return re.sub(r'\\([\\"])', r'\1', t[1:-1])
    def obj(i, nested=False):
        out = {}
        while i < len(tokens):
            if tokens[i] == '}':
                if not nested:
                    raise ValueError('unexpected VDF closing brace')
                return out, i + 1
            key = unquote(tokens[i]); i += 1
            if i == len(tokens):
                raise ValueError('missing VDF value')
            if tokens[i] == '{':
                value, i = obj(i + 1, True)
            else:
                value = unquote(tokens[i]); i += 1
            out[key] = value
        if nested:
            raise ValueError('unterminated VDF object')
        return out, i
    return obj(0)[0]


def read_vdf(file):
    return vdf(Path(file).read_text(encoding='utf-8-sig'))


def unique_dirs(paths):
    return list(dict.fromkeys(Path(p).expanduser().resolve() for p in paths if Path(p).expanduser().is_dir()))


def steam_roots(explicit=None, home=None):
    home = Path(home or Path.home())
    if explicit:
        roots = unique_dirs([explicit])
        if not roots or not (roots[0] / 'steamapps').is_dir():
            raise ValueError('--steam must name the Steam directory containing steamapps')
        return roots
    return unique_dirs([home / '.local/share/Steam', home / '.steam/steam', home / '.steam/root'])


def libraries(roots, extra=()):
    found = list(roots) + list(extra)
    for root in roots:
        file = root / 'steamapps/libraryfolders.vdf'
        if file.exists():
            for key, value in read_vdf(file).get('libraryfolders', {}).items():
                if key.isdigit():
                    found.append(value.get('path', '') if isinstance(value, dict) else value)
    return unique_dirs(p for p in found if p)


def apps(libs):
    result = []
    for lib in libs:
        for manifest in sorted((lib / 'steamapps').glob('appmanifest_*.acf')):
            state = read_vdf(manifest).get('AppState', {})
            install = state.get('installdir', '')
            if not install or Path(install).is_absolute() or '..' in Path(install).parts:
                raise ValueError(f'invalid installdir in {manifest}')
            folder = lib / 'steamapps/common' / install
            if folder.is_dir():
                result.append({'id': state.get('appid'), 'name': state.get('name', ''), 'dir': folder.resolve(), 'library': lib})
    return result


def er_at(folder):
    p = Path(folder).expanduser().resolve()
    for exe in [p / 'Game/eldenring.exe', p / 'eldenring.exe', p]:
        if exe.name.lower() == 'eldenring.exe' and exe.is_file():
            return exe
    raise ValueError(f'no eldenring.exe at {p}; use --er DIR')


def kh_at(folder):
    p = Path(folder).expanduser().resolve()
    for root in [p, p.parent]:
        inner = root / 'KINGDOM HEARTS III'
        bin_dir, paks = inner / 'Binaries/Win64', inner / 'Content/Paks'
        exes = sorted(bin_dir.glob('*.exe'))
        exes = [e for e in exes if e.name.upper().startswith('KINGDOM HEARTS III') and 'shipping-cmd' not in e.name.lower()]
        if exes and any(paks.glob('*.pak')):
            return {'gameRoot': str(root), 'mainExe': str(exes[0].relative_to(root)), 'paksDir': str(paks.relative_to(root))}
    raise ValueError(f'no KH3 executable and Paks at {p}; use --kh3 DIR')


def find_game(installed, appid, override, check):
    if override:
        return check(override)
    for app in installed:
        if app['id'] == appid:
            return check(app['dir'])
    raise ValueError(f'Steam app {appid} not found; supply its game directory explicitly')


def protons(roots, libs, extra=None):
    search = [r / 'compatibilitytools.d' for r in roots]
    search += [Path('/usr/share/steam/compatibilitytools.d'), Path('/usr/local/share/steam/compatibilitytools.d')]
    search += [Path(p) for p in (extra if extra is not None else os.environ.get('STEAM_EXTRA_COMPAT_TOOLS_PATHS', '').split(':')) if p]
    search += [lib / 'steamapps/common' for lib in libs]
    candidates = []
    for folder in search:
        candidates += [folder / 'proton'] + [p / 'proton' for p in folder.glob('*') if p.is_dir()]
    found = list(dict.fromkeys(p.resolve() for p in candidates if p.is_file() and os.access(p, os.X_OK)))
    # Prefer latest GE naturally (GE-Proton10-10 follows GE-Proton10-9), then official versions.
    def order(p):
        return ('ge' in p.parent.name.lower(), tuple((1, int(x)) if x.isdigit() else (0, x.lower()) for x in re.split(r'(\d+)', p.parent.name)))
    return sorted(found, key=order, reverse=True)


def select_proton(explicit, roots, libs):
    if explicit:
        p = Path(explicit).expanduser().resolve()
        p = p / 'proton' if p.is_dir() else p
        if not p.is_file() or not os.access(p, os.X_OK):
            raise ValueError('--proton must be an executable Proton script or its directory')
        return p
    found = protons(roots, libs)
    if not found:
        raise ValueError('No Proton found; install GE-Proton or Steam Proton, or use --proton FILE')
    return found[0]


def win_path(value):
    """Absolute host path via Proton's standard Z: mapping; preserve existing DOS paths."""
    value = str(value)
    if re.match(r'^[a-zA-Z]:[\\/]', value) or value.startswith('\\\\'):
        return value.replace('/', '\\')
    if not value.startswith('/'):
        raise ValueError(f'expected absolute Linux or Windows path: {value}')
    return 'Z:' + value.replace('/', '\\')


def linux_path(value, prefix):
    value = str(value).replace('\\', '/')
    if value.startswith('/'):
        return Path(value)
    match = re.match(r'^([A-Za-z]):/(.*)$', value)
    if not match:
        raise ValueError(f'not an absolute DOS path: {value}')
    drive, rest = match.groups()
    if drive.lower() == 'z':
        return Path('/') / rest
    link = Path(prefix) / 'pfx/dosdevices' / (drive.lower() + ':')
    if not link.exists():
        raise ValueError(f'unmapped Wine drive {drive}:')
    return link.resolve() / rest


def prefix_for(libs, er_exe, override=None):
    if override:
        p = Path(override).expanduser().resolve()
        if p.name == 'pfx':
            raise ValueError('--prefix is the compatdata directory containing pfx, not pfx itself')
    else:
        owning = next((lib for lib in libs if er_exe.is_relative_to(lib)), None)
        candidates = ([owning] if owning else []) + list(libs)
        p = next((lib / f'steamapps/compatdata/{ER_ID}' for lib in candidates if (lib / f'steamapps/compatdata/{ER_ID}/pfx').is_dir()), None)
    if not p or not (p / 'pfx').is_dir():
        raise ValueError('Elden Ring Proton prefix missing. Run it once through Steam, or give --prefix COMPATDATA_DIR')
    return p


def proton_env(steam, proton, prefix, game, overrides=None):
    result = dict(os.environ)
    result.update(STEAM_COMPAT_CLIENT_INSTALL_PATH=str(steam), STEAM_COMPAT_DATA_PATH=str(prefix),
                  STEAM_COMPAT_INSTALL_PATH=str(game), SteamAppId=ER_ID, SteamGameId=ER_ID,
                  STEAM_COMPAT_APP_ID=ER_ID, PROTON_LOG='1')
    if overrides is not None:
        result['WINEDLLOVERRIDES'] = overrides
    return result


def executable(name):
    found = shutil.which(str(name))
    if not found:
        raise ValueError(f'Missing dependency: {name}')
    return str(Path(found).resolve())


def atomic_json(file, data):
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_suffix(file.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(file)


def run(command, env=None, cwd=ROOT, log=None):
    command = list(map(str, command))
    print('>', json.dumps(command), flush=True)
    stream = None
    try:
        if log:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            stream = Path(log).open('a')
            stream.write(json.dumps(command) + '\n')
            stream.flush()
        chunks = []
        with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace') as proc:
            for line in proc.stdout:
                chunks.append(line)
                print(line, end='', flush=True)
                if stream:
                    stream.write(line)
                    stream.flush()
            code = proc.wait()
        if code:
            raise RuntimeError(f'{Path(command[0]).name} exited {code}; see {log or "output above"}')
        return ''.join(chunks)
    finally:
        if stream:
            stream.close()



def platform_args(parser):
    parser.add_argument('--steam', help='Steam root directory (not executable)')
    parser.add_argument('--library', action='append', default=[], help='additional Steam library; repeatable')
    parser.add_argument('--er', help='Elden Ring install, Game directory or executable')
    parser.add_argument('--proton', help='Proton executable or directory; default newest installed GE, then official')
    parser.add_argument('--prefix', help='Elden Ring compatdata directory containing pfx')
    parser.add_argument('--dll-overrides', help='process-local WINEDLLOVERRIDES; default preserve environment')
    parser.add_argument('--dry-run', action='store_true', help='inspect and print plan without writes, downloads or subprocesses')


def context(args, need_kh=False):
    roots = steam_roots(args.steam)
    if not roots:
        raise ValueError('Native Steam not found; use --steam DIR (Flatpak Steam is not supported)')
    libs = libraries(roots, args.library)
    installed = apps(libs)
    er = find_game(installed, ER_ID, args.er, er_at)
    proton = select_proton(args.proton, roots, libs)
    prefix = prefix_for(libs, er, args.prefix)
    out = dict(steam=roots[0], libraries=libs, er=er, proton=proton, prefix=prefix)
    if need_kh:
        out['kh3'] = find_game(installed, KH_ID, args.kh3, kh_at)
    return out
