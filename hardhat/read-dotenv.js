const fs = require("node:fs");
const path = require("node:path");

const DOTENV_LAYERS = [".env", ".env.default"]; // user overrides first, tracked defaults last
const CHAIN_ID_KEYS = ["GENLAYER_CHAIN_ID", "HARDHAT_CHAIN_ID"];

function readLayer(name, root) {
  try {
    return fs.readFileSync(path.join(root, name), "utf8");
  } catch (_) {
    return null;
  }
}

// Integer value of `key` in one dotenv file's contents, or null.
function parseInteger(contents, key) {
  const match = contents.match(
    new RegExp(`^\\s*${key}\\s*=\\s*['"]?(\\d+)['"]?\\s*(?:#.*)?$`, "m")
  );
  return match ? match[1] : null;
}

// Chain ID from the dotenv files: layer first (`.env` before `.env.default`),
// then key within the layer (GENLAYER_CHAIN_ID before HARDHAT_CHAIN_ID).
function readChainIdFromDotenv(root = path.resolve(__dirname, "..")) {
  for (const name of DOTENV_LAYERS) {
    const contents = readLayer(name, root);
    if (contents === null) continue;
    for (const key of CHAIN_ID_KEYS) {
      const value = parseInteger(contents, key);
      if (value !== null) return value;
    }
  }
  return null;
}

module.exports = { readChainIdFromDotenv };
