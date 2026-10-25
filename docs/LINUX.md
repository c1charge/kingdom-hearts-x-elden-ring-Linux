# Linux support (experimental)

These additional entry points target Nobara/Fedora, native Steam, and installed GE-Proton or official Steam Proton. Windows `setup.exe`, `setup/setup.bat`, and `er/play.bat` remain supported. Read the [compatibility audit](LINUX-AUDIT.md) for the Windows boundaries and remaining blockers.

**Status:** automated filesystem/discovery, orchestration and dry-run tests pass on a Linux cloud machine. The native Rust shader compiler builds and the Windows API helper cross-compiles. This machine has no games, Steam, Proton, or graphical session. End-to-end asset generation, Windows API execution under Proton, DLL injection, dedicated-save redirection in the running game, D3D12 rendering, and gameplay are **unverified**. A passing dry run is not proof that the mod works under Proton.

## Prerequisites on Nobara/Fedora

- Native Linux Steam, signed in and running when playing. Flatpak Steam is outside this initial scope.
- Your Steam copies of KINGDOM HEARTS III + Re Mind and Elden Ring + DLC. Supported Elden Ring executable versions: 2.7.1.0 / 2.7.2.0 (the current README specifies 2.7.2.0).
- An installed GE-Proton or official Steam Proton. In Steam, select the intended version under Elden Ring → Properties → Compatibility and launch the unmodded game once, then close it. Use that same Proton with these scripts. No script downloads a moving “GE Latest” release or changes Steam's compatibility settings.
- Python **3.11+**, Node.js **20+**, npm, and about **80 GiB free** for initial extraction. Keep the mod on a filesystem that permits executable files and symlinks. Paths containing spaces are supported; quote them in the shell.
- Windows me3 **0.13.0 or newer**, from the [official me3 releases](https://github.com/garyttierney/me3/releases). Extract the **whole Windows distribution**, keeping `me3.exe`, `me3-launcher.exe`, `me3_mod_host.dll`, and its other files together. This launcher runs that distribution under Proton; it does not use the native Linux me3 CLI.
- `x86_64-w64-mingw32-gcc` to build the small open-source API helper, or an already built helper supplied with `--helper`. On Fedora the relevant compiler package is `mingw64-gcc`. A package manager can install prerequisites if your machine lacks them; these scripts never invoke sudo, install system packages, or require root. A helper built on another machine is a no-root alternative.
- Rust stable/Cargo only when a native `kh-shaderc` must be built from source. The Windows release's `tools/bin/kh-shaderc.exe` is supported through Proton, so a full release does not require Rust.
- Protontricks is needed if the prefix lacks a translator-compatible Microsoft `d3dcompiler_47`. Install it explicitly with `protontricks 1245620 d3dcompiler_47`; ensure Protontricks targets the same prefix passed to setup. Setup never invokes Protontricks or downloads that DLL automatically.

No game assets, extracted data, game executables, Oodle DLLs or keys are distributed. Licenses and attribution remain in [LEGAL.md](../LEGAL.md). All extraction reads your own installations; outputs stay in the mod tree.

## Prepare the mod

Keep the mod folder **outside both game directories**. Do not run these scripts as root. Do not run setup while either game is running.

For a release containing these Linux scripts, extract it in your chosen mod folder. If using a source checkout, obtain matching Windows-built mod artifacts from a release or a Windows build: `kh_er.dll`, `kh_er_loader.dll`, and `kh_er.schemas.json`. Supply their directory with `--dll-dir`. Linux does not attempt an unsupported MSVC or MinGW build of the game mod. The setup compares the generated pack against the DLL schema record before installation succeeds.

From the mod root:

```bash
bash setup-linux.sh --dry-run
bash setup-linux.sh --dll-dir "/path/to/windows-release/er/mods"
```

Omit `--dll-dir` when `er/mods` already has those artifacts. With a source checkout, native `kh-shaderc` is built using `cargo build --locked --release -p kh-shaderc`. To use the release's compiler instead:

```bash
bash setup-linux.sh \
  --dll-dir "/path/to/release/er/mods" \
  --shaderc "/path/to/release/tools/bin/kh-shaderc.exe"
```

Discovery checks `~/.local/share/Steam`, `~/.steam/steam`, and `~/.steam/root`, canonicalizing symlinks. It reads `steamapps/libraryfolders.vdf` for additional libraries and their `appmanifest_*.acf` files. Elden Ring is app 1245620; KH3 is app 2552450. Explicit overrides are authoritative and fail with an error if invalid:

```bash
bash setup-linux.sh \
  --steam "$HOME/.local/share/Steam" \
  --library "/mnt/Games/Steam Library" \
  --kh3 "/mnt/Games/Steam Library/steamapps/common/KINGDOM HEARTS III" \
  --er "/mnt/Games/Steam Library/steamapps/common/ELDEN RING" \
  --proton "$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-10/proton"
```

`--proton` accepts the `proton` executable or its directory. Discovery checks custom compatibility tool directories, `STEAM_EXTRA_COMPAT_TOOLS_PATHS`, system compatibility tool directories, and official Proton installations in Steam libraries. The default prefers the highest naturally sorted installed GE version, then official Proton; it does not infer Steam's selected version. Pin it explicitly when multiple versions are installed.

`--prefix` accepts the **compatdata directory containing `pfx`**, normally `<Steam library>/steamapps/compatdata/1245620`. Do not supply `pfx` itself. The existing prefix must exist; launch the normal game once through Steam to create it. The scripts set `STEAM_COMPAT_CLIENT_INSTALL_PATH`, `STEAM_COMPAT_DATA_PATH`, and the Elden Ring app IDs for their child processes. They do not change global environment variables or install a separate Wine prefix.

## What setup runs

1. Discover games, Steam, Proton and prefix. Check Windows mod artifacts and loader version marker.
2. Check native Node/npm. Install locked npm dependencies if missing, unless `--no-download` is specified.
3. Build or refresh the helper when its source changes. Under Proton, compile and disassemble a tiny project-authored shader and require the exact input-signature format consumed by the translators. If the check fails, stop early with prefix-specific Protontricks instructions. Then read Elden Ring's executable version. If the version cannot be determined, warn and continue; a positively detected unsupported version still stops setup.
4. Discover DXC (`--dxc FILE` or `tools/bin/dxc/bin/x64/dxc.exe`), or download Microsoft's official pinned v1.8.2502 archive. TLS verification and the repository's existing SHA-256 pin are required **before extraction**. Keep `dxcompiler.dll` and `dxil.dll` beside `dxc.exe`. Existing user-supplied tools are trusted local inputs.
5. Use a native shader compiler, a Windows release shader compiler through an adapter, or build the native compiler from source.
6. Use native Node for pak-key discovery and the existing `packgen.js` extract/convert/research/pack/replay/shaders/gen stages. Calls to `D3DDisassemble` and the copied game Oodle DLL cross into the selected Proton through the helper. Shader translation, replay verification, generated-data checks and schema comparison remain in place.
7. Import DLLs without overwriting differing existing files, preserve `kh-er.ini` (or convert legacy `.ctl` with the existing tool), and call the DLL's `kh_er_selfcheck` export under Proton. Setup succeeds only if that check reads the pack's table groups.

The standard pack is `build/sora_pack`. Existing custom `er/mods/kh-er.cfg` pack overrides are preserved and cause an explanatory stop; back up that config and remove the override to use the generated standard pack. Custom `--pack`/`--gen` imports from Windows setup are not implemented by the Linux entry point.

All commands use argument arrays, not shell interpolation. Adapters translate absolute host paths to Proton's normal `Z:` mapping. Python, Node and pipeline input paths stay native; only Windows process arguments are translated. Modified drive mappings, UNC mounts, and Windows paths in source profile overrides are outside this initial scope.

### Resume and errors

Replay preserves original inputs that its recipes overwrite, using `build/packgen/replay_inputs` (and a separate overlay snapshot). Retries and `apply --check` use that generation's snapshot rather than applying recipes twice to transformed output. All recipe hashes must pass before live pack or auxiliary files are copied. A filesystem error during copying can still leave partial output; retry uses the preserved inputs. Running an upstream extraction/conversion/research/pack stage invalidates snapshots for the new generation.

If an older setup already left partially replayed files, run your original setup command once with `--rebuild-pack` after updating. The fix cannot reconstruct original inputs from those previously transformed files. Recipe changes or damaged snapshots stop with a rebuild instruction; expected hashes are never rewritten.

Rerun the same command after fixing the reported error. The existing `build/packgen/done_steps.json` journal is passed back through `--resume`; failed steps are not marked complete. Linux setup also records game input paths, sizes and timestamps in `build/linux-install.json`. Changing game inputs requires a fresh mod folder to avoid reusing stale extracted caches. In-place game updates are deliberately not handled by silently accepting old outputs.

On upgrading from an older Linux setup, or when the compiler DLL, helper, Proton executable, or setup overrides change, setup automatically deletes only cached shader `.asm` files and invalidates pack/replay/shaders/gen completion. Extracted data, bytecode, completed extraction/conversion/research steps, settings, and saves are retained. The selected disassembler identity is recorded in `build/linux-disassembler.json`. This repairs the earlier case where outfit stages recorded completion despite untranslated materials.

`--rebuild-pack` reruns packgen phases and generated tables; it does not erase all converter caches and is not a workaround for changed game inputs. `--workers N` controls pipeline workers and native compiler build parallelism. No writes go into original game directories; output aliases into those directories are rejected.

Logs: `build/packgen/logs/linux-setup.log` plus existing packgen/shader logs. Errors preserve the failed command's status; a failed validation is not treated as successful installation. Simultaneous setup/launch operations in one mod folder are unsupported.

## Launching

After setup succeeds, start Steam and close any running Elden Ring instance:

```bash
bash play-linux.sh --me3 "/path/to/me3/bin/me3.exe" --dry-run
bash play-linux.sh --me3 "/path/to/me3/bin/me3.exe" \
  --proton "$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-10/proton"
```

The launcher remembers the me3 path in `er/linux-launch.json` after validating its version. Steam, library, Proton, prefix and Elden Ring overrides are the same as setup. `--profile FILE` accepts a host-side v1 me3 profile supporting Elden Ring, loading this mod's loader, with an explicit dedicated save name and `start_online` false or absent.

The source `er/profiles/kh-sora.me3` stays intact. A derived `er/profiles/kh-sora-linux.me3` has absolute Wine paths, explicit offline mode and the original dedicated save name. The launch command also supplies `--savefile` explicitly. Unsafe save names, `ER0000.sl2`, unknown me3 versions, prereleases and versions below 0.13.0 are rejected before launch.

The child process runs `me3.exe launch -g eldenring --exe <eldenring.exe> -p <derived profile> --savefile KHSora.sl2` under Proton. It does not start `start_protected_game.exe`, enable online mode, alter Steam launch options, rename game executables, copy proxy DLLs into the game, or change registry overrides. me3's offline behavior is responsible for bypassing EAC and blocking official matchmaking. **Keep the mod offline.** Normal Steam launch settings remain unchanged; the existing prefix is shared and Proton can update it when versions change.

The dedicated game save is inside the Elden Ring prefix, normally `pfx/drive_c/users/steamuser/AppData/Roaming/EldenRing/<Steam id>/KHSora.sl2`. KH progress remains in `er/saves`. Back up both when moving the mod. Save redirection must still be verified in a real Proton session.

## DLL overrides and troubleshooting

There is no evidence yet that extra injection overrides are needed. me3 uses its own injection path; do not blindly add `dinput8`, `winhttp`, `dxgi` or `d3d12` overrides.

Linux **setup** adds `d3dcompiler_47=n,b` to its child-process environment and preserves unrelated DLL overrides. This selects a native compiler when installed. The early preflight stops if fallback Wine disassembly lacks the input-signature format needed by the translator. It does not alter the prefix registry, normal Steam launches, or the launcher environment. `--dll-overrides '...'` still selects other process-local overrides, but setup always sets the compiler entry to `n,b`.

A user confirmed that installing Microsoft's native compiler fixed widespread `NOT TRANSLATED: input v0` failures under Proton. To install it yourself:

```bash
protontricks 1245620 d3dcompiler_47
```

For a custom `--prefix`, configure Protontricks to target that same app prefix; do not install the DLL into a different prefix. Rerun the original setup command afterward. The compiler override and shader cache repair are now automatic: there is no need to add `--dll-overrides` or delete assembly manually. Microsoft DLLs are not bundled. The new preflight and automatic repair have automated regression coverage, but their actual execution under Proton remains unverified on the development cloud machine.

For Oodle errors, check that Elden Ring's own `Game/oo2core_6_win64.dll` exists and that the selected prefix can load 64-bit Windows DLLs. The helper uses a copy under `build/`; never download that game DLL from third-party sites.

For loader errors, ensure the full Windows me3 bundle is together, its version is >=0.13.0, Steam is running, the prefix belongs to app 1245620 and the same Proton works with the unmodded game. Unknown/unsupported executable versions and schema mismatches need matching builds, not disabled checks. The Windows me3-to-native-Steam IPC route may require further compatibility work.

Launch diagnostics are in `er/logs/linux-launch.log` and Proton's `er/logs/steam-1245620.log`; the mod should write `er/logs/kh-er.log` after loading. Exit code zero or a window appearing does not prove injection. On a real Nobara system, verify these in order:

1. Setup self-check reads all table groups; inspect `er/mods/kh-er-selfcheck.txt`.
2. me3 logs confirm loading `kh_er_loader.dll`, and `kh-er.log` confirms the expected game version and successful initialization.
3. The mod HUD/characters appear and controls/audio/rendering work.
4. A session uses `KHSora.sl2` and KH progress under `er/saves`; verify that normal-save contents were not changed.
5. Close the mod and verify that the normal Steam launch still works with its original settings.

## Automated validation

No owned games, downloads, Proton or graphical session are needed:

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.test.js
node release-tools/scan.js
```

Tests use temporary Steam libraries and fake files to verify discovery, escaped paths, argument handling, missing dependencies, Proton ordering, path conversion, save safety, generated profiles, checksum rejection, disassembler preflight validation, helper rebuild detection, compiler-specific cache invalidation, preservation of unrelated DLL overrides, dry-run side-effect boundaries, and a failed installation followed by resume. They do not emulate or prove Windows API behavior. `--dry-run` inspects the selected filesystem and prints the plan without downloads, writes, version subprocesses or launches; missing games/Steam/Proton/prefix still produce useful errors. Installer plans list missing build dependencies and Windows artifacts.

The Windows PowerShell disassembly branch is covered by an argument-level regression test. Full Windows setup/release execution remains untested on this Linux machine. No combat or mod runtime logic was changed.
