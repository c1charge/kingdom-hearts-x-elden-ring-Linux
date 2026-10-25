// Keep Windows PowerShell behavior intact; Linux uses the explicit Proton API bridge.
'use strict';
const path = require('path');
const cp = require('child_process');
function helper(mode, args) {
  if (!process.env.KH_WINDOWS_HELPER) throw new Error('Windows API helper missing: run setup-linux.sh');
  return cp.spawnSync(process.env.KH_LINUX_PYTHON || 'python3', [path.resolve(__dirname, '../../setup/linux/bridge.py'), process.env.KH_WINDOWS_HELPER, mode, ...args], { encoding: 'utf8', maxBuffer: 1 << 28 });
}
function disassemble(dir) {
  if (process.platform === 'linux') {
    const result = helper('disasm', [dir]);
    if (result.status === 0) {
      try { require('./disasm_rounding').normalizeDirectory(dir); }
      catch (error) { return { ...result, status: 1, stderr: `${result.stderr || ''}\n${error.message}` }; }
    }
    return result;
  }
  return cp.spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(__dirname, 'dxbc_disasm.ps1'), '-Dir', dir], { encoding: 'utf8' });
}
module.exports = { helper, disassemble };
