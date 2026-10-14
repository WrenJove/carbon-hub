import fs from 'node:fs';
import { getDocument } from 'file:///C:/Users/HP/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pdfjs-dist/legacy/build/pdf.mjs';

const files = process.argv.slice(2);
for (const f of files) {
  const data = new Uint8Array(fs.readFileSync(f));
  const doc = await getDocument({ data, disableFontFace: true }).promise;
  const out = [];
  for (let p = 1; p <= doc.numPages; p++) {
    const page = await doc.getPage(p);
    const tc = await page.getTextContent();
    for (const item of tc.items) {
      if (typeof item.str === 'string' && item.str.trim() !== '') {
        out.push(item.str + (item.hasEOL ? '\n' : ' '));
      }
    }
  }
  console.log('===== ' + f.split('/').pop() + ' =====');
  console.log(out.join('').replace(/\n{2,}/g, '\n').trim());
  console.log('');
}
