<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api, errorMessage } from '@/api/client'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import type { components } from '@/types/api.generated'

const emit = defineEmits<{ close: []; saved: [] }>()
const settings = ref<components['schemas']['UserLlmConfigOut'] | null>(null)
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const error = ref('')
const form = reactive({ base_url: '', model_id: '', api_key: '', allow_insecure_http: false })
const dirty = computed(() => !settings.value?.configured || !!form.api_key ||
  form.base_url !== settings.value.base_url || form.model_id !== settings.value.model_id ||
  form.allow_insecure_http !== settings.value.allow_insecure_http)

function apply(value: components['schemas']['UserLlmConfigOut']) {
  settings.value = value
  Object.assign(form, { base_url: value.base_url, model_id: value.model_id, api_key: '', allow_insecure_http: value.allow_insecure_http })
}

async function load() {
  loading.value = true
  error.value = ''
  try { apply((await api.get('/model-providers/personal-llm')).data) }
  catch (cause) { error.value = errorMessage(cause) }
  finally { loading.value = false }
}

async function save() {
  saving.value = true
  try {
    apply((await api.put('/model-providers/personal-llm', { ...form, api_key: form.api_key || null })).data)
    ElMessage.success('个人 LLM 配置已保存')
    emit('saved')
  } catch (cause) { ElMessage.error(errorMessage(cause)) }
  finally { saving.value = false }
}

async function test() {
  testing.value = true
  try {
    await api.post('/model-providers/personal-llm/connection-test', undefined, { timeout: 0 })
    ElMessage.success('个人 LLM 连接成功')
  } catch (cause) { ElMessage.error(errorMessage(cause)) }
  finally { testing.value = false }
}

async function reset() {
  try {
    await ElMessageBox.confirm('清除个人配置后，新任务将使用系统默认模型；已提交的任务继续使用提交时的配置。', '恢复系统默认', { confirmButtonText: '恢复默认', cancelButtonText: '取消' })
    saving.value = true
    await api.delete('/model-providers/personal-llm')
    await load()
    ElMessage.success('已恢复系统默认模型')
    emit('saved')
  } catch (cause) {
    if (cause !== 'cancel' && cause !== 'close') ElMessage.error(errorMessage(cause))
  } finally { saving.value = false }
}

onMounted(load)
</script>

<template>
  <el-dialog :model-value="true" title="我的 LLM 配置" width="min(660px, 94vw)" :close-on-click-modal="false" @close="emit('close')">
    <div v-loading="loading">
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <template v-else-if="settings">
        <p class="muted">仅对当前账号的智能用例和智能助手生效。未配置时使用系统默认模型：{{ settings.default_model || '尚未配置' }}。</p>
        <el-form label-position="left" label-width="110px" @submit.prevent="save">
          <el-form-item label="服务地址" required><el-input v-model="form.base_url" aria-label="LLM 服务地址" placeholder="https://api.example.com/v1" /></el-form-item>
          <el-form-item label="模型 ID" required><el-input v-model="form.model_id" aria-label="LLM 模型 ID" placeholder="输入对话模型 ID" /></el-form-item>
          <el-form-item label="API Key"><el-input v-model="form.api_key" aria-label="LLM API Key" type="password" show-password autocomplete="new-password" :placeholder="settings.has_api_key ? '已保存，留空保留现有密钥' : '服务无需鉴权时可留空'" /></el-form-item>
          <p v-if="settings.has_api_key && form.base_url !== settings.base_url" class="muted">修改服务地址后，旧密钥会被清除，请按需填写新密钥。</p>
          <el-form-item v-if="form.base_url.trim().startsWith('http://')"><el-checkbox v-model="form.allow_insecure_http">允许 HTTP 明文传输到当前受控内网服务</el-checkbox></el-form-item>
        </el-form>
      </template>
    </div>
    <template #footer>
      <div class="personal-llm-actions">
        <el-button v-if="settings?.configured" :disabled="saving || testing" @click="reset">恢复系统默认</el-button>
        <el-button :disabled="!settings?.configured || dirty || saving" :loading="testing" @click="test">测试连接</el-button>
        <el-button @click="emit('close')">关闭</el-button>
        <el-button type="primary" :loading="saving" :disabled="loading || !!error || testing || !form.base_url.trim() || !form.model_id.trim()" @click="save">保存配置</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.personal-llm-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.personal-llm-actions .el-button{margin-left:0}
</style>
