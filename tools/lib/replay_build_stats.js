// Build inventory values belong to the reference pack, not to the machine's
// intermediate output directory. Keep them literal rather than scaling them
// from a sandbox's incidental file counts/sizes.
'use strict';
function pinOutfitStats(template, target) {
  const array = template.s && template.s.outfits;
  if (!array || !Object.hasOwn(array, '$a')) throw new Error('outfit stats: expected array-difference recipe');
  const entries = new Map(array.s || []);
  for (let i = 0; i < target.outfits.length; i++) {
    const stats = Object.fromEntries(['disk_bytes', 'files', 'dds_bytes'].filter(k => Object.hasOwn(target.outfits[i], k)).map(k => [k, target.outfits[i][k]]));
    if (!Object.keys(stats).length) continue;
    if (Object.values(stats).some(n => !Number.isSafeInteger(n) || n < 0)) throw new Error('outfit stats must be nonnegative safe integers');
    const node = entries.get(i) || { $o: 0, s: {} };
    if (!Object.hasOwn(node, '$o')) throw new Error('outfit stats: expected object-difference recipe');
    node.s = { ...(node.s || {}), ...stats };
    entries.set(i, node);
  }
  array.s = [...entries].sort((a,b) => a[0]-b[0]);
  return template;
}
module.exports = { pinOutfitStats };
