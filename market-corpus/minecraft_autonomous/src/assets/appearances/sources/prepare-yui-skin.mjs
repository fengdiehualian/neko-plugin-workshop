/** Export the generated UV sheet to Minecraft's 64x64/binary-alpha format.
 * No colors are painted: nearest-neighbor resampling and alpha quantization only.
 */
import { createCanvas, loadImage } from 'canvas';
import { writeFile } from 'node:fs/promises';
const [input, output] = process.argv.slice(2);
if (!input || !output) throw Error('Usage: node skins/prepare-yui-skin.mjs <generated-square.png> <skin.png>');
const source = await loadImage(input);
if (source.width !== source.height) throw Error('Expected the square modern Minecraft UV layout');
const canvas = createCanvas(64, 64), ctx = canvas.getContext('2d');
ctx.imageSmoothingEnabled = false;
ctx.drawImage(source, 0, 0, 64, 64);
const image = ctx.getImageData(0, 0, 64, 64);
for (let i = 3; i < image.data.length; i += 4) image.data[i] = image.data[i] >= 128 ? 255 : 0;
// Six opaque faces per base part, including the 3-pixel slim arms.
const base = [[8, 0, 16, 8], [0, 8, 32, 8], [4, 16, 8, 4], [0, 20, 16, 12],
    [20, 16, 16, 4], [16, 20, 24, 12], [44, 16, 6, 4], [40, 20, 14, 12],
    [20, 48, 8, 4], [16, 52, 16, 12], [36, 48, 6, 4], [32, 52, 14, 12]];
for (const [x, y, w, h] of base) for (let i = x; i < x + w; i++) for (let j = y; j < y + h; j++) {
    if (image.data[(j * 64 + i) * 4 + 3] !== 255) throw Error(`Transparent base skin face at ${i},${j}`);
}
ctx.putImageData(image, 0, 0);
await writeFile(output, canvas.toBuffer('image/png'));
console.log(JSON.stringify({ width: 64, height: 64, model: 'slim', opaqueBase: true, output }));
