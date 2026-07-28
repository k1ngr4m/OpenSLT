<script setup lang="ts">
import { Lock } from '@element-plus/icons-vue'
</script>

<template>
  <main class="forbidden">
    <div class="forbidden-code mono">403</div>
    <el-icon class="forbidden-icon"><Lock /></el-icon>
    <h1>暂无访问权限</h1>
    <p>当前账号不能使用此功能。如需继续，请联系管理员调整角色权限。</p>
    <div class="forbidden-actions">
      <el-button @click="$router.back()">返回上一页</el-button>
      <el-button type="primary" @click="$router.push('/dashboard')">返回工作台</el-button>
    </div>
  </main>
</template>

<style scoped>
.forbidden{display:grid;min-height:calc(100dvh - 52px);place-content:center;justify-items:center;padding:40px 20px;color:var(--ui-text-secondary);text-align:center}.forbidden-code{color:var(--ui-primary-soft);font-size:clamp(88px,16vw,180px);font-weight:800;line-height:.8;letter-spacing:-.08em}.forbidden-icon{margin-top:-18px;color:var(--ui-primary);font-size:36px}.forbidden h1{margin:18px 0 6px;color:var(--ui-text-primary);font-size:24px}.forbidden p{max-width:480px;margin:0;line-height:1.7}.forbidden-actions{display:flex;gap:10px;margin-top:24px}
</style>
