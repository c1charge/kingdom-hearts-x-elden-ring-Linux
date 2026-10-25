'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const cp = require('node:child_process');

test('Chandelier rebuild refreshes its owned material namespace and retains stream precedence', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kh deployment '));
  t.after(() => fs.rmSync(root, {recursive:true,force:true}));
  const write = (name, data) => { const file=path.join(root,name);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,data); };
  write('tools/packgen.js',fs.readFileSync(path.join(__dirname,'../tools/packgen.js')));
  for(const [stream,name] of [['xw','xw_form_weapons.js'],['xwb','xwb_chandelier.js'],['er90','xsi_weapon_er_slots.js']]) {
    write(`tools/research/packgen/${stream}/tools/${name}`, `
      const fs=require('fs'),path=require('path');
      const out=path.resolve(__dirname,'../pack/xsi/weapons');
      fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'shared.txt'),${JSON.stringify(stream)});
      if(${JSON.stringify(stream)}==='xwb') {
        fs.mkdirSync(path.join(out,'materials_xwb/textures'),{recursive:true});
        fs.writeFileSync(path.join(out,'materials_xwb/textures/texture.png'),'fresh texture');
        fs.writeFileSync(path.join(out,'materials_xwb/materials.wgsl'),'fresh shader');
      }
    `);
  }
  write('build/sora_pack/xsi/weapons/materials_xwb/textures/texture.png','stale texture');
  write('build/sora_pack/xsi/weapons/materials_xwb/materials.wgsl','stale shader');
  write('build/sora_pack/unrelated.txt','keep');
  for(let i=0;i<2;i++) {
    const result=cp.spawnSync(process.execPath,[path.join(root,'tools/packgen.js'),'pack','--only','xsi-form-weapons'],{encoding:'utf8'});
    assert.equal(result.status,0,result.stdout+result.stderr);
    const read=name=>fs.readFileSync(path.join(root,'build/sora_pack',name),'utf8');
    assert.equal(read('xsi/weapons/materials_xwb/textures/texture.png'),'fresh texture');
    assert.equal(read('xsi/weapons/materials_xwb/materials.wgsl'),'fresh shader');
    assert.equal(read('xsi/weapons/shared.txt'),'xw');
    assert.equal(read('unrelated.txt'),'keep');
  }
});

test('main VFX regenerates its list and savepoint preserves it on success and failure', t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'kh vfx inputs '));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const write=(file,data)=>{const p=path.join(root,file);fs.mkdirSync(path.dirname(p),{recursive:true});fs.writeFileSync(p,data);};
  const list='build/sora_tmp/vfx/fxinv/effect_list.json';
  write('tools/packgen.js',fs.readFileSync(path.join(__dirname,'../tools/packgen.js')));
  write('tools/research/vfx/fxinv/build_rows.js',`require('fs').writeFileSync(require('path').join(__dirname,'effect_list.json'),'["main-effect"]');`);
  write('tools/sora_vfx_pack.js',`const fs=require('fs');const list=JSON.parse(fs.readFileSync(${JSON.stringify(list)}));if(list.length!==1||list[0]!=='main-effect')process.exit(4);fs.mkdirSync('build/sora_pack',{recursive:true});fs.writeFileSync('build/sora_pack/vfx-test.json',JSON.stringify(list));`);
  for(const fail of [false,true]) {
    write(list,'["main-effect","stale-savepoint"]');
    write('tools/sora_savepoint_pack.js',`const fs=require('fs');fs.writeFileSync(${JSON.stringify(list)},'["main-effect","savepoint"]');fs.writeFileSync('build/sora_pack/savepoint-test.json','kept');${fail?'process.exit(7);':''}`);
    const result=cp.spawnSync(process.execPath,[path.join(root,'tools/packgen.js'),'pack','--only','vfx,savepoint'],{cwd:root,encoding:'utf8'});
    assert.equal(result.status,fail?1:0,result.stdout+result.stderr);
    assert.equal(fs.readFileSync(path.join(root,list),'utf8'),'["main-effect"]');
    assert.equal(fs.readFileSync(path.join(root,'build/sora_pack/vfx-test.json'),'utf8'),'["main-effect"]');
    assert.equal(fs.readFileSync(path.join(root,'build/sora_pack/savepoint-test.json'),'utf8'),'kept');
  }
});
