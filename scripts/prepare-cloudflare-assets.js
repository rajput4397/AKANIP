import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const assetDirectory = resolve(projectRoot, ".cloudflare-assets");
const assetFiles = [
  "config_options_daily.yaml",
  "config_options_15min.yaml",
  "indian_options.csv",
];

mkdirSync(assetDirectory, { recursive: true });
for (const file of assetFiles) {
  copyFileSync(resolve(projectRoot, file), resolve(assetDirectory, file));
}

console.log(`Prepared ${assetFiles.length} Cloudflare scan assets.`);