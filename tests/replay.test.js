'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const cp = require('node:child_process');
const crypto = require('node:crypto');
const hash = text => crypto.createHash('sha256').update(text).digest('hex');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kh replay test '));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const write = (file, text) => { fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true }); fs.writeFileSync(path.join(root, file), text); };
  for (const script of ['packgen_replay.js', 'packgen_serializers.js', 'packgen_diff.js']) write('tools/' + script, fs.readFileSync(path.join(__dirname, '../tools', script)));
  const read = file => fs.readFileSync(path.join(root, file), 'utf8');
  const recipe = (file, data) => write('tools/packgen/replay/' + file, JSON.stringify(data));
  const run = (...args) => cp.spawnSync(process.execPath, [path.join(root, 'tools/packgen_replay.js'), 'apply', ...args], { encoding: 'utf8' });
  const target = JSON.stringify({rows:['b','c']});
  write('build/sora_pack/ui/data.json', JSON.stringify({rows:['a','b','c']}));
  const transform = { kind:'json', format:'json0', corpus:['pack:ui/data.json'], template:{ rows:[{'$ref@packgen':[0,'/rows/1']},{'$ref@packgen':[0,'/rows/2']}] }, sha256:hash(target) };
  recipe('files_ui.json', {root:'ui',files:{'ui/data.json':transform}});
  return {root,write,read,recipe,run,target,transform};
}

test('repeated replay and --check use original inputs after shrinking an array', t => {
  const f=fixture(t);
  for(const args of [[], [], ['--check'], []]) {
    const result=f.run(...args);
    assert.equal(result.status,0,result.stdout+result.stderr);
    assert.equal(f.read('build/sora_pack/ui/data.json'),f.target);
  }
});

test('failed validation publishes neither pack nor auxiliary outputs; retry succeeds', t => {
  const f=fixture(t), original=f.read('build/sora_pack/ui/data.json');
  f.write('user/design.txt','original');
  f.write('build/packgen/scratch/fixture/texture.png','wrong');
  f.recipe('files_aux.json',{root:'aux',files:{'user/design.txt':{kind:'raw',text:'changed',sha256:hash('changed')}}});
  f.recipe('files_texture.json',{root:'texture',files:{'texture.png':{kind:'copy',from:'scratch:fixture/texture.png',sha256:hash('correct')}}});
  const failed=f.run();
  assert.equal(failed.status,1);
  assert.match(failed.stdout,/SHA-256 differs/);
  assert.equal(f.read('build/sora_pack/ui/data.json'),original);
  assert.equal(f.read('user/design.txt'),'original');
  // Repair an upstream input, not the expected recipe hash.
  f.write('build/packgen/scratch/fixture/texture.png','correct');
  const retried=f.run();
  assert.equal(retried.status,0,retried.stdout+retried.stderr);
  assert.equal(f.read('build/sora_pack/ui/data.json'),f.target);
  assert.equal(f.read('user/design.txt'),'changed');
});

test('--check stages auxiliary files without changing them, including dependent root references', t => {
  const f=fixture(t);
  f.write('user/design.txt','original');
  f.recipe('files_aaa.json',{root:'aux',files:{'user/design.txt':{kind:'raw',text:'changed',sha256:hash('changed')}}});
  f.recipe('files_aab.json',{root:'copy',files:{'aux-copy.txt':{kind:'copy',from:'root:user/design.txt',sha256:hash('changed')}}});
  const result=f.run('--check');
  assert.equal(result.status,0,result.stdout+result.stderr);
  assert.equal(f.read('user/design.txt'),'original');
  assert.equal(f.read('build/sora_pack/ui/data.json'),JSON.stringify({rows:['a','b','c']}));
});

test('tampered snapshot fails closed and leaves live pack unchanged', t => {
  const f=fixture(t);
  assert.equal(f.run().status,0);
  const manifest=JSON.parse(f.read('build/packgen/replay_inputs/manifest.json'));
  const item=manifest.inputs['pack:ui/data.json'];
  f.write('build/packgen/replay_inputs/'+item.file,'corrupted');
  const result=f.run();
  assert.notEqual(result.status,0);
  assert.match(result.stderr,/snapshot damaged/);
  assert.equal(f.read('build/sora_pack/ui/data.json'),f.target);
});

test('failure report identifies frozen inputs and rejects missing text lines explicitly', t => {
  const f=fixture(t);
  f.recipe('files_text.json',{root:'text',files:{'shader.wgsl':{kind:'text',corpus:['pack:ui/data.json'],lines:[[0,100]],sha256:hash('')}}});
  const result=f.run('--check');
  assert.equal(result.status,1);
  assert.match(result.stdout,/missing source line 100/);
  const report=JSON.parse(f.read('build/packgen/replay_failures.json'));
  assert.equal(report.failures.length,1);
  assert.equal(report.failures[0].sources[0].frozen,true);
  assert.equal(report.failures[0].sources[0].sha256,hash(JSON.stringify({rows:['a','b','c']})));
  assert.equal(f.read('build/sora_pack/ui/data.json'),JSON.stringify({rows:['a','b','c']}));
});
