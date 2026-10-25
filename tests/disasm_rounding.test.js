'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {canonicalTie, normalize, normalizeDirectory} = require('../tools/lib/disasm_rounding');
function bits(value) { const b=Buffer.alloc(4);b.writeFloatLE(value);return '0x'+b.readUInt32LE().toString(16).padStart(8,'0'); }

test('exact halfway float literals use ties to even for either sign', () => {
  assert.equal(canonicalTie('0.007813', bits(1/128)), '0.007812');
  assert.equal(canonicalTie('-0.007813', bits(-1/128)), '-0.007812');
  assert.equal(canonicalTie('0.023438', bits(3/128)), '0.023438');
  assert.equal(canonicalTie('0.039063', bits(5/128)), '0.039062');
  assert.equal(canonicalTie('0.007812', bits(1/128)), '0.007812');
});

test('non-ties, scientific notation, integer operands and special floats remain unchanged', () => {
  assert.equal(canonicalTie('0.007813',bits(0.007813)), '0.007813');
  assert.equal(canonicalTie('5', '0x00000005'),'5');
  assert.equal(canonicalTie('1.0e-8',bits(1e-8)),'1.0e-8');
  assert.equal(canonicalTie('0.000000','0x7f800000'),'0.000000');
  assert.throws(()=>canonicalTie('0.007811',bits(1/128)),/disagreement/);
});

test('paired disassembly normalizes only literals and is idempotent', () => {
  const text='// header\nmul r0.x, r0.x, l(0.007813, -0.007813, 0.023438, 5)\nret\n';
  const hex='// header\nmul r0.x, r0.x, l(0x3c000000, 0xbc000000, '+bits(3/128)+', 0x00000005)\nret\n';
  const expected=text.replace('0.007813, -0.007813','0.007812, -0.007812');
  assert.equal(normalize(text,hex),expected);
  assert.equal(normalize(expected,hex),expected);
  assert.throws(()=>normalize(text,hex.replace('mul r0','add r0')),/instruction mismatch/);
  assert.throws(()=>normalize(text,hex+'\n'),/line counts/);
});

test('normalization requires hex evidence and updates only generated assembly', t => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'kh rounding '));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  fs.writeFileSync(path.join(dir,'test.bin'),'synthetic bytecode');
  fs.writeFileSync(path.join(dir,'test.asm'),'mov r0.x, l(0.007813)\n');
  assert.throws(()=>normalizeDirectory(dir),/ENOENT/);
  fs.writeFileSync(path.join(dir,'test.hex.asm'),'mov r0.x, l(0x3c000000)\n');
  normalizeDirectory(dir);
  assert.equal(fs.readFileSync(path.join(dir,'test.asm'),'utf8'),'mov r0.x, l(0.007812)\n');
  assert.equal(fs.readFileSync(path.join(dir,'test.bin'),'utf8'),'synthetic bytecode');
});
