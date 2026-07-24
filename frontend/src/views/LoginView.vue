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
      <h1 id="platform-title"><span>盛立自动化</span><span>测试平台</span></h1>
    </section>

    <section class="panel">
      <div class="form">
        <div class="logo" aria-hidden="true">SL</div>
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
            安全登录
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
  place-items: center;
  overflow: hidden;
  padding: clamp(3rem, 7vw, 7rem);
  color: #fff;
  background: #073f40;
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

.hero::after {
  width: min(48vw, 42rem);
  aspect-ratio: 1;
  right: -24%;
  bottom: -42%;
  border: 1px solid rgba(114, 225, 205, 0.2);
  border-radius: 50%;
  box-shadow:
    0 0 0 6rem rgba(114, 225, 205, 0.025),
    0 0 0 12rem rgba(114, 225, 205, 0.02);
}

.hero h1 {
  margin: 0;
  font-size: clamp(3rem, 5.3vw, 5.75rem);
  font-weight: 700;
  line-height: 1.16;
  letter-spacing: 0;
  text-wrap: balance;
  text-shadow: 0 0.12em 0 rgba(1, 31, 32, 0.24);
}

.hero h1 span {
  display: block;
  white-space: nowrap;
}

.panel {
  display: grid;
  place-items: center;
  padding: clamp(2.5rem, 6vw, 5rem);
}

.form {
  width: min(100%, 22.5rem);
}

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
  margin: 1.5rem 0 0.5rem;
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
