import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'
import { releaseDefines } from './release-metadata.config'

export default defineConfig({
  plugins: [vue()],
  define: releaseDefines(),
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    clearMocks: true,
  },
})
