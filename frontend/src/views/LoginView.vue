<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { errorMessage } from '@/api/client'

const username = ref('')
const password = ref('')
const loading = ref(false)
const auth = useAuthStore()
const router = useRouter()

async function submit() {
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push('/dashboard')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login">
    <section class="hero" aria-labelledby="platform-title">
      <div class="hero-copy">
        <p class="hero-kicker">OpenSLT 测速控制台</p>
        <h1 id="platform-title"><span>让复杂测速链路</span><span>保持清晰、可控、可追溯</span></h1>
        <p class="hero-description">统一管理测速资源、运行流程、实时日志和结果归档。</p>
        <div class="capability-list" aria-label="平台能力">
          <span>资源预检</span><i></i><span>人工确认</span><i></i><span>自动执行</span><i></i><span>结果复核</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="form">
        <div class="logo" aria-hidden="true">SL</div>
        <span class="form-kicker">内部系统</span>
        <h2>登录 OpenSLT</h2>
        <p class="muted">使用管理员分配的账号进入平台</p>
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名">
            <el-input v-model="username" size="large" autofocus />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="password"
              type="password"
              size="large"
              show-password
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            class="submit"
            @click="submit"
          >
            登录
          </el-button>
        </el-form>
        <p class="notice">初始账号登录后请立即修改默认密码</p>
      </div>
    </section>
  </main>
</template>

<style scoped>
.login {
  min-height: 100dvh;
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(28rem, 1fr);
  background: #fff;
}

.hero {
  position: relative;
  isolation: isolate;
  display: grid;
  place-items: center start;
  overflow: hidden;
  padding: clamp(3rem, 7vw, 7rem);
  color: #fff;
  background: #0b3639;
}

.hero::before,
.hero::after {
  position: absolute;
  z-index: -1;
  content: '';
  pointer-events: none;
}

.hero::before {
  inset: 0;
  opacity: 0.16;
  background-image:
    linear-gradient(rgba(128, 231, 214, 0.16) 1px, transparent 1px),
    linear-gradient(90deg, rgba(128, 231, 214, 0.16) 1px, transparent 1px);
  background-size: 4.5rem 4.5rem;
  mask-image: linear-gradient(to bottom right, transparent 8%, #000 62%, transparent 96%);
}

.hero::after{right:6%;bottom:8%;width:42%;height:1px;background:linear-gradient(90deg,transparent,rgba(111,218,199,.36),transparent);box-shadow:0 -80px 0 rgba(111,218,199,.08),0 -160px 0 rgba(111,218,199,.05)}

.hero-copy{max-width:46rem}
.hero-kicker{margin:0 0 24px;color:#71dac7;font-size:12px;font-weight:650;letter-spacing:.14em}

.hero h1 {
  margin: 0;
  font-size: clamp(2.65rem, 4.4vw, 4.7rem);
  font-weight: 650;
  line-height: 1.14;
  letter-spacing: -.045em;
  text-wrap: balance;
  text-shadow: 0 0.12em 0 rgba(1, 31, 32, 0.24);
}

.hero h1 span {
  display: block;
  white-space: nowrap;
}

.hero-description{max-width:38rem;margin:24px 0 0;color:#b8d2d4;font-size:16px;line-height:1.7}
.capability-list{display:flex;align-items:center;gap:12px;margin-top:46px;color:#d7e8e9;font-size:11px;font-weight:550}.capability-list i{width:32px;height:1px;background:#527b7e}

.panel {
  display: grid;
  place-items: center;
  padding: clamp(2.5rem, 6vw, 5rem);
}

.form {
  width: min(100%, 22.5rem);
}

.form-kicker{display:block;margin-top:22px;color:var(--ui-primary);font-size:11px;font-weight:650;letter-spacing:.1em}

.logo {
  width: 3rem;
  height: 3rem;
  display: grid;
  place-items: center;
  color: #fff;
  background: #0c8674;
  border-radius: 0.5rem;
  font-weight: 800;
}

.form h2 {
  margin: .4rem 0 0.5rem;
  font-size: 1.75rem;
}

.form .muted {
  margin-bottom: 2rem;
}

.submit {
  width: 100%;
  background: #0d8c78;
  border-color: #0d8c78;
}

.submit:hover{background:var(--ui-primary-hover);border-color:var(--ui-primary-hover)}

.notice {
  margin-top: 1.5rem;
  color: #94a3b8;
  font-size: 0.75rem;
  text-align: center;
}

@media (max-width: 52rem) {
  :global(body) {
    min-width: 0;
  }

  .login {
    grid-template-columns: 1fr;
  }

  .hero {
    min-height: 38dvh;
    place-items: end start;
    padding: 2.5rem 1.5rem;
  }

  .hero-description,.capability-list{display:none}

  .hero h1 {
    font-size: clamp(2.5rem, 12vw, 4.25rem);
  }

  .panel {
    place-items: start center;
    padding: 2.5rem 1.5rem 3rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  .submit {
    transition: none;
  }
}
</style>
