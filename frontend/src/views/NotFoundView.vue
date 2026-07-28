<script setup lang="ts">
import { Compass } from '@element-plus/icons-vue'
</script>

<template>
  <main class="not-found">
    <div class="not-found-code mono">404</div>
    <el-icon class="not-found-icon"><Compass /></el-icon>
    <h1>页面不存在</h1>
    <p>地址可能已更改，或你没有可用的访问入口。</p>
    <div class="not-found-actions">
      <el-button @click="$router.back()">返回上一页</el-button>
      <el-button type="primary" @click="$router.push('/dashboard')">返回工作台</el-button>
    </div>
  </main>
</template>

<style scoped>
.not-found{display:grid;min-height:calc(100dvh - 52px);place-content:center;justify-items:center;padding:40px 20px;color:var(--ui-text-secondary);text-align:center}.not-found-code{color:var(--ui-primary-soft);font-size:clamp(88px,16vw,180px);font-weight:800;line-height:.8;letter-spacing:-.08em}.not-found-icon{margin-top:-18px;color:var(--ui-primary);font-size:36px}.not-found h1{margin:18px 0 6px;color:var(--ui-text-primary);font-size:24px}.not-found p{margin:0}.not-found-actions{display:flex;gap:10px;margin-top:24px}
</style>
