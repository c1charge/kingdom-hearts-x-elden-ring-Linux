# Linux compatibility audit

Audit before implementation, 2026-10-25. The Windows installer remains the reference workflow.

| Area | Existing boundary | Linux change |
| --- | --- | --- |
| `setup/setup.js` | Registry Steam discovery, Epic manifests, PowerShell version queries and folder pickers | Separate noninteractive Linux entry point; Steam VDF/ACF discovery and explicit overrides |
| Extraction/conversion/research | Node scripts, buffer/crypto/WASM readers and Node workers | Run natively with the same pipeline, recipes and checks |
| Material/VFX translation | `D3DDisassemble` via `tools/lib/dxbc_disasm.ps1` | Windows API helper under selected Proton; preserve translator and errors |
| `tools/er_icons.js` | Oodle DLL via PowerShell/.NET | Same DLL from user's game, copied to build output; helper under Proton |
| `kh-shaderc` | Portable Rust, but Windows-only default DXC discovery | Native compiler or Windows release compiler through an adapter; explicit DXC adapter; pinned official Windows DXC and adjacent DLLs retained |
| Mod DLL compilation | MSVC / Windows SDK / vendored Windows game SDK | Require Windows-built release DLLs and matching schema record; no claim of supported cross compilation |
| DLL self-check | PowerShell `LoadLibraryW` and exported `kh_er_selfcheck` | Same export called by helper under Proton; required before setup success |
| `er/play.bat` | Windows me3 discovery, registry elevation checks, Windows process launch | Additional Linux launcher; Windows script untouched |
| Release packaging | Windows Node SEA, PowerShell archive commands and MSVC | Windows release builder retained; Linux consumes its Windows artifacts |
| Development/research utilities | `tools/lib/cl.js` discovers Visual Studio and invokes `cmd.exe` | Not part of the normal asset pipeline; remains Windows-only |

Upstream me3 v0.13.0 source (commit 989b522214e39c83d701f4182c4f0a86ad79a30a) was consulted: `crates/cli/src/commands/launch.rs` accepts `--exe`, `--profile`, `--savefile`; `start_online` defaults to false. Its native Linux CLI also exists, but selects Proton from Steam's configuration. Our explicit Proton override uses the Windows me3 distribution inside that Proton instead. This route, Steam IPC, DLL injection and D3D12 hooks need real-game testing. Do not describe dry-run or DLL self-check success as successful injection.

Remaining blockers to full validation here: no owned games, no Steam/Proton or graphical session, no Windows mod DLLs/pack, and less than the documented 80 GB working space. Case sensitivity and paths embedded in research recipes may reveal further data-dependent issues. Keep failures visible rather than skipping validation or rewriting assets.

No blanket `dinput8`, `winhttp`, `dxgi`, or `d3d12` native override is justified by this audit: me3 injects the explicitly named DLL. `d3dcompiler_47` is used for DXBC disassembly; Wine's implementation must produce valid assembly or setup fails. A user confirmed that Microsoft's native compiler fixes missing-signature translation failures. Setup now applies a process-local `d3dcompiler_47=n,b` override, validates a project-authored shader before extraction, and invalidates affected caches when the compiler changes; do not replace Proton's graphics DLLs. See LINUX.md for the test procedure and evidence limits.

A later paired Windows/Linux shader comparison isolated three six-decimal halfway-rounding differences. Linux disassembly now carries a hexadecimal-literal companion, used to derive ties-to-even formatting from original float bits without altering expected hashes. The main uploaded shader reproduced the Windows bytes and expected replay hash after correction; this does not validate all game shaders or the separate outfit-statistics recipe.
