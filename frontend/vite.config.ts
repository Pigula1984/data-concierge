import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server forwards API calls to the backend, so the page talks to one origin in both modes.
const backend = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ask": backend,
      "/health": backend,
    },
  },
  build: {
    outDir: "dist",
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
