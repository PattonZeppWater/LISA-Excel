import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@common': path.resolve(__dirname, '../../CommonTools/Frontend'),
    },
  },
  server: {
    port: 5200,
    proxy: {
      // All API calls forward to the unified Flask app — no path rewriting needed.
      '/api': 'http://localhost:5000',
    },
  },
})
