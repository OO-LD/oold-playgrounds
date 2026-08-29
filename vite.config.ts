import { defineConfig, type Plugin, type Connect } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PYODIDE_VERSION = "0.27.7";

/**
 * Pyodide asks for distribution wheels under /pyodide/. Only the core files and
 * the wheels npm ships are on disk; anything else is redirected to the CDN so it
 * arrives with the bytes pyodide-lock.json expects. Without this the Vite SPA
 * fallback answers 200 with index.html and the subresource integrity check fails.
 */
function pyodideCdnFallback(): Plugin {
  const localDir = join(dirname(fileURLToPath(import.meta.url)), "public", "pyodide");
  const cdn = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

  const middleware: Connect.NextHandleFunction = (req, res, next) => {
    const url = req.url ?? "";
    if (!url.startsWith("/pyodide/")) {
      next();
      return;
    }
    const name = url.slice("/pyodide/".length).split("?")[0];
    if (name === "" || existsSync(join(localDir, name))) {
      next();
      return;
    }
    res.writeHead(302, { Location: cdn + name });
    res.end();
  };

  return {
    name: "pyodide-cdn-fallback",
    configureServer(server) {
      server.middlewares.use(middleware);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
  };
}

export default defineConfig({
  plugins: [react(), pyodideCdnFallback()],
  optimizeDeps: { exclude: ["pyodide", "ty_wasm"] },
  server: { port: 5173, strictPort: true },
  preview: { port: 4173, strictPort: true },
  build: { target: "es2022" },
});
