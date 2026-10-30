import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

function separatePageEntry(editor) {
  function configure(server) {
    server.middlewares.use((request, response, next) => {
      const url = new URL(request.url || "/", "http://localhost");
      if (editor && (url.pathname === "/" || url.pathname === "/index.html")) {
        request.url = `/editor.html${url.search}`;
      } else if (!editor && url.pathname === "/editor.html") {
        response.statusCode = 404;
        response.end("Not found");
        return;
      }
      next();
    });
  }
  return {
    name: "separate-page-entry",
    configureServer: configure,
    configurePreviewServer: configure,
  };
}

export default defineConfig(({ mode }) => {
  const editor = mode === "editor";
  return {
    // Each server has its own HTML entry; unknown pages do not fall back to the other app.
    appType: "mpa",
    // Legacy .env.local may contain VITE_BAIDU_SERVER_AK. Never inject it.
    envPrefix: ["VITE_BAIDU_BROWSER_"],
    plugins: [vue(), separatePageEntry(editor)],
    build: {
      outDir: editor ? "dist-editor" : "dist",
      rollupOptions: {
        input: fileURLToPath(new URL(editor ? "./editor.html" : "./index.html", import.meta.url)),
      },
    },
    server: {
      port: editor ? 5174 : 5173,
      strictPort: true,
      proxy: {
        // Dev-server configuration only; never bundled into browser code.
        "/api": process.env.API_UPSTREAM || "http://127.0.0.1:8000",
      },
    },
    preview: {
      port: editor ? 5174 : 4173,
      strictPort: true,
    },
  };
});
