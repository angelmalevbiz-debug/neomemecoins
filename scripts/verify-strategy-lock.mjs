import fs from 'node:fs';
import crypto from 'node:crypto';
const lock = JSON.parse(fs.readFileSync(new URL('../strategy-lock.json', import.meta.url), 'utf8'));
if (lock.hash_basis && lock.hash_basis !== 'UTF8_LF') {
  throw new Error('Unsupported strategy lock hash basis');
}
const digest = bytes => crypto.createHash('sha256').update(
  lock.hash_basis === 'UTF8_LF' ? bytes.toString('utf8').replace(/\r\n/g, '\n') : bytes
).digest('hex');
const file = fs.readFileSync(new URL('../' + lock.strategy_file, import.meta.url));
const actual = digest(file);
if (actual !== lock.sha256) {
  console.error(`STRATEGY LOCK FAILED: ${lock.strategy_file} changed.\nExpected ${lock.sha256}\nActual   ${actual}`);
  process.exit(1);
}
for (const [path, expected] of Object.entries(lock.support_files_sha256 || {})) {
  const bytes = fs.readFileSync(new URL('../' + path, import.meta.url));
  const actualSupport = digest(bytes);
  if (actualSupport !== expected) {
    console.error(`STRATEGY LOCK FAILED: ${path} changed.`);
    process.exit(1);
  }
}
console.log(`STRATEGY LOCK OK: ${lock.production_strategy_id} ${actual}`);
