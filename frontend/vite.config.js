import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  // Legacy .env.local may contain VITE_BAIDU_SERVER_AK. Never inject it.
  envPrefix: ["VITE_BAIDU_BROWSER_"],
  plugins: [vue()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
