import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: `npm run dev` proxies API calls to the desk at :8000.
// Prod: `npm run build` writes frontend/dist, which `python -m desk serve` mounts at /.
const API = ["/health", "/status", "/events", "/stream", "/work-items", "/questions", "/policies",
  "/precedents", "/challenges", "/golden", "/books", "/invariants", "/documents", "/inbox",
  "/score", "/seats", "/tools", "/runs", "/reset", "/attention", "/issues", "/simulate", "/scenarios", "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(API.map((p) => [p, { target: "http://127.0.0.1:8000", changeOrigin: true }])),
  },
  build: { outDir: "dist", sourcemap: false },
});
