import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// ContextMesh dashboard frontend.
// The FastAPI backend (dashboard/backend/main.py) serves the /api routes on :8082.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8082",
        changeOrigin: true,
      },
    },
  },
  build: {
    // Recharts is a single large vendor library; split it from app code so
    // app changes don't invalidate the vendor chunk in browser caches.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          recharts: ["recharts"],
        },
      },
    },
  },
});
