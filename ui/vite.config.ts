import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built output lands inside the Python package so `plex-auto-genres serve`
// finds it without configuration. The Docker build overrides this with
// PAG_STATIC_DIR instead of installing node into the runtime image.
export default defineConfig({
  plugins: [react()],
  build: {
    // PAG_UI_OUT lets the Docker build emit into its own stage instead.
    outDir: process.env.PAG_UI_OUT ?? "../plex_auto_genres/server/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8095", changeOrigin: false },
    },
  },
});
