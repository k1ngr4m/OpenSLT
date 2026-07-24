<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { CopyDocument, Delete, Document, EditPen, Plus, RefreshRight, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import OrderConfigNodeEditor from '@/components/OrderConfigNodeEditor.vue'
import type { OrderConfigDetail, OrderConfigFile, XmlNode } from '@/types/orderConfig'
import { formatBytes, parseDocument, prepareTree, serializeDocument } from '@/utils/orderConfigXml'

const props = defineProps<{ resourceId: number; active: boolean; resourceType?: 'order' | 'parser' }>()
const loaded = ref(false)
const loadingList = ref(false)
const loadingDetail = ref(false)
const saving = ref(false)
const files = ref<OrderConfigFile[]>([])
const tool = ref('')
const directory = ref('')
const simulated = ref(false)
const search = ref('')
const selectedName = ref('')
const checksum = ref('')
const modifiedAt = ref('')
const size = ref(0)
const declaration = ref('<?xml version="1.0" encoding="utf-8"?>')
const documentTree = ref<XmlNode | null>(null)
const rawContent = ref('')
const editorMode = ref<'structured' | 'raw'>('structured')
const dirty = ref(false)
const panelError = ref('')
const xmlError = ref('')
const createDialog = ref(false)
const createLoading = ref(false)
const createForm = reactive({ name: '', source_name: '' })

const filteredFiles = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return keyword ? files.value.filter(item => item.name.toLowerCase().includes(keyword)) : files.value
})
const selectedFile = computed(() => files.value.find(item => item.name === selectedName.value))
const prefix = computed(() => tool.value === 'ees_zf_trader_binary_api_test'
  ? 'ees_zf_trader_api_test_conf'
  : props.resourceType === 'parser' ? tool.value : 'ees_ef_vi_trader_api_test_conf')

function endpoint(filename?: string) {
  const base = `/resources/${props.resourceId}/${props.resourceType === 'parser' ? 'parser-configs' : 'order-configs'}`
  return filename ? `${base}/${encodeURIComponent(filename)}` : base
}

function applyDetail(detail: OrderConfigDetail) {
  selectedName.value = detail.name
  checksum.value = detail.checksum
  modifiedAt.value = detail.modified_at
  size.value = detail.size
  declaration.value = detail.declaration
  documentTree.value = prepareTree(detail.document)
  rawContent.value = detail.content
  simulated.value = detail.simulated
  tool.value = detail.tool
  dirty.value = false
  xmlError.value = ''
}

async function confirmDiscard() {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前配置有未保存的修改，继续将丢弃这些修改。', '未保存修改', {
      type: 'warning',
      confirmButtonText: '放弃修改',
      cancelButtonText: '继续编辑',
    })
    return true
  } catch {
    return false
  }
}

async function loadDetail(filename: string) {
  loadingDetail.value = true
  panelError.value = ''
  try {
    const { data } = await api.get<OrderConfigDetail>(endpoint(filename))
    applyDetail(data)
  } catch (error) {
    panelError.value = errorMessage(error)
    ElMessage.error(panelError.value)
  } finally {
    loadingDetail.value = false
  }
}

async function selectFile(filename: string) {
  if (filename === selectedName.value) return
  if (!await confirmDiscard()) return
  await loadDetail(filename)
}

async function loadFiles(selectName?: string) {
  loadingList.value = true
  panelError.value = ''
  try {
    const { data } = await api.get(endpoint())
    files.value = data.files
    tool.value = data.tool
    directory.value = data.directory
    simulated.value = data.simulated
    loaded.value = true
    const target = selectName || (files.value.some(item => item.name === selectedName.value) ? selectedName.value : files.value[0]?.name)
    if (target) await loadDetail(target)
    else {
      selectedName.value = ''
      documentTree.value = null
      rawContent.value = ''
      dirty.value = false
    }
  } catch (error) {
    panelError.value = errorMessage(error)
  } finally {
    loadingList.value = false
  }
}

