// Syntax-check every web UI JavaScript file as an ES module.
//   node scripts/check_js.mjs
// (Plain "node --check file.js" may treat the file as CommonJS and miss real errors.)
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, readdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'openfan_service', 'web');
const files = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p);
    else if (name.endsWith('.js')) files.push(p);
  }
})(root);

const tmp = mkdtempSync(join(tmpdir(), 'openfan-js-'));
let failed = 0;
for (const f of files) {
  const copy = join(tmp, 'check.mjs');
  copyFileSync(f, copy);
  const r = spawnSync(process.execPath, ['--check', copy], { encoding: 'utf8' });
  if (r.status !== 0) {
    failed += 1;
    console.error(`\n${relative(root, f)}:\n${(r.stderr || '').replaceAll(copy, relative(root, f))}`);
  }
}
rmSync(tmp, { recursive: true, force: true });
if (failed) { console.error(`${failed} file(s) with syntax errors`); process.exit(1); }
console.log(`JavaScript OK (${files.length} files)`);
