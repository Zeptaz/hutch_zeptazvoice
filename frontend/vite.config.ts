import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      // Resolve contract copy (see contracts/README.md); fixtures are only loaded by mock mode.
      '@contracts': path.resolve(__dirname, './contracts'),
    },
  },
  server: {
    // 5173 is the Resolve chat app. Add http://localhost:5174 to Voice's HUTCH_VOICE_ALLOWED_ORIGINS
    // and to Resolve's allowed customer origins before a live call.
    port: 5174,
    strictPort: true,
    proxy: {
      // Resolve backend (Harry). Same-origin cookies + CSRF; the browser asks Resolve for the Voice grant.
      '/api': { target: 'http://localhost:8080', changeOrigin: false },
    },
  },
})