async function refresh() {
  if (!await confirmDiscard()) return
  await loadFiles(selectedName.value)
}

function structureChanged() {
  dirty.value = true
  xmlError.value = ''
}

function rawChanged() {
  dirty.value = true
  xmlError.value = ''
}

function changeEditorMode(mode: 'structured' | 'raw') {
  if (mode === 'raw') {
    if (documentTree.value) rawContent.value = serializeDocument(declaration.value, documentTree.value)
    xmlError.value = ''
    return
  }
  try {
    const parsed = parseDocument(rawContent.value)
    declaration.value = parsed.declaration
    documentTree.value = parsed.document
    xmlError.value = ''
  } catch (error: any) {
    editorMode.value = 'raw'
    xmlError.value = error?.message || 'XML 格式错误'
    ElMessage.error(xmlError.value)
  }
}

async function save() {
  if (!selectedName.value || !documentTree.value) return
  let content = rawContent.value
  try {
    if (editorMode.value === 'structured') content = serializeDocument(declaration.value, documentTree.value)
    const parsed = parseDocument(content)
    if (editorMode.value === 'raw') {
      declaration.value = parsed.declaration
      documentTree.value = parsed.document
    }
    xmlError.value = ''
  } catch (error: any) {
    xmlError.value = error?.message || 'XML 格式错误'
    ElMessage.error(xmlError.value)
    return
  }
  saving.value = true
  try {
    const { data } = await api.put<OrderConfigDetail>(endpoint(selectedName.value), {
      content,
      expected_checksum: checksum.value,
    })
    applyDetail(data)
    await loadFiles(data.name)
    ElMessage.success('配置已保存')
  } catch (error: any) {
    if (error?.response?.data?.code === 'ORDER_CONFIG_CHANGED') {
      panelError.value = '远端配置已被修改。本地内容仍保留，请先复制内容或重新加载。'
    } else panelError.value = errorMessage(error)
    ElMessage.error(panelError.value)
  } finally {
    saving.value = false
  }
}

