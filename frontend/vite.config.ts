import { mkdirSync, copyFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const rootDir = dirname(fileURLToPath(import.meta.url));

// maplibre-gl's worker script imports a sibling "maplibre-gl-shared.mjs" via
// a plain relative specifier that bundlers don't rewrite, so both files have
// to be served together, unhashed, at whatever URL we point setWorkerUrl()
// at (see Map.tsx). Copying straight from the installed package (rather than
// committing a vendored copy) keeps it in lockstep with package-lock.json.
function copyMaplibreWorkerAssets(): Plugin {
  const destDir = resolve(rootDir, "public/vendor/maplibre-gl");
  const copy = () => {
    const srcDir = resolve(rootDir, "node_modules/maplibre-gl/dist");
    mkdirSync(destDir, { recursive: true });
    for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
      copyFileSync(resolve(srcDir, file), resolve(destDir, file));
    }
  };
  return {
    name: "copy-maplibre-worker-assets",
    buildStart: copy,
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), copyMaplibreWorkerAssets()],
  // @maplibre/maplibre-gl-leaflet's CJS interop makes esbuild's dev-mode
  // dependency pre-bundler create a second, separate copy of maplibre-gl
  // (visible as two different files under node_modules/.vite/deps/) --
  // setWorkerUrl() on "our" copy then has no effect on the one the adapter
  // actually uses. Excluding the adapter from pre-bundling keeps it on
  // Vite's normal module graph, which dedupes back to one maplibre-gl.
  optimizeDeps: {
    exclude: ["@maplibre/maplibre-gl-leaflet"],
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});