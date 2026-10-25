"""No games, network, Wine, or root needed: python3 -m unittest discover -s tests -v."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'setup/linux'))
import common
import bridge
import install
import play


def touch(file, data=b''):
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)
    return file


def pe(file):
    return touch(file, b'MZ' + bytes(58) + (64).to_bytes(4, 'little') + b'PE\0\0\x64\x86')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='KH Linux tests ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'
        self.steam = self.home / '.local/share/Steam'
        self.lib = self.base / 'Additional Steam Library'
        (self.steam / 'steamapps').mkdir(parents=True)
        (self.lib / 'steamapps').mkdir(parents=True)
        (self.steam / 'steamapps/libraryfolders.vdf').write_text('"libraryfolders" { "0" { "path" ' + json.dumps(str(self.steam)) + ' } "1" { "path" ' + json.dumps(str(self.lib)) + ' } }')
        self.er = self.app(common.ER_ID, 'ELDEN RING')
        self.kh = self.app(common.KH_ID, 'KINGDOM HEARTS III')
        touch(self.er / 'Game/eldenring.exe')
        touch(self.kh / 'KINGDOM HEARTS III/Binaries/Win64/KINGDOM HEARTS III.exe')
        touch(self.kh / 'KINGDOM HEARTS III/Content/Paks/game.pak')
        self.prefix = self.lib / f'steamapps/compatdata/{common.ER_ID}'
        (self.prefix / 'pfx/dosdevices').mkdir(parents=True)
        (self.prefix / 'pfx/drive_c').mkdir()
        (self.prefix / 'pfx/dosdevices/c:').symlink_to('../drive_c')
        self.proton = self.proton_at(self.steam / 'compatibilitytools.d/GE-Proton10-9')
        self.root = self.base / 'mod folder'
        (self.root / 'er/profiles').mkdir(parents=True)
        self.profile = self.root / 'er/profiles/kh-sora.me3'
        self.profile.write_text('profileVersion = "v1"\nsavefile = "KHSora.sl2"\n[[supports]]\ngame = "eldenring"\n[[natives]]\npath = \'../mods/kh_er_loader.dll\'\n')

    def app(self, appid, name):
        (self.lib / f'steamapps/appmanifest_{appid}.acf').write_text(f'"AppState" {{ "appid" "{appid}" "name" "{name}" "installdir" "{name}" }}')
        folder = self.lib / 'steamapps/common' / name
        folder.mkdir(parents=True)
        return folder

    def proton_at(self, folder):
        file = touch(folder / 'proton', b'#!/bin/sh\nexit 99\n')
        file.chmod(0o755)
        return file

    def args(self):
        return ['--steam', str(self.steam), '--proton', str(self.proton)]

    def ctx(self):
        return common.context(install.parser().parse_args(self.args()), need_kh=True)

    def test_standard_steam_and_additional_library(self):
        roots = common.steam_roots(home=self.home)
        self.assertEqual(roots, [self.steam])
        self.assertEqual(common.libraries(roots), [self.steam, self.lib])
        installed = common.apps(common.libraries(roots))
        self.assertEqual({a['id'] for a in installed}, {common.ER_ID, common.KH_ID})

    def test_symlink_steam_roots_deduplicate(self):
        link = self.home / '.steam/steam'
        link.parent.mkdir()
        link.symlink_to(self.steam)
        self.assertEqual(common.steam_roots(home=self.home), [self.steam])

    def test_legacy_libraries_and_explicit_extra(self):
        (self.steam / 'steamapps/libraryfolders.vdf').write_text('"libraryfolders" { "0" "' + str(self.lib) + '" }')
        self.assertEqual(common.libraries([self.steam], [self.lib]), [self.steam, self.lib])

    def test_vdf_escapes_comments_unquoted(self):
        self.assertEqual(common.vdf('// ignored\nroot { key "C:\\\\Games" }'), {'root': {'key': 'C:\\Games'}})
        with self.assertRaises(ValueError):
            common.vdf('root { key value')

    def test_discover_games_and_prefix_with_spaces(self):
        ctx = self.ctx()
        self.assertEqual(ctx['er'], self.er / 'Game/eldenring.exe')
        self.assertEqual(ctx['kh3']['gameRoot'], str(self.kh))
        self.assertEqual(ctx['prefix'], self.prefix)

    def test_manual_game_overrides(self):
        args = install.parser().parse_args(self.args() + ['--er', str(self.er / 'Game'), '--kh3', str(self.kh / 'KINGDOM HEARTS III')])
        self.assertEqual(common.context(args, True)['kh3']['gameRoot'], str(self.kh))

    def test_missing_game_dependency_errors(self):
        with self.assertRaisesRegex(ValueError, 'eldenring.exe'):
            common.er_at(self.base)
        with self.assertRaisesRegex(ValueError, 'KH3'):
            common.kh_at(self.base)
        with patch('common.shutil.which', return_value=None), self.assertRaisesRegex(ValueError, 'Missing dependency'):
            common.executable('npm')

    def test_invalid_steam_manifest_path(self):
        (self.lib / f'steamapps/appmanifest_{common.ER_ID}.acf').write_text('AppState { installdir "../outside" }')
        with self.assertRaisesRegex(ValueError, 'installdir'):
            common.apps([self.lib])

    def test_proton_ge_natural_order_and_official(self):
        latest = self.proton_at(self.steam / 'compatibilitytools.d/GE-Proton10-10')
        official = self.proton_at(self.lib / 'steamapps/common/Proton 10.0')
        found = common.protons([self.steam], [self.lib], extra=[])
        self.assertEqual(found[:2], [latest, self.proton])
        self.assertIn(official, found)
        self.assertEqual(common.select_proton(official.parent, [], []), official)

    def test_extra_proton_search_path(self):
        extra = self.base / 'custom tools'
        proton = self.proton_at(extra / 'GE-Proton11-1')
        self.assertEqual(common.protons([], [], extra=[str(extra)]), [proton])

    def test_concurrent_proton_tools_have_isolated_log_files(self):
        from concurrent.futures import ThreadPoolExecutor
        fake_proton = self.base / 'fake proton'
        fake_proton.write_text('#!' + sys.executable + '\n'
            'import os, pathlib, time\n'
            'folder=pathlib.Path(os.environ["PROTON_LOG_DIR"])\n'
            'assert os.environ["STEAM_COMPAT_DATA_PATH"]\n'
            'with (folder/"steam-1245620.log").open("x") as f: f.write("diagnostic")\n'
            'time.sleep(0.05)\n')
        fake_proton.chmod(0o755)
        log_base = self.base / 'logs with spaces'
        env = dict(os.environ, KH_LINUX_PROTON=str(fake_proton),
                   STEAM_COMPAT_DATA_PATH=str(self.prefix), PROTON_LOG='1', PROTON_LOG_DIR=str(log_base))
        cmd = [sys.executable, str(Path(bridge.__file__)), str(self.base / 'shaderc.exe')]
        def invoke(_):
            return subprocess.run(cmd, env=env, capture_output=True, text=True)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(invoke, range(6)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        logs = list((log_base / 'proton-tools').glob('tool-*/steam-1245620.log'))
        self.assertEqual(len(logs), 6)
        self.assertTrue(all(log.read_text() == 'diagnostic' for log in logs))
        self.assertFalse((log_base / 'steam-1245620.log').exists())
        self.assertEqual(env['PROTON_LOG_DIR'], str(log_base))

    def test_invalid_proton(self):
        with self.assertRaisesRegex(ValueError, 'executable'):
            common.select_proton(self.base / 'missing', [], [])

    def test_missing_prefix_is_actionable(self):
        shutil.rmtree(self.prefix)
        with self.assertRaisesRegex(ValueError, 'Run it once'):
            common.prefix_for([self.lib], self.er / 'Game/eldenring.exe')

    def test_prefix_override_requires_compatdata(self):
        with self.assertRaisesRegex(ValueError, 'not pfx'):
            common.prefix_for([], self.er, self.prefix / 'pfx')

    def test_windows_linux_path_conversion(self):
        self.assertEqual(common.win_path('/games/With Spaces/a.exe'), 'Z:\\games\\With Spaces\\a.exe')
        self.assertEqual(common.win_path('C:/Program Files/a.exe'), 'C:\\Program Files\\a.exe')
        self.assertEqual(common.linux_path('Z:\\games\\With Spaces', self.prefix), Path('/games/With Spaces'))
        self.assertEqual(common.linux_path('C:\\users\\steamuser', self.prefix), self.prefix / 'pfx/drive_c/users/steamuser')
        with self.assertRaises(ValueError):
            common.win_path('relative')

    def test_proton_command_uses_argument_array(self):
        env = {'KH_LINUX_PROTON': str(self.proton), 'STEAM_COMPAT_DATA_PATH': str(self.prefix)}
        command = bridge.command('/mod/dxc.exe', ['-Fo', '/output/a b.dxil', '/input/$x.hlsl'], env)
        self.assertEqual(command, [str(self.proton), 'run', 'Z:\\mod\\dxc.exe', '-Fo', 'Z:\\output\\a b.dxil', 'Z:\\input\\$x.hlsl'])
        with self.assertRaisesRegex(ValueError, 'environment'):
            bridge.command('/tool.exe', [], {})

    def test_argument_handling(self):
        for argv in [['--kh3'], ['--unknown'], ['--workers', 'abc']]:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                install.parser().parse_args(argv)
            self.assertEqual(caught.exception.code, 2)
        self.assertEqual(install.parser().parse_args(['--kh3', '/a b']).kh3, '/a b')

    def test_resume_after_failure_and_input_change(self):
        identity = install.fingerprint(self.ctx())
        self.assertEqual(install.resume_plan({'inputs': identity, 'status': 'in-progress'}, identity, True), ['--resume'])
        self.assertEqual(install.resume_plan({'inputs': identity}, identity, True, True), [])
        self.assertEqual(install.resume_plan(None, identity, False), [])
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            install.resume_plan({'inputs': {}}, identity, True)

    def test_windows_shaderc_adapter_keeps_dxc_windows_native(self):
        env = {'KH_LINUX_PROTON': str(self.proton), 'STEAM_COMPAT_DATA_PATH': str(self.prefix),
               'KH_LINUX_SHADERC': '/mod/kh-shaderc.exe', 'KH_LINUX_DXC': '/mod/dxc.exe',
               'KH_DXC': '/mod/dxc-linux', 'KH_SHADERC_HLSL_DIR': '/output/hlsl dir'}
        with patch.dict(os.environ, env, clear=True), patch('bridge.subprocess.run') as mocked:
            mocked.return_value.returncode = 0
            self.assertEqual(bridge.main(['--shaderc', '--pack', '/my pack', '--dxc', '/mod/dxc-linux']), 0)
        args, kwargs = mocked.call_args
        self.assertIn('Z:\\mod\\dxc.exe', args[0])
        self.assertNotIn('Z:\\mod\\dxc-linux', args[0])
        self.assertEqual(kwargs['env']['KH_SHADERC_HLSL_DIR'], 'Z:\\output\\hlsl dir')

    def test_missing_build_dependencies(self):
        args = install.parser().parse_args([])
        self.assertEqual(install.requirements(args, self.root), ['node', 'npm', 'x86_64-w64-mingw32-gcc', 'cargo'])

    def test_profile_generation_preserves_save_and_source(self):
        before = self.profile.read_bytes()
        text, save = play.profile_text(self.profile, self.root)
        data = tomllib.loads(text)
        self.assertEqual(save, 'KHSora.sl2')
        self.assertIs(data['start_online'], False)
        self.assertEqual(data['natives'][0]['path'], common.win_path(self.root / 'er/mods/kh_er_loader.dll'))
        self.assertEqual(self.profile.read_bytes(), before)

    def test_reject_normal_save_traversal_and_online_profiles(self):
        for save in ['ER0000.sl2', '../KHSora.sl2', 'C:\\\\ER0000.sl2', 'bad.txt', '']:
            self.profile.write_text(self.profile.read_text().replace('KHSora.sl2', save))
            with self.assertRaises(ValueError):
                play.profile_text(self.profile, self.root)
            self.profile.write_text('profileVersion="v1"\nsavefile="KHSora.sl2"\n[[supports]]\ngame="eldenring"\n[[natives]]\npath="../mods/kh_er_loader.dll"\n')
        self.profile.write_text('start_online=true\n' + self.profile.read_text())
        with self.assertRaisesRegex(ValueError, 'offline'):
            play.profile_text(self.profile, self.root)

    def test_er_version_supported_with_proton_diagnostics(self):
        with patch('install.run', return_value='Proton: preparing prefix\n2.7.2.0\n'), contextlib.redirect_stdout(io.StringIO()) as output:
            install.check_er_version(['helper', 'version'], {}, None)
        self.assertNotIn('WARNING', output.getvalue())

    def test_er_version_unknown_warns_and_continues(self):
        for result in ['', 'cannot read executable version', '0.0.0.0']:
            with self.subTest(result=result), patch('install.run', return_value=result), contextlib.redirect_stdout(io.StringIO()) as output:
                install.check_er_version(['helper', 'version'], {}, None)
                self.assertIn('Continuing setup', output.getvalue())

    def test_er_version_probe_failure_warns_and_continues(self):
        for error in [RuntimeError('helper exited 1'), OSError('Proton unavailable')]:
            with self.subTest(error=error), patch('install.run', side_effect=error), contextlib.redirect_stdout(io.StringIO()) as output:
                install.check_er_version(['helper', 'version'], {}, None)
                self.assertIn('Unable to determine', output.getvalue())

    def test_er_version_known_unsupported_still_stops(self):
        with patch('install.run', return_value='2.6.0.0\n'), self.assertRaisesRegex(ValueError, 'unsupported Elden Ring version 2.6.0.0'):
            install.check_er_version(['helper', 'version'], {}, None)

    def test_me3_versions(self):
        for version in ['me3 0.13.0', 'me3 0.14.1', 'me3 1.0.0']:
            self.assertTrue(play.version_ok(version), version)
        for version in ['me3 0.12.9', 'unknown', 'me3 0.13.0-beta.1', 'wine 9.0.0\nme3 0.12.9', '0.13.0']:
            self.assertFalse(play.version_ok(version), version)

    def test_launch_plan_no_eac_bootstrap_or_steam_changes(self):
        command, env = play.launch_plan(self.ctx(), self.base / 'me3 folder/me3.exe', self.profile, 'KHSora.sl2')
        self.assertEqual(command[:2], [str(self.proton), 'waitforexitandrun'])
        self.assertIn('--exe', command)
        self.assertIn('Z:' + str(self.er / 'Game/eldenring.exe').replace('/', '\\'), command)
        self.assertEqual(command[-2:], ['--savefile', 'KHSora.sl2'])
        self.assertEqual(env['STEAM_COMPAT_DATA_PATH'], str(self.prefix))
        self.assertNotIn('start_protected_game.exe', ' '.join(command))
        self.assertNotIn('-applaunch', command)

    def test_installer_dry_run_no_subprocess_or_writes(self):
        with patch.object(install, 'ROOT', self.root), patch('install.run', side_effect=AssertionError('must not execute')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(install.main(self.args() + ['--dry-run']), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(len(plan['missing_windows_artifacts']), 3)
        self.assertFalse((self.root / 'build').exists())

    def test_launcher_dry_run_does_not_probe_or_launch(self):
        me3 = pe(self.base / 'Me3 folder/me3.exe')
        with patch.object(play, 'ROOT', self.root), patch('play.profile_text', return_value=('start_online=false', 'KHSora.sl2')), patch('play.run', side_effect=AssertionError('must not execute')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(play.main(self.args() + ['--dry-run', '--me3', str(me3)]), 0)
        self.assertFalse((self.root / 'er/linux-launch.json').exists())
        self.assertFalse((self.root / 'er/logs').exists())

    def test_output_symlink_into_game_is_rejected(self):
        (self.root / 'build').symlink_to(self.er / 'Game')
        with patch.object(install, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'original game'):
            install.protect_games(self.ctx())

    def test_nested_output_symlink_into_game_is_rejected(self):
        (self.root / 'build').mkdir()
        (self.root / 'build/result').symlink_to(self.er / 'Game/eldenring.exe')
        with patch.object(install, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'original game'):
            install.protect_games(self.ctx())

    def test_pe_validation(self):
        install.pe64(pe(self.base / 'valid.exe'))
        with self.assertRaisesRegex(ValueError, 'Windows PE'):
            install.pe64(touch(self.base / 'not.exe', b'not a PE'))

    def test_dxc_archive_verification(self):
        archive = self.base / 'official.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('bin/x64/dxc.exe', b'fixture')
        data = archive.read_bytes()
        dest = self.base / 'dxc'
        with patch('install.urllib.request.urlopen', return_value=io.BytesIO(data)):
            install.download_dxc(dest, {'url': 'https://official.invalid', 'sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual((dest / 'bin/x64/dxc.exe').read_bytes(), b'fixture')
        with patch('install.urllib.request.urlopen', return_value=io.BytesIO(b'bad')), self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            install.download_dxc(self.base / 'bad', {'url': 'https://official.invalid', 'sha256': '0'*64})
        self.assertFalse((self.base / 'dxc-linux-download.zip').exists())

    def test_install_failure_then_resume_uses_existing_pipeline(self):
        # Exercise actual installer filesystem/state transitions, substituting only external
        # commands and available space. No game content is fabricated or used by the tools.
        for name in install.DLL_FILES:
            target = self.root / 'er/mods' / name
            if name.endswith('.dll'):
                pe(target)
                with target.open('ab') as stream:
                    stream.write(b'2.7.1.0 or 2.7.2.0')
            else:
                touch(target, b'{"files":{"sora_anim":"schema"}}')
        helper = pe(self.root / 'tools/bin/helper.exe')
        dxc = pe(self.root / 'tools/bin/dxc/bin/x64/dxc.exe')
        for name in ['dxcompiler.dll', 'dxil.dll']:
            pe(dxc.parent / name)
        shaderc = touch(self.root / 'tools/bin/native-shaderc', b'\x7fELFfixture')
        shaderc.chmod(0o755)
        for name in ['ajv', 'iced-x86', '@wasm-audio-decoders/ogg-vorbis', 'gltf-validator']:
            touch(self.root / 'node_modules' / name / 'package.json', b'{}')
        touch(self.root / 'node_modules/@wasm-audio-decoders/ogg-vorbis/dist/ogg-vorbis-decoder.min.js')
        touch(self.root / 'setup/templates/kh-er.ini')
        before = {p: p.read_bytes() for root in [self.er, self.kh] for p in root.rglob('*') if p.is_file()}
        fail_pack = True
        probe_ok = False
        commands = []
        def fake_run(command, **kwargs):
            nonlocal fail_pack
            cmd = list(map(str, command)); commands.append(cmd)
            if '-p' in cmd and 'process.versions.node' in cmd:
                return '24.19.0\n'
            if 'version' in cmd:
                return '2.7.2.0\n'
            if 'probe' in cmd:
                if not probe_ok:
                    Path(cmd[-1]).write_text('ps_5_0\ndcl_input_ps v0.xy\n')
                else:
                    Path(cmd[-1]).write_text('KH_COMPILER_PATH=' + common.win_path(dxc.parent / 'dxcompiler.dll') + '\n// TEXCOORD 0 xy 0 NONE float xy\n// SV_Position 0 xyzw 1 POS float xy\n')
                return 'ProtonFixes: skipping fixes\n'
            if 'tools/pak_key.js' in cmd:
                common.atomic_json(self.root / 'audit/triage/pak_key.json', {'key_file_offset': 42, 'verified_paks': 1, 'total_paks': 1})
            if 'tools/packgen.js' in cmd and 'extract' in cmd:
                if fail_pack:
                    common.atomic_json(self.root / 'build/packgen/done_steps.json', {'steps': ['extract']})
                    fail_pack = False
                    raise RuntimeError('simulated converter failure')
                for file in install.PACK_REQUIRED:
                    touch(self.root / 'build/sora_pack' / file)
            if 'tools/packgen.js' in cmd and 'gen' in cmd:
                touch(self.root / 'build/gen/rust_runtime/khg_schemas.rs')
                common.atomic_json(self.root / 'build/sora_pack/gen/schemas.json', {'files': {'sora_anim': 'schema'}})
            return ''
        args = self.args() + ['--helper', str(helper), '--dxc', str(dxc), '--shaderc', str(shaderc)]
        with patch.object(install, 'ROOT', self.root), patch('install.run', side_effect=fake_run), patch('install.shutil.disk_usage', return_value=type('Space', (), {'free': 100 * 1024**3})()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'translator-compatible input signatures'):
                install.main(args)
            self.assertFalse(any('tools/packgen.js' in c or 'tools/pak_key.js' in c for c in commands))
            self.assertFalse((self.root / 'build/linux-install.json').exists())
            probe_ok = True
            with self.assertRaisesRegex(RuntimeError, 'converter failure'):
                install.main(args)
            self.assertEqual(json.loads((self.root / 'build/linux-install.json').read_text())['status'], 'in-progress')
            self.assertEqual(install.main(args), 0)
        self.assertEqual(json.loads((self.root / 'build/linux-install.json').read_text())['status'], 'complete')
        pack_runs = [c for c in commands if 'tools/packgen.js' in c and 'extract' in c]
        self.assertNotIn('--resume', pack_runs[0])
        self.assertIn('--resume', pack_runs[1])
        self.assertTrue(any('selfcheck' in c for c in commands))
        self.assertEqual(before, {p: p.read_bytes() for root in [self.er, self.kh] for p in root.rglob('*') if p.is_file()})

    def test_setup_override_preserves_other_dlls_and_does_not_change_parent(self):
        original = 'dxgi=b;d3dcompiler_47,winhttp=b;dinput8=n'
        self.assertEqual(install.setup_overrides(original), 'dxgi=b;winhttp=b;dinput8=n;d3dcompiler_47=n,b')
        self.assertEqual(install.setup_overrides('D3DCOMPILER_47.dll=b'), 'd3dcompiler_47=n,b')
        with patch.dict(os.environ, {'WINEDLLOVERRIDES': original}):
            install.setup_overrides(os.environ['WINEDLLOVERRIDES'])
            self.assertEqual(os.environ['WINEDLLOVERRIDES'], original)

    def test_probe_checks_actual_signature_format_and_dll_identity(self):
        dll = touch(self.base / 'compiler.dll', b'compiler fixture')
        helper = touch(self.base / 'helper.exe', b'helper fixture')
        output = 'KH_COMPILER_PATH=' + common.win_path(dll) + '\n// TEXCOORD 0 xy 0 NONE float xy\n// SV_Position 0 xyzw 1 POS float xy\n'
        report = self.base / 'probe.txt'
        def silent_report(command, **kwargs):
            Path(command[-1]).write_text(output)
            return 'ProtonFixes WARN: Skipping fix execution.\n'
        with patch('install.run', side_effect=silent_report):
            identity = install.probe_disassembler([], helper, {'WINEDLLOVERRIDES': 'd3dcompiler_47=n,b'}, self.ctx(), None, report=report)
        self.assertEqual(identity['dll_sha256'], hashlib.sha256(dll.read_bytes()).hexdigest())
        for bad in ['ps_5_0\ndcl_input_ps v0.xy', '// TEXCOORD 0 xy 0 NONE float xy']:
            def bad_report(command, **kwargs):
                Path(command[-1]).write_text(bad)
                return ''
            with patch('install.run', side_effect=bad_report), self.assertRaisesRegex(ValueError, 'protontricks 1245620 d3dcompiler_47'):
                install.probe_disassembler([], helper, {}, self.ctx(), None, report=report)

    def test_probe_does_not_accept_stale_report_or_blame_silent_output_on_dll(self):
        report = touch(self.base / 'probe.txt', b'old report')
        with patch('install.run', return_value='ProtonFixes warning only'), self.assertRaisesRegex(ValueError, 'without producing.*report'):
            install.probe_disassembler([], 'helper.exe', {}, self.ctx(), None, report=report)
        self.assertFalse(report.exists())

    def test_probe_failure_gives_prefix_specific_remediation(self):
        with patch('install.run', side_effect=RuntimeError('export missing')), self.assertRaisesRegex(ValueError, str(self.prefix)):
            install.probe_disassembler([], 'helper.exe', {}, self.ctx(), None, report=self.base / 'probe.txt')

    def test_shader_cache_change_invalidates_consumers_but_preserves_sources(self):
        journal = self.root / 'build/packgen/done_steps.json'
        common.atomic_json(journal, {'steps': ['extract', 'convert', 'reflection', 'outfits', 'shaders', 'gen-port']})
        for folder in ['char_shaders', 'char_shaders_outfit/caribbean', 'char_shaders_own/caribbean_real', 'vfx_shaders']:
            touch(self.root / 'build/cache' / folder / 'shader.asm', b'old assembly')
            touch(self.root / 'build/cache' / folder / 'shader.bin', b'bytecode')
        touch(self.root / 'build/cache/other/keep.asm', b'unrelated')
        touch(self.root / 'build/packgen/pack_done.json')
        touch(self.root / 'build/gen/rust_runtime/khg_schemas.rs')
        listing = 'extract extract\nconvert convert\nresearch reflection\npack outfits\nshaders shaders\ngen gen-port\n'
        identity = {'dll_sha256': 'new'}
        with patch('install.run', return_value=listing), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(install.refresh_shader_cache(self.root, identity, 'node', {}, None))
        self.assertEqual(json.loads(journal.read_text())['steps'], ['extract', 'convert', 'reflection'])
        self.assertEqual(len(list((self.root / 'build/cache').rglob('*.bin'))), 4)
        self.assertEqual(list((self.root / 'build/cache').rglob('*.asm')), [self.root / 'build/cache/other/keep.asm'])
        self.assertFalse((self.root / 'build/packgen/pack_done.json').exists())
        self.assertFalse((self.root / 'build/gen/rust_runtime/khg_schemas.rs').exists())
        with patch('install.run', side_effect=AssertionError('unchanged compiler must retain resume')):
            self.assertFalse(install.refresh_shader_cache(self.root, identity, 'node', {}, None))
        with patch('install.run', return_value=listing), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(install.refresh_shader_cache(self.root, {'dll_sha256': 'updated'}, 'node', {}, None))

    def test_helper_source_change_requires_rebuild(self):
        touch(self.root / 'setup/linux/windows-helper.c', b'new source')
        helper = touch(self.root / 'tools/bin/kh-windows-helper.exe')
        self.assertTrue(install.helper_needs_build(self.root))
        helper.with_suffix('.source-sha256').write_text(install.helper_source_hash(self.root))
        self.assertFalse(install.helper_needs_build(self.root))
        (self.root / 'setup/linux/windows-helper.c').write_text('updated source')
        self.assertTrue(install.helper_needs_build(self.root))

    def test_cli_wrappers_end_to_end(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([root / 'setup-linux.sh', *self.args(), '--dry-run'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['platform']['prefix'], str(self.prefix))
        result = subprocess.run([root / 'play-linux.sh', *self.args(), '--me3', str(pe(self.base / 'me3.exe')), '--dry-run'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['offline'])


if __name__ == '__main__':
    unittest.main()
