import { defineConfig } from 'vite';

// base './' makes the built site work from any folder or domain (GitHub Pages, own host, a sub-path).
export default defineConfig({
  base: './',
  build: { outDir: 'dist', sourcemap: true },
});
