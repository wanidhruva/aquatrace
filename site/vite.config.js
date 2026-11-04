import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const here = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  base: '/site/',
  plugins: [react(), tailwindcss()],
  // assets/ is the single source of truth for media (hero video, scene clips)
  publicDir: path.resolve(here, '../assets'),
  server: { host: true, port: 5174, strictPort: true },
})
