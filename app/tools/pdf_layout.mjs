import fs from 'node:fs';
import { getDocument } from 'file:///C:/Users/HP/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pdfjs-dist/legacy/build/pdf.mjs';

const f = process.argv[2];
const data = new Uint8Array(fs.readFileSync(f));
const doc = await getDocument({ data, disableFontFace: true }).promise;
const page = await doc.getPage(1);
const tc = await page.getTextContent();
const items = tc.items
  .map(it => ({ s: it.str.trim(), x: it.transform[4], y: it.transform[5] }))
  .filter(it => it.s !== '');
items.sort((a, b) => (Math.abs(a.y - b.y) > 4 ? b.y - a.y : a.x - b.x));
let prevY = null;
let line = [];
for (const it of items) {
  if (prevY === null || Math.abs(it.y - prevY) > 4) {
    if (line.length) console.log(line.join('  '));
    line = [];
    prevY = it.y;
  }
  line.push(it.s);
}
if (line.length) console.log(line.join('  '));
