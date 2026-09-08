<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { errorMessage } from '@/api/client'

const username = ref('')
const password = ref('')
const usernameError = ref('')
const passwordError = ref('')
const serverError = ref('')
const loading = ref(false)
const logoReady = ref(true)
const auth = useAuthStore()
const router = useRouter()

watch(username, () => {
  usernameError.value = ''
  serverError.value = ''
})

watch(password, () => {
  passwordError.value = ''
  serverError.value = ''
})

function validateForm() {
  usernameError.value = username.value.trim() ? '' : '请输入用户名'
  passwordError.value = password.value ? '' : '请输入密码'
  return !usernameError.value && !passwordError.value
}

async function submit() {
  if (loading.value || !validateForm()) return

  loading.value = true
  serverError.value = ''
  try {
    await auth.login(username.value.trim(), password.value)
    router.push('/dashboard')
  } catch (error) {
    serverError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-shell">
    <section class="login-card" aria-labelledby="platform-title">
      <div class="brand-panel">
        <div class="brand-logo">
          <img v-if="logoReady" src="/assets/atl.site.logo.png" alt="盛立科技" @error="logoReady = false" />
          <span v-else>盛立科技</span>
        </div>
        <div class="brand-copy">
          <p class="brand-kicker">OpenSLT · 自动化测试平台</p>
          <h1 id="platform-title">专注测速，<br />让结果清晰可见。</h1>
          <p>从环境采集到分析复核，在一个工作台完成。</p>
        </div>
        <div class="brand-flow" aria-label="测速流程"><span>采集环境</span><span>执行测速</span><span>分析复核</span></div>
      </div>

      <div class="form-panel">
        <div class="form-content">
          <div class="form-heading">
            <span>内部系统</span>
            <h2>登录 OpenSLT</h2>
            <p>使用管理员分配的账号进入平台</p>
          </div>

          <el-form class="login-form" @submit.prevent="submit" label-position="left" label-width="var(--ui-field-label-width)">
            <p v-if="serverError" class="error-alert" role="alert">{{ serverError }}</p>

            <el-form-item label="用户名" :error="usernameError">
              <el-input
                v-model="username"
                size="large"
                autofocus
                autocomplete="username"
                placeholder="请输入用户名"
              >
                <template #prefix>
                  <el-icon><User /></el-icon>
                </template>
              </el-input>
            </el-form-item>

            <el-form-item label="密码" :error="passwordError">
              <el-input
                v-model="password"
                type="password"
                size="large"
                show-password
                autocomplete="current-password"
                placeholder="请输入密码"
                @keyup.enter="submit"
              >
                <template #prefix>
                  <el-icon><Lock /></el-icon>
                </template>
              </el-input>
            </el-form-item>

            <el-button
              type="primary"
              size="large"
              :loading="loading"
              :disabled="loading"
              native-type="submit"
              class="submit"
            >
              登录
            </el-button>
          </el-form>

          <p class="notice">初始账号登录后请立即修改默认密码</p>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.login-shell { min-height: 100dvh; display: grid; place-items: center; padding: 32px; background: var(--ui-canvas); }
.login-card { display: grid; grid-template-columns: 1fr 1fr; width: min(1040px, 100%); overflow: hidden; border: 1px solid var(--ui-border); border-radius: var(--ui-radius-panel); background: var(--ui-surface); }
.brand-panel { display: flex; min-width: 0; flex-direction: column; justify-content: space-between; gap: 48px; padding: 48px; background: var(--ui-primary-soft); }
.brand-logo img { width: 160px; max-width: 100%; height: auto; filter: brightness(.3); }
.brand-kicker { color: var(--ui-primary); font-size: 14px; font-weight: 600; }
.brand-copy h1 { margin: 18px 0; font-size: clamp(28px, 3vw, 38px); line-height: 1.4; letter-spacing: -.03em; }
.brand-copy p:last-child { color: var(--ui-text-secondary); line-height: 1.8; }
.brand-flow { display: flex; flex-wrap: wrap; gap: 16px; color: var(--ui-primary); font-size: 12px; }
.brand-flow span + span::before { content: '→'; margin-right: 16px; color: var(--ui-text-tertiary); }
.form-panel { display: grid; min-width: 0; place-items: center; padding: 56px 48px; }
.form-content { width: min(100%, 360px); }
.form-heading > span { color: var(--ui-primary); font-size: 12px; font-weight: 600; }
.form-heading h2 { margin: 12px 0; font-size: 28px; }
.form-heading p { margin: 0 0 32px; color: var(--ui-text-secondary); font-size: 14px; }
.login-form { --ui-field-label-width: 76px; }
.login-form :deep(.el-form-item) { margin-bottom: 24px; }
.login-form :deep(.el-form-item__label) { font-size: 14px; }
.login-form :deep(.el-input__wrapper) { min-height: 44px; }
.submit { width: 100%; min-height: 44px; }
.notice { margin: 24px 0 0; color: var(--ui-text-secondary); font-size: 12px; line-height: 1.7; }
.error-alert { padding: 12px; color: var(--ui-danger); background: var(--el-color-danger-light-9); border-radius: var(--ui-radius-control); }
@media(max-width: 767px) {
  .login-shell { padding: 16px; }
  .login-card { grid-template-columns: 1fr; }
  .brand-panel { padding: 24px; gap: 20px; }
  .brand-copy h1 { font-size: 28px; }
  .brand-flow { display: none; }
  .form-panel { padding: 28px 24px; }
}
</style>
