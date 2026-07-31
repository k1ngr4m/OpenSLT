import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'
import { releaseDefines } from './release-metadata.config'

const elementPlusComponentDirectories: Record<string, string> = {
  ElCheckboxGroup: 'checkbox',
  ElCollapseItem: 'collapse',
  ElFormItem: 'form',
  ElMenuItem: 'menu',
  ElOption: 'select',
  ElRadioButton: 'radio',
  ElRadioGroup: 'radio',
  ElStep: 'steps',
  ElTabPane: 'tabs',
  ElTableColumn: 'table',
}

const additionalElementPlusStyles: Record<string, string[]> = {
  ElRadioButton: ['element-plus/theme-chalk/el-radio-button.css'],
  ElRadioGroup: ['element-plus/theme-chalk/el-radio-group.css'],
}

function directElementPlusComponent(name: string) {
  if (!/^El[A-Z]/.test(name)) return
  const componentName = name
    .slice(2)
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .toLowerCase()
  const directory = elementPlusComponentDirectories[name] ?? componentName
  return {
    name,
    from: `element-plus/es/components/${directory}/index`,
    sideEffects: [
      'element-plus/es/components/base/style/css',
      `element-plus/es/components/${directory}/style/css`,
      ...(additionalElementPlusStyles[name] ?? []),
    ],
  }
}

function elementPlusChunk(id: string) {
  if (!id.includes('/element-plus/es/')) return
  const component = id.match(/\/element-plus\/es\/components\/([^/]+)\//)?.[1]
  if (!component || component === 'base' || component === 'config-provider') {
    return 'vendor-element-core'
  }
}

export default defineConfig({
  plugins: [
    vue(),
    Components({
      dts: false,
      resolvers: [
        { type: 'component', resolve: directElementPlusComponent },
        ...ElementPlusResolver({ importStyle: 'css' }),
      ],
    }),
  ],
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
  build: {
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          if (!id.includes('/node_modules/')) return
          if (id.includes('/@xterm/')) return 'vendor-xterm'
          if (id.includes('/@element-plus/icons-vue/')) return 'vendor-element-icons'
          if (id.includes('/element-plus/')) return elementPlusChunk(id)
          if (
            id.includes('/vue/') ||
            id.includes('/@vue/') ||
            id.includes('/vue-router/') ||
            id.includes('/pinia/') ||
            id.includes('/@vueuse/')
          ) {
            return 'vendor-vue'
          }
          if (id.includes('/axios/')) return 'vendor-http'
          return 'vendor-utils'
        },
      },
    },
  },
})
