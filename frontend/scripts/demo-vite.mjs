// The launcher owns one Vite process per page, sharing the same backend proxy.
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

function port(name, fallback) {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 1024 || value > 65535) {
    throw new Error(`Invalid ${name}`);
  }
  return value;
}

const backendPort = port("DEMO_BACKEND_PORT", 8000);
const editor = process.argv.includes("--editor");
const frontendPort = editor ? port("DEMO_EDITOR_PORT", 5174) : port("DEMO_FRONTEND_PORT", 5173);
const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  configFile: fileURLToPath(new URL("../vite.config.js", import.meta.url)),
  mode: editor ? "editor" : "development",
  cacheDir: fileURLToPath(new URL(`../node_modules/.vite-demo-${frontendPort}-${backendPort}/`, import.meta.url)),
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: frontendPort,
    strictPort: true,
    proxy: { "/api": { target: `http://127.0.0.1:${backendPort}` } },
  },
});
await server.listen();
server.printUrls();
