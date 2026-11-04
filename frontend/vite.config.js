import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const API = 'http://127.0.0.1:8000'
const prefixes = [
  '/ingest', '/cameras', '/telemetry', '/alerts', '/simulate',
  '/agent', '/snapshots', '/health', '/aws', '/api',
]

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries([
      ...prefixes.map((p) => [p, { target: API, changeOrigin: true }]),
      ['/stream', { target: 'ws://127.0.0.1:8000', ws: true }],
    ]),
  },
})
