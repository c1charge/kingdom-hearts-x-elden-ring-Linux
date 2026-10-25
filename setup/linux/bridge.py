#!/usr/bin/env python3
"""Launch a Windows tool under the installer's selected Proton, without a shell."""
import os
from pathlib import Path
import subprocess
import sys
from common import win_path


def command(exe, args, env):
    proton = env.get('KH_LINUX_PROTON')
    if not proton or not env.get('STEAM_COMPAT_DATA_PATH'):
        raise ValueError('Linux Windows-tool adapter needs setup-linux.sh Proton environment')
    return [proton, 'run', win_path(exe), *[win_path(a) if a.startswith('/') else a for a in args]]


def main(args):
    if not args:
        raise ValueError('missing Windows tool')
    exe, *rest = args
    env = dict(os.environ)
    if exe == '--dxc':
        exe = env['KH_LINUX_DXC']
    elif exe == '--shaderc':
        exe = env['KH_LINUX_SHADERC']
        # The Windows compiler must launch the real Windows DXC, not the Unix adapter.
        rest = [env['KH_LINUX_DXC'] if i and rest[i-1] == '--dxc' else arg for i, arg in enumerate(rest)]
        env['KH_DXC'] = win_path(env['KH_LINUX_DXC'])
        if env.get('KH_SHADERC_HLSL_DIR', '').startswith('/'):
            env['KH_SHADERC_HLSL_DIR'] = win_path(env['KH_SHADERC_HLSL_DIR'])
    proc = subprocess.run(command(str(Path(exe).resolve()), rest, env), env=env)
    return proc.returncode if proc.returncode >= 0 else 128 - proc.returncode


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError, KeyError) as error:
        print(f'Proton adapter: {error}', file=sys.stderr)
        sys.exit(1)
