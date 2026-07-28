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
    <div class="background-grid" aria-hidden="true"></div>
    <div class="background-network" aria-hidden="true"></div>

    <section class="login-card" aria-labelledby="platform-title">
      <div class="brand-panel">
        <div class="brand-header">
          <div class="brand-logo">
            <img
              v-if="logoReady"
              src="/assets/atl.site.logo.png"
              alt="盛立科技"
              @error="logoReady = false"
            />
            <span v-else>盛立科技</span>
          </div>
        </div>

        <div class="brand-copy">
<!--          <p class="brand-kicker">OpenSLT 自动化测试</p>-->
<!--          <h1 id="platform-title">让复杂测速链路保持清晰、可控、可追溯</h1>-->
<!--          <p class="brand-description">统一管理测速资源、运行流程、实时日志和结果归档。</p>-->
        </div>

        <div class="ops-visual" aria-hidden="true">
          <div class="radar-rings">
            <span class="ring ring-large"></span>
            <span class="ring ring-middle"></span>
            <span class="ring ring-small"></span>
            <span class="scan-line"></span>
          </div>
          <div class="ops-desk">
            <span class="desk-arc"></span>
            <span class="operator operator-left"></span>
            <span class="operator operator-center"></span>
            <span class="operator operator-right"></span>
            <span class="screen screen-left"></span>
            <span class="screen screen-right"></span>
            <span class="chart-bars"></span>
          </div>
        </div>

