import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API is served from the same origin under /api (docs/Web_Auth_Spec.md §7); in development
// Vite proxies it to the FastAPI server.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8000' },
  },
  build: {
    // No inline scripts or styles, so the strict Content-Security-Policy holds.
    assetsInlineLimit: 0,
  },
  test: {
    environment: 'jsdom',
  },
})
