import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: the browser talks only to Vite; /api is proxied to the FastAPI backend.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: { '/api': { target: process.env.BOOKMAKER_API || 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  test: { environment: 'jsdom', globals: true, setupFiles: './src/test/setup.js' },
})
