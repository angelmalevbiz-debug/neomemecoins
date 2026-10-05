import fs from 'node:fs';
import path from 'node:path';
import AdmZip from 'adm-zip';

const root = process.cwd();
const extensionDir = path.join(root, 'extension');
const distDir = path.join(root, 'dist');
const unpackedDir = path.join(distDir, 'extension');
const zipPath = path.join(distDir, 'neo-meme-coins-extension.zip');

if (!fs.existsSync(extensionDir)) {
  throw new Error(`Missing extension directory: ${extensionDir}`);
}
if (!fs.existsSync(distDir)) fs.mkdirSync(distDir, { recursive: true });

fs.rmSync(unpackedDir, { recursive: true, force: true });
fs.cpSync(extensionDir, unpackedDir, { recursive: true });

const zip = new AdmZip();
zip.addLocalFolder(extensionDir);
zip.writeZip(zipPath);

const bytes = fs.statSync(zipPath).size;
console.log(`[NEO] Extension package ready: ${zipPath} (${bytes} bytes)`);
