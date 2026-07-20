import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api/client'

export interface User { id: number; username: string; display_name: string; role: 'admin' | 'tester' | 'visitor'; is_active: boolean }

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const accessToken = ref(localStorage.getItem('access_token') || '')
  const loggedIn = computed(() => Boolean(accessToken.value))
  const canOperate = computed(() => user.value?.role === 'admin' || user.value?.role === 'tester')
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function login(username: string, password: string) {
    const { data } = await api.post('/auth/login', { username, password })
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    accessToken.value = data.access_token
    await loadUser()
  }

  async function loadUser() {
    if (!accessToken.value) return
    const { data } = await api.get('/auth/me')
    user.value = data
  }

  async function logout() {
    try { await api.post('/auth/logout', { refresh_token: localStorage.getItem('refresh_token') }) }
    finally {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      accessToken.value = ''
      user.value = null
    }
  }

  return { user, loggedIn, canOperate, isAdmin, login, loadUser, logout }
})
