import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// Production builds are served by FastAPI straight out of backend/static, so
// the dev proxy only matters while running `npm run dev`.
const backendUrl = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL('../backend/static', import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    strictPort: true,
    proxy: { '/api': { target: backendUrl, changeOrigin: false } },
  },
})
