const fs = require("node:fs");
const path = require("node:path");

// Reads an integer variable from `.env` (user overrides) and then from
// `.env.default` (tracked defaults) in `root`. Returns null when absent.
function readDotenvVariable(key, root = path.resolve(__dirname, "..")) {
  const pattern = new RegExp(
    `^\\s*${key}\\s*=\\s*['"]?(\\d+)['"]?\\s*(?:#.*)?$`,
    "m"
  );
  for (const name of [".env", ".env.default"]) {
    try {
      const match = fs.readFileSync(path.join(root, name), "utf8").match(pattern);
      if (match) return match[1];
    } catch (_) {}
  }
  return null;
}

module.exports = { readDotenvVariable };
