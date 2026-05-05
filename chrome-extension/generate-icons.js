// Run with: node generate-icons.js
// Generates icon16.png, icon48.png, icon128.png using the Canvas API (node-canvas)
// If you don't have node-canvas, the extension will still load — Chrome just won't
// show a custom icon. You can also replace the PNGs with any 16/48/128px images.

const { createCanvas } = require("canvas");
const fs = require("fs");
const path = require("path");

function drawIcon(size) {
  const canvas = createCanvas(size, size);
  const ctx    = canvas.getContext("2d");

  // Background circle
  ctx.fillStyle = "#ff0000";
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
  ctx.fill();

  // Play triangle
  ctx.fillStyle = "#ffffff";
  const cx = size / 2;
  const cy = size / 2;
  const r  = size * 0.28;
  ctx.beginPath();
  ctx.moveTo(cx - r * 0.6, cy - r);
  ctx.lineTo(cx + r,       cy);
  ctx.lineTo(cx - r * 0.6, cy + r);
  ctx.closePath();
  ctx.fill();

  return canvas.toBuffer("image/png");
}

for (const size of [16, 48, 128]) {
  try {
    const buf  = drawIcon(size);
    const dest = path.join(__dirname, "icons", `icon${size}.png`);
    fs.writeFileSync(dest, buf);
    console.log(`✓ icons/icon${size}.png`);
  } catch (e) {
    console.warn(`Could not generate icon${size}.png (node-canvas not installed): ${e.message}`);
  }
}
