import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    // Local dev against a running backend (uvicorn on 10443, OVERHUB_SESSION_COOKIE_SECURE=false)
    proxy: { "/api": "http://127.0.0.1:10443" },
  },
});
