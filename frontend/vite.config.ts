import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { releaseDefines } from './release-metadata.config'

export default defineConfig({
  plugins: [vue()],
  define: releaseDefines(),
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 7777,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:4396', ws: true },
    },
  },
})
