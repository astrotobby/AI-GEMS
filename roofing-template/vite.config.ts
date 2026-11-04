import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { whop } from "@whop/cli/vite";

// Client build → dist/client (served as static assets by Whop hosting).
// The server build (vite.server.config.ts) → dist/server/index.js.
// The whop() plugin packs both into dist/whop-build.zip for `whop apps deploy`.
export default defineConfig({
  plugins: [react(), whop()],
  build: { outDir: "dist/client", emptyOutDir: true },
});
