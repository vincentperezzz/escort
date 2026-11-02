"use strict";

// npm run package -> out/EscortOverlay-win32-x64/EscortOverlay.exe
// Only main.js, preload.js and package.json go into the asar; Chromium
// locales other than en-US are dropped (the overlay's UI text comes from the page).
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { packager } = require("@electron/packager");

const KEEP = new Set(["main.js", "preload.js", "package.json"]);
const outApp = path.join(__dirname, "out", "EscortOverlay-win32-x64");
const outExe = path.join(outApp, "EscortOverlay.exe");
const outAsar = path.join(outApp, "resources", "app.asar");

if (fs.existsSync(outExe) && fs.existsSync(path.dirname(outAsar))) {
  const asar = require("@electron/asar");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "escort-ov-"));
  for (const name of KEEP) {
    fs.copyFileSync(path.join(__dirname, name), path.join(tmp, name));
  }
  asar
    .createPackage(tmp, outAsar)
    .then(() => {
      fs.rmSync(tmp, { recursive: true, force: true });
      console.log(`Packaged ${outExe} (updated app.asar)`);
    })
    .catch((err) => {
      fs.rmSync(tmp, { recursive: true, force: true });
      console.error(err);
      process.exit(1);
    });
} else {
  packager({
    dir: __dirname,
    name: "EscortOverlay",
    executableName: "EscortOverlay",
    platform: "win32",
    arch: "x64",
    out: path.join(__dirname, "out"),
    overwrite: true,
    asar: true,
    prune: true,
    ignore: (file) => file !== "" && !KEEP.has(file.replace(/^\//, "")),
    win32metadata: { FileDescription: "Escort overlay", ProductName: "Escort", CompanyName: "Escort" },
  })
    .then(([appPath]) => {
      const locales = path.join(appPath, "locales");
      for (const name of fs.readdirSync(locales)) {
        if (name !== "en-US.pak") fs.rmSync(path.join(locales, name));
      }
      console.log(`Packaged ${path.join(appPath, "EscortOverlay.exe")}`);
    })
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