<!--        <div class="brand-flow" aria-label="平台能力">-->
<!--          <span>资源预检</span>-->
<!--          <i></i>-->
<!--          <span>人工确认</span>-->
<!--          <i></i>-->
<!--          <span>自动执行</span>-->
<!--          <i></i>-->
<!--          <span>结果复核</span>-->
<!--        </div>-->
      </div>

      <div class="form-panel">
        <div class="form-content">
          <div class="form-heading">
            <span>内部系统</span>
            <h2>登录 OpenSLT</h2>
            <p>使用管理员分配的账号进入平台</p>
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
.login-shell {
  position: relative;
  isolation: isolate;
  min-height: 100dvh;
  display: grid;
  place-items: center;
  overflow: hidden;
  padding: clamp(24px, 5vw, 56px);
  color: #f6fbff;
  background:
    radial-gradient(circle at 52% 12%, rgba(34, 111, 196, 0.45), transparent 34rem),
    radial-gradient(circle at 84% 78%, rgba(0, 151, 198, 0.18), transparent 28rem),
    linear-gradient(135deg, #061a43 0%, #0a3272 46%, #06204b 100%);
}

.login-shell::before,
.login-shell::after,
.background-grid,
.background-network {
  position: absolute;
  inset: 0;
  z-index: -1;
  content: '';
  pointer-events: none;
}

.login-shell::before {
  opacity: 0.34;
  background-image:
    radial-gradient(circle, rgba(183, 221, 255, 0.38) 1px, transparent 1.5px),
    radial-gradient(circle, rgba(72, 169, 240, 0.2) 1px, transparent 1.5px);
  background-position:
    0 0,
    18px 18px;
  background-size:
    36px 36px,
    72px 72px;
  mask-image: radial-gradient(ellipse at center, #000 8%, transparent 74%);
}

.login-shell::after {
  opacity: 0.5;
  background:
    linear-gradient(115deg, transparent 0 18%, rgba(94, 178, 245, 0.08) 18.2%, transparent 18.6%),
    linear-gradient(64deg, transparent 0 73%, rgba(41, 152, 214, 0.12) 73.2%, transparent 73.6%);
}

.background-grid {
  opacity: 0.24;
  background-image:
    linear-gradient(rgba(157, 213, 255, 0.16) 1px, transparent 1px),
    linear-gradient(90deg, rgba(157, 213, 255, 0.13) 1px, transparent 1px);
  background-size: 86px 86px;
  transform: perspective(520px) rotateX(62deg) translateY(30%);
  transform-origin: center bottom;
}

.background-network {
  opacity: 0.28;
  background:
    radial-gradient(circle at 13% 57%, rgba(112, 186, 255, 0.8) 0 2px, transparent 3px),
    radial-gradient(circle at 20% 62%, rgba(112, 186, 255, 0.8) 0 2px, transparent 3px),
    radial-gradient(circle at 27% 54%, rgba(112, 186, 255, 0.55) 0 2px, transparent 3px),
    radial-gradient(circle at 78% 34%, rgba(112, 186, 255, 0.7) 0 2px, transparent 3px),
    radial-gradient(circle at 86% 40%, rgba(112, 186, 255, 0.55) 0 2px, transparent 3px),
    linear-gradient(25deg, transparent 0 13%, rgba(112, 186, 255, 0.2) 13.1%, transparent 13.3%),
    linear-gradient(152deg, transparent 0 78%, rgba(112, 186, 255, 0.2) 78.1%, transparent 78.3%);
}

.login-card {
  width: min(100%, 1060px);
  min-height: min(680px, calc(100dvh - 72px));
  display: grid;
  grid-template-columns: minmax(0, 1.04fr) minmax(360px, 0.96fr);
  overflow: hidden;
  border: 1px solid rgba(180, 218, 255, 0.22);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow:
    0 34px 90px rgba(0, 18, 58, 0.42),
    0 0 0 1px rgba(255, 255, 255, 0.08);
}

.brand-panel {
  position: relative;
  isolation: isolate;
  display: flex;
  min-width: 0;
  flex-direction: column;
  overflow: hidden;
  padding: clamp(34px, 4vw, 52px);
  background:
    radial-gradient(circle at 54% 50%, rgba(24, 120, 232, 0.62), transparent 19rem),
    linear-gradient(160deg, #064bb6 0%, #053592 48%, #061d5a 100%);
}

.brand-panel::before,
.brand-panel::after {
  position: absolute;
  inset: 0;
  z-index: -1;
  content: '';
  pointer-events: none;
}

.brand-panel::before {
  opacity: 0.24;
  background-image:
    linear-gradient(rgba(155, 213, 255, 0.16) 1px, transparent 1px),
    linear-gradient(90deg, rgba(155, 213, 255, 0.12) 1px, transparent 1px);
  background-size: 58px 58px;
  mask-image: linear-gradient(150deg, transparent 0%, #000 38%, transparent 100%);
}

.brand-panel::after {
  opacity: 0.75;
  background:
    radial-gradient(circle at 68% 62%, rgba(125, 218, 255, 0.28), transparent 16rem),
    linear-gradient(90deg, transparent 0 16%, rgba(119, 205, 255, 0.13) 16.2%, transparent 16.6%),
    linear-gradient(36deg, transparent 0 63%, rgba(119, 205, 255, 0.16) 63.2%, transparent 63.6%);
}

.brand-header {
  position: relative;
  z-index: 1;
}

.brand-logo {
  display: inline-flex;
  min-height: 48px;
  align-items: center;
}

.brand-logo img {
  width: min(231px, 62vw);
  height: auto;
  display: block;
  filter: drop-shadow(0 12px 22px rgba(0, 15, 49, 0.22));
}

.brand-logo span {
  color: #fff;
  font-size: 21px;
  font-weight: 700;
}

.brand-copy {
  position: relative;
  z-index: 1;
  max-width: 460px;
  margin-top: clamp(34px, 6vh, 58px);
}

.brand-kicker {
  margin: 0 0 16px;
  color: #9fd9ff;
  font-size: 12px;
  font-weight: 650;
  letter-spacing: 0.12em;
}

.brand-copy h1 {
  margin: 0;
  max-width: 12em;
  color: #fff;
  font-size: clamp(34px, 4.8vw, 54px);
  font-weight: 700;
  line-height: 1.16;
  letter-spacing: 0;
  text-wrap: balance;
  text-shadow: 0 10px 34px rgba(0, 22, 74, 0.38);
}

.brand-description {
  max-width: 33em;
  margin: 20px 0 0;
  color: rgba(232, 247, 255, 0.78);
  font-size: 15px;
  line-height: 1.8;
}

.ops-visual {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 260px;
  margin: clamp(20px, 4vh, 36px) 0 20px;
}

.radar-rings {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
}

.ring,
.scan-line,
.desk-arc,
.operator,
.screen,
.chart-bars {
  position: absolute;
  display: block;
}

.ring {
  border-radius: 50%;
  border: 1px solid rgba(171, 233, 255, 0.28);
  box-shadow: inset 0 0 34px rgba(86, 186, 255, 0.12);
}

.ring-large {
  width: min(88%, 430px);
  aspect-ratio: 1;
}

.ring-middle {
  width: min(64%, 310px);
  aspect-ratio: 1;
  border-color: rgba(171, 233, 255, 0.42);
}

.ring-small {
  width: min(36%, 176px);
  aspect-ratio: 1;
  background: rgba(81, 179, 246, 0.1);
}

.scan-line {
  width: min(40%, 198px);
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(196, 247, 255, 0.92), transparent);
  transform-origin: left center;
  animation: scanSweep 5.8s linear infinite;
}

.ops-desk {
  position: absolute;
  inset: 15% 6% 0;
  display: grid;
  place-items: center;
}

.desk-arc {
  bottom: 15%;
  width: min(86%, 380px);
  height: 42%;
  border-radius: 50% 50% 46% 46%;
  background:
    linear-gradient(180deg, rgba(217, 249, 255, 0.82), rgba(73, 170, 242, 0.48)),
    linear-gradient(90deg, rgba(255, 255, 255, 0.45), transparent);
  box-shadow:
    inset 0 -18px 42px rgba(5, 57, 156, 0.4),
    0 28px 54px rgba(0, 22, 74, 0.34);
}

.desk-arc::after {
  position: absolute;
  inset: 22% 12%;
  border-radius: 50%;
  background: #05399a;
  box-shadow: inset 0 0 34px rgba(104, 198, 255, 0.28);
  content: '';
}

.operator {
  z-index: 2;
  width: 32px;
  height: 48px;
  border-radius: 16px 16px 8px 8px;
  background: #9fd9ff;
  box-shadow: 0 12px 22px rgba(0, 22, 74, 0.28);
}

.operator::before {
  position: absolute;
  top: -13px;
  left: 8px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #bb7a33;
  content: '';
}

.operator-left {
  top: 43%;
  left: 31%;
  background: #ffd466;
}

.operator-center {
  top: 37%;
  left: 50%;
  background: #94d4ff;
}

.operator-right {
  top: 55%;
  left: 61%;
  background: #f2a1c2;
}

.screen {
  z-index: 3;
  width: 70px;
  height: 46px;
  border: 2px solid rgba(212, 248, 255, 0.78);
  border-radius: 4px;
  background:
    linear-gradient(145deg, rgba(174, 233, 255, 0.62), rgba(22, 95, 207, 0.62)),
    linear-gradient(rgba(255, 255, 255, 0.28) 1px, transparent 1px);
  background-size:
    auto,
    100% 12px;
  box-shadow: 0 14px 28px rgba(0, 22, 74, 0.28);
}

.screen-left {
  top: 26%;
  left: 24%;
  transform: skewY(-10deg);
}

.screen-right {
  top: 25%;
  right: 22%;
  transform: skewY(8deg);
}

.chart-bars {
  right: 13%;
  bottom: 29%;
  z-index: 3;
  width: 86px;
  height: 76px;
  opacity: 0.68;
  background:
    linear-gradient(90deg, rgba(196, 247, 255, 0.72) 0 8px, transparent 8px 14px),
    linear-gradient(90deg, rgba(196, 247, 255, 0.38) 0 10px, transparent 10px 16px);
  background-size:
    14px 100%,
    16px 72%;
  background-position:
    left bottom,
    right bottom;
  background-repeat: repeat-x;
}

.brand-flow {
  position: relative;
  z-index: 1;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  color: rgba(232, 247, 255, 0.82);
  font-size: 12px;
  font-weight: 600;
}

.brand-flow i {
  width: 28px;
  height: 1px;
  background: rgba(158, 220, 255, 0.48);
}

.form-panel {
  display: grid;
  min-width: 0;
  place-items: center;
  padding: clamp(40px, 5vw, 68px);
  color: #162b3a;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(247, 251, 255, 0.98)),
    #fff;
}

.form-content {
  width: min(100%, 390px);
}

.form-heading span {
  display: block;
  margin-bottom: 11px;
  color: #0e806f;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.1em;
}

.form-heading h2 {
  margin: 0;
  color: #10283a;
  font-size: 32px;
  font-weight: 700;
  line-height: 1.18;
  letter-spacing: 0;
}

.form-heading p {
  margin: 12px 0 34px;
  color: #65798a;
  font-size: 14px;
  line-height: 1.7;
}

.login-form {
  display: grid;
  gap: 4px;
}

.error-alert {
  margin: 0 0 12px;
  padding: 11px 13px;
  border: 1px solid rgba(189, 63, 75, 0.22);
  border-radius: 6px;
  color: #a72f3e;
  background: rgba(189, 63, 75, 0.08);
  font-size: 13px;
  line-height: 1.5;
}

.login-form :deep(.el-form-item) {
  margin-bottom: 22px;
}

.login-form :deep(.el-form-item__label) {
  padding-bottom: 8px;
  color: #284155;
  font-size: 13px;
  font-weight: 650;
  line-height: 1.2;
}

.login-form :deep(.el-input__wrapper) {
  min-height: 46px;
  border-radius: 6px;
  box-shadow: 0 0 0 1px #dce6ee inset;
  transition:
    box-shadow var(--ui-transition),
    background var(--ui-transition),
    transform var(--ui-transition);
}

.login-form :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #b9ccd9 inset;
}

.login-form :deep(.el-input__wrapper.is-focus) {
  background: #fff;
  box-shadow:
    0 0 0 1px #0e806f inset,
    0 0 0 4px rgba(14, 128, 111, 0.14);
}

.login-form :deep(.el-input__prefix) {
  color: #8496a5;
}

.login-form :deep(.el-form-item__error) {
  padding-top: 6px;
  color: #bd3f4b;
  font-size: 12px;
}

.submit {
  width: 100%;
  min-height: 46px;
  margin-top: 2px;
  border-color: #0e806f;
  border-radius: 6px;
  background: #0e806f;
  color: #fff;
  font-weight: 700;
  transition:
    background var(--ui-transition),
    border-color var(--ui-transition),
    transform var(--ui-transition),
    box-shadow var(--ui-transition);
}

.submit:hover,
.submit:focus {
  border-color: #0b6b5e;
  background: #0b6b5e;
  box-shadow: 0 12px 24px rgba(14, 128, 111, 0.22);
}

.submit:active {
  transform: translateY(1px);
}

.notice {
  margin: 22px 0 0;
  color: #8494a3;
  font-size: 12px;
  line-height: 1.7;
  text-align: center;
}

@keyframes scanSweep {
  0% {
    transform: rotate(0deg);
  }

  100% {
    transform: rotate(360deg);
  }
}

@media (max-width: 980px) {
  .login-shell {
    padding: 24px;
  }

  .login-card {
    min-height: min(640px, calc(100dvh - 48px));
    grid-template-columns: minmax(0, 0.96fr) minmax(340px, 1.04fr);
  }

  .brand-panel {
    padding: 34px;
  }

  .brand-copy h1 {
    font-size: 38px;
  }

  .ops-visual {
    min-height: 220px;
  }

  .form-panel {
    padding: 42px 34px;
  }
}

@media (max-width: 720px) {
  :global(body) {
    min-width: 0;
  }

  .login-shell {
    align-items: start;
    overflow: auto;
    padding: 16px;
  }

  .login-card {
    min-height: 0;
    grid-template-columns: 1fr;
  }

  .brand-panel {
    min-height: 250px;
    padding: 24px;
  }

  .brand-logo img {
    width: min(190px, 68vw);
  }

  .brand-copy {
    margin-top: 22px;
  }

  .brand-kicker {
    margin-bottom: 10px;
  }

  .brand-copy h1 {
    max-width: 14em;
    font-size: clamp(28px, 8vw, 36px);
  }

  .brand-description {
    max-width: 29em;
    margin-top: 12px;
    font-size: 13px;
  }

  .ops-visual {
    position: absolute;
    right: -52px;
    bottom: -58px;
    width: 260px;
    min-height: 210px;
    margin: 0;
    opacity: 0.45;
  }

  .brand-flow {
    display: none;
  }

  .form-panel {
    padding: 30px 22px 32px;
  }

  .form-heading h2 {
    font-size: 27px;
  }

  .form-heading p {
    margin-bottom: 26px;
  }
}

@media (max-width: 420px) {
  .login-shell {
    padding: 0;
  }

  .login-card {
    width: 100%;
    min-height: 100dvh;
    border: 0;
    border-radius: 0;
  }

  .brand-panel {
    min-height: 232px;
    padding: 22px;
  }

  .form-panel {
    padding: 28px 20px 34px;
  }

  .form-content {
    width: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 1ms !important;
  }
}
</style>
