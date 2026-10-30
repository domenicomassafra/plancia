import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const src = join(dirname(fileURLToPath(import.meta.url)), "..", "src");

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

test("device assets have one fallback surface and no raw <img> call sites", () => {
  const files = walk(src).filter((path) => /\.tsx?$/.test(path));
  const deviceImage = readFileSync(join(src, "components", "DeviceImage.tsx"), "utf8");

  // The single canonical surface must fail closed: an absent or failed asset
  // renders the explicit fallback, never a broken image.
  assert.match(deviceImage, /onError=\{\(\) => setFailedImage\(image \?\? null\)\}/);
  assert.match(deviceImage, /if \(!image \|\| failedImage === image\) return <>\{fallback\}<\/>;/);

  // No view or component may build its own /assets/devices/ <img>, because that
  // bypasses the fallback and reintroduces the broken-image defect.
  const offenders = files
    .filter((path) => path !== join(src, "components", "DeviceImage.tsx"))
    .filter((path) => readFileSync(path, "utf8").includes("`/assets/devices/${"))
    .map((path) => path.slice(src.length + 1));

  assert.deepEqual(offenders, []);
});
