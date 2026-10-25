'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const cp = require('node:child_process');
const path = require('node:path');
const api = require('../tools/lib/windows_api');

test('Windows disassembly retains its PowerShell entry point and argument boundaries', () => {
  const platform = Object.getOwnPropertyDescriptor(process, 'platform');
  const spawn = cp.spawnSync;
  try {
    Object.defineProperty(process, 'platform', { value: 'win32' });
    let call;
    cp.spawnSync = (...args) => { call = args; return { status: 0 }; };
    assert.equal(api.disassemble('C:\\mod folder\\cache').status, 0);
    assert.equal(call[0], 'powershell');
    assert.deepEqual(call[1], ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.resolve(__dirname, '../tools/lib/dxbc_disasm.ps1'), '-Dir', 'C:\\mod folder\\cache']);
  } finally {
    Object.defineProperty(process, 'platform', platform);
    cp.spawnSync = spawn;
  }
});

test('Linux Windows API calls require an explicit helper and preserve failures', () => {
  const helper = process.env.KH_WINDOWS_HELPER;
  const spawn = cp.spawnSync;
  try {
    delete process.env.KH_WINDOWS_HELPER;
    assert.throws(() => api.helper('disasm', ['/cache']), /setup-linux.sh/);
    process.env.KH_WINDOWS_HELPER = '/helper folder/helper.exe';
    let call;
    cp.spawnSync = (...args) => { call = args; return { status: 7, stderr: 'failed' }; };
    assert.equal(api.helper('undcx', ['/copy of game dll.dll', '/a b.dcx']).status, 7);
    assert.deepEqual(call[1].slice(-4), ['/helper folder/helper.exe', 'undcx', '/copy of game dll.dll', '/a b.dcx']);
  } finally {
    if (helper === undefined) delete process.env.KH_WINDOWS_HELPER; else process.env.KH_WINDOWS_HELPER = helper;
    cp.spawnSync = spawn;
  }
});