function suggestedName() {
  const now = new Date()
  const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}-${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`
  return `${prefix.value}-scenario-${stamp}.xml`
}

function openCreate() {
  if (!files.value.length) {
    ElMessage.warning('远端目录中没有可用的模板配置')
    return
  }
  createForm.source_name = selectedName.value || files.value[0].name
  createForm.name = suggestedName()
  createDialog.value = true
}

async function createConfig() {
  if (!createForm.name.trim() || !createForm.source_name) return
  if (!await confirmDiscard()) return
  createLoading.value = true
  try {
    const { data } = await api.post<OrderConfigDetail>(endpoint(), {
      name: createForm.name.trim(),
      source_name: createForm.source_name,
    })
    createDialog.value = false
    await loadFiles(data.name)
    ElMessage.success('配置已创建')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    createLoading.value = false
  }
}

async function renameConfig() {
  if (!selectedName.value || dirty.value) return
  try {
    const { value } = await ElMessageBox.prompt('请输入新的 XML 文件名', '重命名配置', {
      inputValue: selectedName.value,
      confirmButtonText: '重命名',
      inputValidator: value => Boolean(value.trim()) || '文件名不能为空',
    })
    const { data } = await api.patch<OrderConfigDetail>(endpoint(selectedName.value), {
      new_name: value.trim(),
      expected_checksum: checksum.value,
    })
    await loadFiles(data.name)
    ElMessage.success('配置已重命名')
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  }
}

async function deleteConfig() {
  if (!selectedName.value || dirty.value) return
  try {
    await ElMessageBox.confirm(`配置“${selectedName.value}”将移入远端隐藏回收目录。`, '删除配置', {
      type: 'warning',
      confirmButtonText: '移入回收目录',
    })
    await api.delete(endpoint(selectedName.value), { params: { expected_checksum: checksum.value } })
    const oldName = selectedName.value
    selectedName.value = ''
    await loadFiles()
    ElMessage.success(`已删除 ${oldName}`)
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  }
}

async function revert() {
  if (!selectedName.value || !await confirmDiscard()) return
  await loadDetail(selectedName.value)
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

watch(() => props.active, active => {
  if (active && !loaded.value) loadFiles()
})

onBeforeRouteLeave(async () => await confirmDiscard())
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <section class="config-workspace">
    <aside class="config-sidebar">
      <div class="sidebar-heading">
        <div><strong>配置文件</strong><small>{{ files.length }} 个</small></div>
        <el-button :icon="RefreshRight" text circle :loading="loadingList" title="刷新" aria-label="刷新配置列表" @click="refresh" />
      </div>
      <el-input v-model="search" :prefix-icon="Search" clearable placeholder="搜索文件名" />
      <div v-if="loadingList && !loaded" class="file-skeleton"><el-skeleton :rows="5" animated /></div>
      <div v-else-if="!filteredFiles.length" class="file-empty">
        <el-icon><Document /></el-icon><span>{{ search ? '没有匹配的配置' : '远端目录暂无配置' }}</span>
      </div>
      <div v-else class="file-list">
        <button v-for="file in filteredFiles" :key="file.name" type="button" class="file-item" :class="{ active: file.name === selectedName }" @click="selectFile(file.name)">
          <el-icon><Document /></el-icon>
          <span><strong>{{ file.name }}</strong><small>{{ formatBytes(file.size) }} · {{ new Date(file.modified_at).toLocaleString() }}</small></span>
        </button>
      </div>
      <el-button :icon="Plus" class="create-button" :disabled="!files.length" @click="openCreate">复制新建配置</el-button>
    </aside>

    <main class="config-editor">
      <div v-if="panelError" class="config-alert">{{ panelError }}</div>
      <div v-if="!selectedName && !loadingDetail" class="editor-empty">
        <el-icon><Document /></el-icon><strong>选择一个配置文件</strong><span>从左侧列表打开并编辑远端 XML 配置。</span>
      </div>
      <template v-else>
        <div class="editor-toolbar">
          <div class="file-title">
            <div><strong class="mono">{{ selectedName }}</strong><el-tag v-if="dirty" type="warning" effect="plain">未保存</el-tag></div>
            <small>{{ formatBytes(size) }} · {{ modifiedAt ? new Date(modifiedAt).toLocaleString() : '' }} · <span class="mono">{{ checksum.slice(0, 12) }}</span></small>
          </div>
          <div class="editor-actions">
            <el-button :icon="CopyDocument" :disabled="!selectedName" @click="openCreate">复制</el-button>
            <el-button :icon="EditPen" :disabled="!selectedName || dirty" @click="renameConfig">重命名</el-button>
            <el-button :icon="Delete" type="danger" plain :disabled="!selectedName || dirty" @click="deleteConfig">删除</el-button>
          </div>
        </div>

        <div class="editor-modebar">
          <el-radio-group v-model="editorMode" size="small" @change="value => changeEditorMode(value as 'structured' | 'raw')">
            <el-radio-button value="structured">结构化编辑</el-radio-button>
            <el-radio-button value="raw">XML 原文</el-radio-button>
          </el-radio-group>
          <div class="mode-meta"><el-tag :type="simulated ? 'warning' : 'success'" effect="plain">{{ simulated ? '模拟配置' : '远端文件' }}</el-tag><span class="mono">{{ directory }}</span></div>
        </div>

        <div v-if="xmlError" class="xml-error">{{ xmlError }}</div>
        <div v-loading="loadingDetail" class="editor-body">
          <div v-if="editorMode === 'structured' && documentTree" class="structured-editor">
            <OrderConfigNodeEditor :node="documentTree" @changed="structureChanged" />
          </div>
          <el-input v-else v-model="rawContent" type="textarea" :rows="26" resize="none" spellcheck="false" class="raw-editor" @input="rawChanged" />
        </div>

        <footer class="editor-footer">
          <span>{{ dirty ? '修改尚未写入远端文件' : '当前内容已与远端同步' }}</span>
          <div><el-button :disabled="!dirty" @click="revert">放弃修改</el-button><el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存配置</el-button></div>
        </footer>
      </template>
    </main>

    <el-dialog v-model="createDialog" title="复制新建配置" width="560px">
      <el-form label-width="90px">
        <el-form-item label="模板配置" required>
          <el-select v-model="createForm.source_name" style="width:100%">
            <el-option v-for="file in files" :key="file.name" :label="file.name" :value="file.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="新文件名" required><el-input v-model="createForm.name" class="mono" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createDialog = false">取消</el-button><el-button type="primary" :loading="createLoading" @click="createConfig">创建配置</el-button></template>
    </el-dialog>
  </section>
</template>

<style scoped>
.config-workspace{display:grid;grid-template-columns:minmax(250px,290px) minmax(0,1fr);min-height:660px;border:1px solid #dfe6ec;border-radius:8px;overflow:hidden;background:#fff}.config-sidebar{display:flex;flex-direction:column;gap:12px;padding:16px;border-right:1px solid #e2e8ee;background:#f6f8fa}.sidebar-heading{display:flex;align-items:center;justify-content:space-between}.sidebar-heading>div{display:flex;align-items:baseline;gap:8px}.sidebar-heading strong{font-size:14px}.sidebar-heading small{color:#87939e}.file-list{display:flex;flex-direction:column;gap:4px;overflow:auto;max-height:535px}.file-item{display:grid;grid-template-columns:18px minmax(0,1fr);gap:8px;align-items:start;width:100%;padding:10px;border:1px solid transparent;border-radius:6px;background:transparent;color:#3c4b59;text-align:left;cursor:pointer;transition:background .2s,border-color .2s}.file-item:hover{background:#edf2f5}.file-item.active{background:#e4f1ed;border-color:#b9d8cf;color:#1e6151}.file-item span{min-width:0}.file-item strong,.file-item small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-item strong{font:12px/1.5 Cascadia Code,Consolas,monospace}.file-item small{margin-top:3px;color:#87939e;font-size:10px}.create-button{margin-top:auto}.file-empty,.editor-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#8a97a3}.file-empty{min-height:180px;font-size:12px}.file-empty :deep(svg),.editor-empty :deep(svg){width:28px;height:28px}.config-editor{display:flex;min-width:0;flex-direction:column;padding:18px 20px}.config-alert,.xml-error{padding:9px 12px;border-radius:5px;font-size:12px}.config-alert{margin-bottom:10px;background:#fff3d9;color:#805d1e}.xml-error{margin-bottom:8px;background:#fdecee;color:#a53c4d}.editor-empty{flex:1}.editor-empty strong{color:#4b5a68}.editor-empty span{font-size:12px}.editor-toolbar,.editor-modebar,.editor-footer{display:flex;align-items:center;justify-content:space-between;gap:14px}.editor-toolbar{padding-bottom:13px;border-bottom:1px solid #e7ecf0}.file-title{min-width:0}.file-title>div{display:flex;align-items:center;gap:8px}.file-title strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.file-title small{display:block;margin-top:5px;color:#82909c}.editor-actions{display:flex;gap:8px}.editor-modebar{padding:12px 0}.mode-meta{display:flex;align-items:center;gap:9px;min-width:0;color:#7c8994;font-size:11px}.mode-meta .mono{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.editor-body{min-height:500px;max-height:585px;overflow:auto;border:1px solid #e2e8ed;border-radius:6px;background:#fbfcfd}.structured-editor{padding:14px}.raw-editor{height:100%}.raw-editor :deep(.el-textarea__inner){min-height:583px!important;border:0;border-radius:0;background:#111827;color:#dce6ee;font:12px/1.6 Cascadia Code,Consolas,monospace;box-shadow:none}.editor-footer{padding-top:12px;color:#7f8b96;font-size:12px}.editor-footer>div{display:flex;gap:8px}.mono{font-family:Cascadia Code,Consolas,monospace}
</style>
