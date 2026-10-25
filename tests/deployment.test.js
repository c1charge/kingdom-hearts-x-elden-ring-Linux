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
