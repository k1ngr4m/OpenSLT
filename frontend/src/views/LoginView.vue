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
    <div class="environment-grid" aria-hidden="true" />
    <section class="login-workspace" aria-labelledby="platform-title">
      <div class="brand-environment">
        <header class="brand-header">
          <img src="/assets/global.logo.jpg" alt="OpenSLT" />
          <div>
            <strong>OPENSLT</strong>
            <span>AUTOMATED TESTING CONTROL PLATFORM</span>
          </div>
        </header>

        <div class="brand-statement">
          <p>CONTROL · OBSERVE · TRACE</p>
          <h1 id="platform-title">复杂测试系统的<br />数字控制台</h1>
          <span>观察运行状态，控制测试流程，沿完整证据链定位问题。</span>
        </div>

        <div class="signal-map" aria-hidden="true">
          <div class="signal-track" />
          <div class="signal-node is-live"><i />NODE<small>READY</small></div>
          <div class="signal-node"><i />RUN<small>CONTROL</small></div>
          <div class="signal-node"><i />DEVICE<small>LINKED</small></div>
          <div class="signal-node"><i />LOG<small>TRACING</small></div>
        </div>

        <footer class="brand-foot">
          <span><i />SYSTEM INTERFACE READY</span>
          <span>INTERNAL ACCESS</span>
        </footer>
      </div>

      <div class="form-panel">
        <div class="form-content">
          <div class="form-heading">
            <span>SIGN IN</span>
            <h2>登录 OpenSLT</h2>
            <p>使用管理员分配的账号进入测试控制平台</p>
          </div>

          <el-form class="login-form" label-position="top" @submit.prevent="submit">
            <p v-if="serverError" class="error-alert" role="alert">{{ serverError }}</p>

            <el-form-item label="用户名" :error="usernameError">
              <el-input
                v-model="username"
                size="large"
                autofocus
                autocomplete="username"
                placeholder="请输入用户名"
              >
                <template #prefix><el-icon><User /></el-icon></template>
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
                <template #prefix><el-icon><Lock /></el-icon></template>
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
.login-shell{position:relative;isolation:isolate;display:grid;min-height:100dvh;overflow:hidden;place-items:center;padding:32px;color:#d7e2e5;background:radial-gradient(circle at 22% 36%,rgba(0,168,143,.09),transparent 28rem),linear-gradient(135deg,#0d171c,#13242a)}
.environment-grid{position:absolute;z-index:-1;inset:0;opacity:.16;background-image:linear-gradient(rgba(215,226,229,.12) 1px,transparent 1px),linear-gradient(90deg,rgba(215,226,229,.09) 1px,transparent 1px);background-size:48px 48px;mask-image:linear-gradient(90deg,#000,transparent 72%)}
.login-workspace{display:grid;width:min(1120px,100%);min-height:min(660px,calc(100dvh - 64px));grid-template-columns:minmax(0,1.25fr) minmax(360px,.75fr);overflow:hidden;border:1px solid rgba(215,226,229,.14);border-radius:10px;background:#11191d;box-shadow:0 24px 64px rgba(3,10,13,.28)}
.brand-environment{position:relative;display:flex;min-width:0;flex-direction:column;padding:36px 40px 30px;background:linear-gradient(150deg,rgba(255,255,255,.025),transparent 50%)}
.brand-header{display:flex;align-items:center;gap:12px}.brand-header img{width:36px;height:36px;border-radius:6px}.brand-header div{display:grid;gap:3px}.brand-header strong{color:#f5f8f9;font:650 16px/1 var(--ui-font-mono);letter-spacing:.08em}.brand-header span{color:#72848a;font:500 10px/1.2 var(--ui-font-mono);letter-spacing:.08em}
.brand-statement{margin:auto 0 42px}.brand-statement>p{margin:0 0 14px;color:var(--ui-primary);font:600 10px/1 var(--ui-font-mono);letter-spacing:.13em}.brand-statement h1{margin:0;color:#f3f7f8;font-size:clamp(34px,4.2vw,52px);font-weight:600;line-height:1.16;letter-spacing:-.04em;text-wrap:balance}.brand-statement>span{display:block;max-width:34em;margin-top:18px;color:#94a5aa;font-size:14px;line-height:1.7}
.signal-map{position:relative;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px;padding:28px 0 34px}.signal-track{position:absolute;top:35px;right:9%;left:9%;height:1px;background:#405056}.signal-track::after{position:absolute;top:0;left:0;width:58%;height:2px;background:var(--ui-primary);content:''}.signal-node{position:relative;display:grid;justify-items:center;gap:5px;color:#a9b7bb;font:600 11px/1 var(--ui-font-mono);letter-spacing:.06em}.signal-node i{position:relative;z-index:1;width:15px;height:15px;border:4px solid #11191d;border-radius:50%;background:#74858b;box-shadow:0 0 0 1px #74858b}.signal-node.is-live i,.signal-node:nth-of-type(3) i{background:var(--ui-primary);box-shadow:0 0 0 1px var(--ui-primary)}.signal-node small{color:#596a70;font:500 9px/1 var(--ui-font-mono)}
.brand-foot{display:flex;align-items:center;justify-content:space-between;padding-top:18px;border-top:1px solid rgba(215,226,229,.09);color:#65777d;font:500 10px/1 var(--ui-font-mono)}.brand-foot span{display:flex;align-items:center;gap:7px}.brand-foot i{width:5px;height:5px;border-radius:50%;background:var(--ui-success)}
.form-panel{display:grid;place-items:center;padding:48px 42px;color:var(--ui-text-primary);background:#f5f7f8}.form-content{width:min(100%,360px)}.form-heading>span{display:block;margin-bottom:10px;color:var(--ui-primary);font:650 10px/1 var(--ui-font-mono);letter-spacing:.12em}.form-heading h2{margin:0;font-size:26px;font-weight:650;line-height:1.2;letter-spacing:-.025em}.form-heading p{margin:10px 0 30px;color:var(--ui-text-secondary);font-size:14px;line-height:1.55}
.login-form{display:grid;gap:2px}.login-form :deep(.el-form-item){margin-bottom:18px}.login-form :deep(.el-form-item__label){padding-bottom:7px;color:var(--ui-text-secondary);font-size:13px;font-weight:600;line-height:1.2}.login-form :deep(.el-input__wrapper){min-height:40px;border-radius:6px;box-shadow:0 0 0 1px var(--ui-border-control) inset}.login-form :deep(.el-input__wrapper:hover){box-shadow:0 0 0 1px var(--ui-border-strong) inset}.login-form :deep(.el-input__wrapper.is-focus){box-shadow:0 0 0 1px var(--ui-primary) inset,var(--ui-focus)}.login-form :deep(.el-input__prefix){color:var(--ui-text-tertiary)}.login-form :deep(.el-form-item__error){padding-top:5px;color:var(--ui-danger);font-size:11px}
.error-alert{margin:0 0 12px;padding:10px 12px;border-left:2px solid var(--ui-danger);border-radius:0 6px 6px 0;color:#c63d50;background:#fcecee;font-size:12px;line-height:1.5}.submit{width:100%;min-height:40px;margin-top:2px;font-weight:650}.notice{margin:18px 0 0;color:var(--ui-text-tertiary);font-size:11px;line-height:1.6;text-align:center}
@media(max-width:860px){.login-workspace{grid-template-columns:1fr;max-width:600px}.brand-environment{min-height:320px;padding:28px 30px 24px}.brand-statement{margin:42px 0 24px}.brand-statement h1{font-size:34px}.brand-statement>span{margin-top:12px}.signal-map{padding-block:20px 26px}.form-panel{padding:36px 30px}}
@media(max-width:520px){.login-shell{align-items:start;overflow:auto;padding:0}.login-workspace{min-height:100dvh;border:0;border-radius:0}.brand-environment{min-height:250px;padding:22px 20px}.brand-header span{display:none}.brand-statement{margin:34px 0 16px}.brand-statement h1{font-size:28px}.brand-statement>span{font-size:12px}.signal-map{gap:6px;padding:15px 0 8px}.signal-node{font-size:9px}.signal-node small,.brand-foot{display:none}.form-panel{padding:30px 20px 34px}}
@media(prefers-reduced-motion:reduce){.login-shell *{transition:none!important}}
</style>
