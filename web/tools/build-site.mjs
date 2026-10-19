// Builds the browser version into a folder of plain files that any web server can host.
//
//   npm run site     -> the repository root (index.html, assets/, .nojekyll), which is what
//                       GitHub Pages serves for jadi.zip ("Deploy from a branch", main, / root)
//   npm run build    -> web/dist (to upload somewhere else)
//
// Only esbuild is needed (it comes with `npm install`).
import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(web, process.argv[2] || 'dist');
const assets = join(out, 'assets');
mkdirSync(assets, { recursive: true });

// remove the previous build's app files (and nothing else – the root also holds the desktop app)
for (const f of readdirSync(assets)) if (/^app-[0-9a-f]+\.(js|css)(\.map)?$/.test(f)) rmSync(join(assets, f));

const hash = (s) => createHash('sha256').update(s).digest('hex').slice(0, 10);
const js = await build({
  entryPoints: [join(web, 'src/main.ts')], bundle: true, format: 'esm', minify: true, target: 'es2022',
  write: false, sourcemap: 'external', outfile: 'app.js', legalComments: 'none',
});
const code = js.outputFiles.find(f => f.path.endsWith('.js')).text;
const map = js.outputFiles.find(f => f.path.endsWith('.map')).text;
const jsName = `app-${hash(code)}.js`;
writeFileSync(join(assets, jsName), code + `\n//# sourceMappingURL=${jsName}.map\n`);
writeFileSync(join(assets, jsName + '.map'), map);

const css = await build({ entryPoints: [join(web, 'src/style.css')], bundle: true, minify: true, write: false, outfile: 'app.css' });
const cssText = css.outputFiles[0].text;
const cssName = `app-${hash(cssText)}.css`;
writeFileSync(join(assets, cssName), cssText);

let html = readFileSync(join(web, 'index.html'), 'utf8');
html = html.replace('./src/style.css', `./assets/${cssName}`)
  .replace('<script type="module" src="./src/main.ts"></script>', `<script type="module" src="./assets/${jsName}"></script>`);
writeFileSync(join(out, 'index.html'), html);
for (const f of readdirSync(join(web, 'public'))) copyFileSync(join(web, 'public', f), join(out, f));
// GitHub Pages: serve the files as they are (no Jekyll – it would turn README.md into the home page)
writeFileSync(join(out, '.nojekyll'), '');
if (!existsSync(join(out, 'CNAME')) && existsSync(join(web, 'public', 'CNAME'))) copyFileSync(join(web, 'public', 'CNAME'), join(out, 'CNAME'));
console.log(`Built ${jsName} (${Math.round(code.length / 1024)} kB) and ${cssName} into ${out}`);
