import { defineConfig } from "vite";
import { whop } from "@whop/cli/vite";

// Bundles the API handler into one self-contained module: dist/server/index.js.
export default defineConfig({
  plugins: [whop()],
  publicDir: false,
  ssr: { noExternal: true, target: "webworker" },
  build: {
    ssr: "src/server/index.ts",
    outDir: "dist/server",
    emptyOutDir: true,
    target: "es2022",
    rollupOptions: { output: { entryFileNames: "index.js", format: "es" } },
  },
});
