import fs from 'node:fs';
import path from 'node:path';
import {bundle} from '@remotion/bundler';
import {openBrowser, renderStill, selectComposition} from '@remotion/renderer';

const root = path.resolve(import.meta.dirname);
const req = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const pub = path.join(req.output, 'public');
fs.mkdirSync(pub, {recursive: true});
fs.copyFileSync(req.image, path.join(pub, 'portrait.png'));
const serveUrl = await bundle({entryPoint: path.join(root, 'src/cover-entry.tsx'), publicDir: pub,
  outDir: path.join(req.output, 'bundle'), enableCaching: false});
const chromiumOptions = {gl: 'swangle'};
const browserInstance = await openBrowser('chrome', {browserExecutable: req.runtime.browser, chromiumOptions});
try {
  const opts = {serveUrl, inputProps: {...req.props, image: 'portrait.png'}, browserInstance,
    browserExecutable: req.runtime.browser, chromiumOptions, logLevel: 'warn'};
  const composition = await selectComposition({...opts, id: 'CoverStudio'});
  await renderStill({...opts, composition, frame: 0, imageFormat: 'png', output: path.join(req.output, 'cover.png')});
} finally {
  await browserInstance.close({silent: true});
}
