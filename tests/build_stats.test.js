'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const crypto=require('node:crypto');
const {pinOutfitStats}=require('../tools/lib/replay_build_stats');
const {decoder}=require('../tools/packgen_diff');
const {serialize}=require('../tools/packgen_serializers');
const hash=text=>crypto.createHash('sha256').update(text).digest('hex');
const decode=(template,base)=>decoder((_,p)=>{let v=base;for(const k of p.split('/').slice(1))v=v[k];return v;},()=>{})(template);

test('outfit build stats remain reference literals across varying intermediate inventories',()=>{
  const template={$o:[0,''],s:{outfits:{$a:0,s:[[0,{$o:0,s:{name:'Reference',files:{$m:2},disk_bytes:{$m:2}}}]]}}};
  const target={outfits:[{id:'sample',name:'Reference',files:20,disk_bytes:200,dds_bytes:80}]};
  pinOutfitStats(template,target);
  const expected=hash(JSON.stringify(target));
  for(const stats of [{files:10,disk_bytes:100,dds_bytes:0},{files:90,disk_bytes:9999,dds_bytes:40}]) {
    const result=decode(template,{outfits:[{id:'sample',name:'Generated',...stats}]});
    assert.equal(hash(JSON.stringify(result)),expected);
  }
  assert.throws(()=>pinOutfitStats(template,{outfits:[{files:-1}]}),/nonnegative/);
});

test('recorded outfit recipe contains no inventory multipliers and retains its expected hash',()=>{
  const recipe=JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'../tools/packgen/replay/files_outfits_outfits.json.json'))).files['outfits/outfits.json'];
  assert.equal(recipe.sha256,'9a5aa9dc0903b255d0b40502597ae85fcbb873fe0232bdfdd1fe6e8f1de79183');
  for(const [i,node] of recipe.template.s.outfits.s) {
    if(i===0)continue;
    for(const field of ['files','disk_bytes','dds_bytes'])assert.ok(Number.isSafeInteger(node.s[field]),field);
  }
  // Other data remains part of the hash; stabilizing inventory does not bypass validation.
  const original={outfits:[{id:'sample',name:'Changed',files:10,disk_bytes:100,dds_bytes:0}]};
  const template={$o:[0,''],s:{outfits:{$a:0,s:[]}}};
  pinOutfitStats(template,{outfits:[{files:20,disk_bytes:200,dds_bytes:80}]});
  assert.equal(decode(template,original).outfits[0].name,'Changed');
});
