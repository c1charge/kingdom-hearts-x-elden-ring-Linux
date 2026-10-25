// MIT. Normalize exact halfway float literals using their original IEEE-754 bits.
// Older Windows CRTs used by Proton can print six decimals with ties away from
// zero; the Windows reference disassembler uses ties to even. Never infer the
// original float from an already rounded decimal string.
'use strict';
const fs = require('fs');
const path = require('path');
function canonicalTie(decimal, hex) {
  if (!/^[+-]?\d+\.\d{6}$/.test(decimal) || !/^0x[0-9a-f]{8}$/i.test(hex)) return decimal;
  const bits = Number.parseInt(hex.slice(2), 16) >>> 0;
  const exponent = (bits >>> 23) & 255;
  if (exponent === 255) return decimal;
  const sign = bits >>> 31 ? '-' : '';
  const mantissa = BigInt((bits & 0x7fffff) + (exponent ? 0x800000 : 0));
  const power = (exponent ? exponent - 127 : -126) - 23;
  const numerator = mantissa * 1000000n * (power > 0 ? 1n << BigInt(power) : 1n);
  const denominator = power < 0 ? 1n << BigInt(-power) : 1n;
  const floor = numerator / denominator;
  if ((numerator % denominator) * 2n !== denominator) return decimal;
  const format = n => sign + (n / 1000000n) + '.' + (n % 1000000n).toString().padStart(6, '0');
  const even = format(floor + (floor & 1n));
  const away = format(floor + 1n);
  if (decimal !== even && decimal !== away) throw new Error('Disassembly decimal/hex literal disagreement');
  return even;
}
function normalize(decimal, hexadecimal) {
  const lines = decimal.split(/\r?\n/), hexLines = hexadecimal.split(/\r?\n/);
  if (lines.length !== hexLines.length) throw new Error('Disassembly decimal/hex line counts differ');
  return lines.map((line, i) => {
    if (/^\s*\/\//.test(line)) return line;
    const groups = [...hexLines[i].matchAll(/\bl\(([^)]*)\)/g)];
    const decimalGroups = [...line.matchAll(/\bl\(([^)]*)\)/g)];
    if (groups.length !== decimalGroups.length ||
        line.replace(/\bl\([^)]*\)/g, 'l()') !== hexLines[i].replace(/\bl\([^)]*\)/g, 'l()')) {
      throw new Error('Disassembly decimal/hex instruction mismatch at line ' + (i + 1));
    }
    let next = 0;
    return line.replace(/\bl\(([^)]*)\)/g, (whole, content) => {
      const hex = groups[next++][1].split(',').map(x => x.trim());
      const values = content.split(',');
      if (values.length !== hex.length) throw new Error('Disassembly literal counts differ');
      return 'l(' + values.map((value, k) => value.replace(value.trim(), canonicalTie(value.trim(), hex[k]))).join(',') + ')';
    });
  }).join('\n');
}
function normalizeDirectory(dir) {
  for (const file of fs.readdirSync(dir).filter(x => x.endsWith('.bin'))) {
    const stem = path.join(dir, file.slice(0, -4));
    const original = fs.readFileSync(stem + '.asm', 'utf8');
    const result = normalize(original, fs.readFileSync(stem + '.hex.asm', 'utf8'));
    if (result !== original) fs.writeFileSync(stem + '.asm', result);
  }
}
module.exports = { canonicalTie, normalize, normalizeDirectory };
