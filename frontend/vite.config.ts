import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/src/claude_context/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/proxy': {
        target: 'http://127.0.0.1:5555',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:5555',
        changeOrigin: true,
      },
    },
  },
})
