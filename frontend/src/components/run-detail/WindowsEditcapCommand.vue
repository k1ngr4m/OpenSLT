<script setup lang="ts">
import { CopyDocument } from '@element-plus/icons-vue'
import { copyText } from '@/utils/clipboard'
import { ElMessage } from '@/ui/elementPlusServices'

const props = defineProps<{ command: string }>()

async function copyCommand() {
  try {
    await copyText(props.command)
    ElMessage.success('Windows editcap 命令已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择命令')
  }
}
</script>

<template>
  <section v-if="command" class="windows-editcap-command">
    <div class="windows-editcap-heading">
      <div>
        <strong>Windows editcap 命令</strong>
        <small>复制到当前操作员 Windows 电脑的 CMD 或 PowerShell 执行，成功后再完成节点。</small>
      </div>
      <el-button :icon="CopyDocument" @click="copyCommand">复制命令</el-button>
    </div>
    <code>{{ command }}</code>
  </section>
</template>

<style scoped>
.windows-editcap-command {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding: 14px;
  border: 1px solid #d7e0e7;
  border-radius: 10px;
  background: #f7f9fb;
}
.windows-editcap-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.windows-editcap-heading > div {
  display: grid;
  gap: 4px;
}
.windows-editcap-heading small {
  color: #667085;
}
code {
  overflow-wrap: anywhere;
  padding: 12px;
  border-radius: 8px;
  color: #d8e1e8;
  background: #111827;
  font-family: 'Cascadia Code', Consolas, monospace;
  line-height: 1.6;
}
</style>
