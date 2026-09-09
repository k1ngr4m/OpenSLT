<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
const promptVisible = ref(false)
const promptLoading = ref(false)
const promptSaving = ref(false)
const promptReady = ref(false)
const promptForm = reactive({ system_prompt: '', user_prompt: '' })
const promptDefaults = reactive({ system_prompt: '', user_prompt: '' })
const promptVariables = ['requirement_no', 'requirement_name', 'source_path', 'revision', 'references']

async function openPrompt() {
  promptVisible.value = true
  promptReady.value = false
  promptLoading.value = true
  try {
    const { data } = await api.get('/smart-cases/generation-prompt')
    Object.assign(promptForm, { system_prompt: data.system_prompt, user_prompt: data.user_prompt })
    Object.assign(promptDefaults, { system_prompt: data.default_system_prompt, user_prompt: data.default_user_prompt })
    promptReady.value = true
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { promptLoading.value = false }
}

async function savePrompt() {
  promptSaving.value = true
  try {
    await api.put('/smart-cases/generation-prompt', promptForm)
    ElMessage.success('用例生成提示词已保存')
    promptVisible.value = false
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { promptSaving.value = false }
}

</script>
<template><el-button @click="openPrompt">用例提示词</el-button>
    <el-dialog v-model="promptVisible" title="用例生成提示词" width="min(860px, 94vw)" :close-on-click-modal="false">
      <el-form v-loading="promptLoading" label-position="left" label-width="var(--ui-field-label-width)" :disabled="!promptReady || promptSaving" @submit.prevent="savePrompt">
        <el-form-item label="System 提示词" required><el-input v-model="promptForm.system_prompt" type="textarea" :rows="4" :maxlength="20000" /></el-form-item>
        <el-form-item label="User 提示词" required><el-input v-model="promptForm.user_prompt" type="textarea" :rows="12" :maxlength="30000" /><p class="field-help">支持变量：<code v-for="variable in promptVariables" :key="variable" v-text="'{{' + variable + '}} '" />。references 为必填变量，用于插入需求正文与检索资料。请保留默认提示词中的 JSON 输出结构。</p></el-form-item>
      </el-form>
      <template #footer><el-button :disabled="!promptReady || promptSaving" @click="Object.assign(promptForm, promptDefaults)">恢复默认</el-button><el-button :disabled="promptSaving" @click="promptVisible = false">取消</el-button><el-button type="primary" :disabled="!promptReady" :loading="promptSaving" @click="savePrompt">保存提示词</el-button></template>
    </el-dialog>
</template>
