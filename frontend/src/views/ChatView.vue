<script setup lang="ts">
import MarkdownContent from '@/components/MarkdownContent.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessageBox } from '@/ui/elementPlusServices'
import { ChatDotRound, Plus, Delete, Promotion, VideoPause, Refresh } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { sendChatMessage, type ChatAttachment, type ChatConversation, type ChatMessage, type ChatStatus } from '@/api/chat'
import type { components } from '@/types/api.generated'

const knowledgeBases = ref<components['schemas']['KnowledgeBaseOut'][]>([])
const knowledgeBaseId = ref<number | null>(null)
const conversations = ref<ChatConversation[]>([])
const selected = ref<ChatConversation | null>(null)
const messages = ref<ChatMessage[]>([])
const status = ref<ChatStatus | null>(null)
const providers = ref<components['schemas']['ModelProviderOut'][]>([])
const selectedModelId = ref<number | null>(null)
const activeModelId = computed(() => providers.value.flatMap(provider => provider.models || []).find(model => model.is_active)?.id ?? null)
const attachments = ref<ChatAttachment[]>([])
const parsing = ref(false)
const fileInput = ref<HTMLInputElement>()
const addMenu = ref<HTMLDetailsElement>()
let uploadController: AbortController | undefined
async function uploadAttachments(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length || busy.value) return
  if (attachments.value.length + files.length > 3) { error.value = '每条消息最多添加 3 个附件'; return }
  parsing.value = true; error.value = ''
  if (addMenu.value) addMenu.value.open = false
  uploadController = new AbortController()
  try {
    for (const file of files) {
      const data = new FormData(); data.append('file', file)
      const response = await api.post<ChatAttachment>('/chat/attachments/parse', data, { timeout: 0, signal: uploadController.signal })
      if (!disposed) attachments.value.push(response.data)
    }
  } catch (cause) { if (!disposed) error.value = errorMessage(cause) }
  finally { parsing.value = false; uploadController = undefined }
}
const draft = ref('')
const error = ref('')
const loading = ref(false)
const sending = ref(false)
const stopping = ref(false)
const moreConversations = ref(false)
const moreMessages = ref(false)
const feed = ref<HTMLElement>()
const composer = ref<HTMLTextAreaElement>()
const busy = computed(() => parsing.value || sending.value || messages.value.some(item => item.status === 'running'))
const currentMode = computed(() => knowledgeBaseId.value ? 'knowledge' : 'general')
const readinessError = computed(() => !status.value ? '正在检查模型配置…' :
  currentMode.value === 'knowledge' ? status.value.knowledge_error : status.value.general_error)
const statusLabel: Record<string, string> = { running: '生成中', completed: '已完成', cancelled: '已停止', failed: '生成失败' }
let controller: AbortController | undefined
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false

async function loadConversations(append = false) {
  const { data } = await api.get<ChatConversation[]>('/chat/conversations', { params: { offset: append ? conversations.value.length : 0 } })
  conversations.value = append ? [...conversations.value, ...data] : data
  moreConversations.value = data.length === 50
}

function scheduleRefresh() {
  clearTimeout(refreshTimer)
  if (disposed || sending.value || !messages.value.some(item => item.status === 'running')) return
  refreshTimer = setTimeout(() => { void refreshMessages() }, 2000)
}

async function refreshMessages(older = false) {
  if (!selected.value) return
  try {
    const { data } = await api.get<ChatMessage[]>(`/chat/conversations/${selected.value.id}/messages`, {
      params: older ? { before_id: messages.value[0]?.id } : {},
    })
    messages.value = older ? [...data, ...messages.value] : data
    moreMessages.value = data.length === 50
    scheduleRefresh()
  } catch (cause) { error.value = errorMessage(cause) }
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [response, modelResponse] = await Promise.all([
      api.get<ChatStatus>('/chat/status', { params: { knowledge_base_id: knowledgeBaseId.value ?? undefined } }),
      api.get<components['schemas']['ModelProviderOut'][]>('/model-providers', { params: { kind: 'chat' } }),
      loadConversations(),
    ])
    status.value = response.data
    providers.value = modelResponse.data
    selectedModelId.value = activeModelId.value
    knowledgeBases.value = (await api.get<components['schemas']['KnowledgeBaseOut'][]>('/knowledge-bases')).data
    await refreshMessages()
  } catch (cause) { error.value = errorMessage(cause) }
  finally { loading.value = false }
}

async function switchModel() {
  if (busy.value || loading.value || selectedModelId.value === null) return
  loading.value = true
  error.value = ''
  try {
    await api.post(`/model-providers/models/${selectedModelId.value}/activate`)
    for (const provider of providers.value) {
      for (const model of provider.models || []) model.is_active = model.id === selectedModelId.value
    }
    status.value = null
    status.value = (await api.get<ChatStatus>('/chat/status', { params: { knowledge_base_id: knowledgeBaseId.value ?? undefined } })).data
  } catch (cause) {
    selectedModelId.value = activeModelId.value
    error.value = errorMessage(cause)
  } finally { loading.value = false }
}

async function selectConversation(item: ChatConversation) {
  if (busy.value || loading.value) return
  clearTimeout(refreshTimer)
  selected.value = item
  knowledgeBaseId.value = item.knowledge_base_id ?? null
  attachments.value = []
  messages.value = []
  draft.value = ''
  error.value = ''
  loading.value = true
  await refreshMessages()
  loading.value = false
  await scrollToBottom(true)
}

function newConversation() {
  if (busy.value || loading.value) return
  clearTimeout(refreshTimer)
  selected.value = null
  knowledgeBaseId.value = null
  attachments.value = []
  messages.value = []
  moreMessages.value = false
  draft.value = ''
  error.value = ''
  void nextTick(() => composer.value?.focus())
}

async function removeConversation(item: ChatConversation) {
  if (busy.value || loading.value) return
  try {
    await ElMessageBox.confirm(`删除“${item.title}”及其全部消息？`, '删除对话', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  loading.value = true
  try {
    await api.delete(`/chat/conversations/${item.id}`)
    if (selected.value?.id === item.id) { selected.value = null; messages.value = []; moreMessages.value = false }
    await loadConversations()
  } catch (cause) { error.value = errorMessage(cause) }
  finally { loading.value = false }
}

async function scrollToBottom(force = false) {
  const nearBottom = !feed.value || feed.value.scrollHeight - feed.value.scrollTop - feed.value.clientHeight < 100
  await nextTick()
  if (feed.value && (force || nearBottom)) feed.value.scrollTop = feed.value.scrollHeight
}

async function send() {
  const content = draft.value.trim() || (attachments.value.length ? '请分析所附文件。' : '')
  if (!content || busy.value || loading.value || readinessError.value) return
  sending.value = true
  error.value = ''
  controller = new AbortController()
  let received = false
  try {
    if (!selected.value) {
      const { data } = await api.post<ChatConversation>('/chat/conversations', { knowledge_base_id: knowledgeBaseId.value })
      selected.value = data
    }
    let answer: ChatMessage | undefined
    await sendChatMessage(selected.value.id, content, controller.signal, event => {
      if (event.type === 'meta') {
        received = true
        draft.value = ''
        attachments.value = []
        if (selected.value?.title === '新对话') selected.value.title = event.user.content.slice(0, 60)
        messages.value.push(event.user, event.assistant)
        answer = messages.value[messages.value.length - 1]
        void scrollToBottom(true)
      } else if (event.type === 'delta' && answer) answer.content += event.content
      else if (event.type === 'sources' && answer) answer.sources = event.sources
      else if (event.type === 'done' && answer) Object.assign(answer, event.message)
      void scrollToBottom()
    }, attachments.value, knowledgeBaseId.value)
    if (selected.value) selected.value.knowledge_base_id = knowledgeBaseId.value
  } catch (cause) {
    if (!controller.signal.aborted) error.value = errorMessage(cause)
    if (!received) draft.value = content
  } finally {
    sending.value = false
    stopping.value = false
    controller = undefined
    if (!disposed) {
      await refreshMessages()
      await loadConversations().catch(cause => { error.value = errorMessage(cause) })
      composer.value?.focus()
    }
  }
}

async function stop() {
  if (stopping.value) return
  if (!selected.value) { controller?.abort(); return }
  stopping.value = true
  try {
    await api.post(`/chat/conversations/${selected.value.id}/cancel`)
    if (!sending.value) await refreshMessages()
  } catch (cause) {
    controller?.abort()
    error.value = errorMessage(cause)
  } finally { stopping.value = false }
}

function useQuestion(text: string) { draft.value = text; composer.value?.focus() }
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send() }
}

watch(knowledgeBaseId, async () => {
  const baseId = knowledgeBaseId.value
  status.value = null
  try {
    const response = await api.get<ChatStatus>('/chat/status', { params: { knowledge_base_id: baseId ?? undefined } })
    if (baseId === (knowledgeBaseId.value)) status.value = response.data
  } catch (cause) {
    if (baseId === (knowledgeBaseId.value)) error.value = errorMessage(cause)
  }
})
onMounted(() => { void refresh() })
onBeforeUnmount(() => { disposed = true; clearTimeout(refreshTimer); controller?.abort(); uploadController?.abort() })
</script>

<template>
  <section class="chat-page" aria-labelledby="chat-title">
    <header class="chat-heading">
      <div><span class="page-kicker">知识与对话</span><h1 id="chat-title" class="page-title">智能助手</h1></div>
      <el-button :icon="Refresh" :disabled="sending || loading" @click="refresh">刷新</el-button>
    </header>
    <div class="chat-workspace">
      <aside class="conversation-panel" aria-label="我的对话">
        <el-button class="new-chat" type="primary" plain :icon="Plus" :disabled="busy || loading" @click="newConversation">新建对话</el-button>
        <p class="conversation-caption">我的对话 · 仅自己可见</p>
        <div class="conversation-list">
          <p v-if="!conversations.length" class="muted empty-history">发送第一条消息后，历史会保存在这里。</p>
          <div v-for="item in conversations" :key="item.id" class="conversation-row" :class="{ active: selected?.id === item.id }">
            <button type="button" class="conversation-link" :disabled="busy || loading" :aria-current="selected?.id === item.id ? 'true' : undefined" @click="selectConversation(item)">
              <span>{{ item.title }}</span><small>{{ item.knowledge_base_id ? '已关联知识库' : '对话' }}</small>
            </button>
            <el-button text circle :icon="Delete" :disabled="busy || loading" :aria-label="`删除对话：${item.title}`" @click="removeConversation(item)" />
          </div>
          <el-button v-if="moreConversations" text :disabled="busy || loading" @click="loadConversations(true).catch(cause => error = errorMessage(cause))">加载更多对话</el-button>
        </div>
      </aside>

      <section class="dialogue-panel" aria-label="聊天内容" :aria-busy="loading">
        <div class="dialogue-heading"><strong>{{ selected?.title || '开始一段新对话' }}</strong></div>
        <el-alert v-if="error" class="chat-alert" :title="error" type="error" show-icon :closable="false" />
        <el-alert v-if="readinessError && !loading" class="chat-alert" :title="readinessError" type="warning" show-icon :closable="false" />
        <div ref="feed" class="message-feed" tabindex="0" aria-label="消息历史">
          <el-button v-if="moreMessages" text :disabled="busy || loading" @click="refreshMessages(true)">加载更早消息</el-button>
          <div v-if="!messages.length" class="chat-empty">
            <el-icon class="empty-icon"><ChatDotRound /></el-icon>
            <h2>一起梳理问题与资料</h2>
            <p>直接提问，或通过下方“+”上传附件、选择知识库，让回答结合你的资料。</p>
            <div class="suggested-questions">
              <button type="button" @click="useQuestion('整合版二期做市的发单流程是什么？')">了解业务流程</button>
              <button type="button" @click="useQuestion('如何设计断线重连的异常与边界测试？')">梳理测试场景</button>
            </div>
          </div>
          <article v-for="(message, index) in messages" :key="message.id" class="message" :class="message.role">
            <div class="message-meta"><strong>{{ message.role === 'user' ? '你' : '智能助手' }}</strong><span v-if="message.role === 'assistant'">{{ message.model }} · {{ statusLabel[message.status] }}</span></div>
            <MarkdownContent v-if="message.role === 'assistant' && message.content" :content="message.content" />
            <div v-else class="message-content">{{ message.content || (message.status === 'running' ? '正在准备回答…' : '未生成回答') }}</div>
            <details v-for="(file, fileIndex) in message.attachments || []" :key="fileIndex" class="chat-attachment"><summary>{{ file.name }} · 已解析 {{ file.content.length }} / {{ file.total_chars }} 字符</summary><pre>{{ file.content }}</pre></details>
            <p v-if="message.error" class="message-error">{{ message.error }}</p>
            <details v-if="message.sources.length" class="message-sources">
              <summary>参考来源 · {{ message.sources.length }} 个片段</summary>
              <details v-for="source in message.sources" :key="source.id" class="source-item">
                <summary>[{{ source.id }}] {{ source.source_path }} <span>r{{ source.revision }} · 片段 {{ source.chunk_no + 1 }}</span></summary>
                <p>{{ source.content }}</p>
              </details>
            </details>
            <el-button v-if="['failed', 'cancelled'].includes(message.status) && index > 0" text :disabled="busy" @click="useQuestion(messages[index - 1]?.content || '')">重新提问</el-button>
          </article>
        </div>
        <form class="composer" @submit.prevent="send">
          <div v-if="attachments.length" class="pending-attachments"><div v-for="(file, index) in attachments" :key="index" class="pending-file"><details class="chat-attachment"><summary>{{ file.name }} · {{ file.content.length < file.total_chars ? `正文节选 ${file.content.length} / ${file.total_chars} 字符` : `已解析 ${file.total_chars} 字符` }}</summary><pre>{{ file.content }}</pre></details><button type="button" :disabled="busy" :aria-label="`移除附件：${file.name}`" @click="attachments.splice(index, 1)">移除</button></div></div>
          <p v-if="parsing" role="status" class="attachment-hint">正在上传并解析附件…</p>
          <p v-if="knowledgeBaseId" class="attachment-hint">知识库：{{ knowledgeBases.find(base => base.id === knowledgeBaseId)?.name || knowledgeBaseId }} <button type="button" :disabled="busy" @click="knowledgeBaseId = null">移除</button></p>
          <label for="chat-input" class="sr-only">输入问题</label>
          <textarea id="chat-input" ref="composer" v-model="draft" rows="3" maxlength="4000" placeholder="输入问题，Enter 发送，Shift + Enter 换行" :disabled="busy || loading" @keydown="onKeydown" />
          <div class="composer-footer">
            <details ref="addMenu" class="add-menu"><summary aria-label="添加附件或知识库" title="添加附件或知识库">+</summary><div class="add-menu-panel"><button type="button" :disabled="busy || loading || attachments.length >= 3" @click="fileInput?.click()">上传附件</button><label>选择知识库<select v-model="knowledgeBaseId" aria-label="选择知识库" :disabled="busy || loading"><option :value="null">不使用知识库</option><option v-for="base in knowledgeBases" :key="base.id" :value="base.id">{{ base.name }}</option></select></label><small>最多 3 个附件。支持 PDF、Word、Excel 和文本；DOCX/XLSX 最大 500 MiB，其他格式 50 MiB。每个附件保留前 12000 字符，模型按上下文容量引用。</small></div></details>
            <input ref="fileInput" class="sr-only" tabindex="-1" type="file" multiple accept=".txt,.md,.csv,.json,.yaml,.yml,.htm,.html,.doc,.docx,.xls,.xlsx,.pdf" aria-label="上传对话附件" @change="uploadAttachments" />
            <span class="draft-count">{{ draft.length }} / 4000</span>
          <label class="model-picker">对话模型
            <select v-model="selectedModelId" aria-label="对话模型" title="与模型管理同步，从下一条消息生效" :disabled="busy || loading || !providers.some(provider => provider.models?.length)" @change="switchModel">
              <option :value="null" disabled>请选择已保存的模型</option>
              <optgroup v-for="provider in providers" :key="provider.id" :label="provider.name">
                <option v-for="model in provider.models" :key="model.id" :value="model.id">{{ model.model_id }}</option>
              </optgroup>
            </select>
          </label>
            <el-button v-if="sending || messages.some(item => item.status === 'running')" type="danger" plain :icon="VideoPause" :loading="stopping" @click="stop">停止生成</el-button>
            <el-button v-else type="primary" native-type="submit" :icon="Promotion" :disabled="(!draft.trim() && !attachments.length) || busy || loading || !!readinessError">发送</el-button>
          </div>
        </form>
        <span class="sr-only" role="status" aria-live="polite">{{ busy ? '正在生成回答' : '可以发送消息' }}</span>
      </section>
    </div>
  </section>
</template>

<style scoped>
.pending-attachments{max-height:150px;overflow:auto;margin-bottom:8px}.pending-file{display:flex;gap:12px;align-items:flex-start}.pending-file details{flex:1;min-width:0}.chat-attachment{margin:8px 0;font-size:12px;overflow-wrap:anywhere}.chat-attachment pre{max-height:220px;overflow:auto;white-space:pre-wrap;font:inherit;line-height:1.7}.attachment-hint{font-size:12px;color:var(--ui-text-secondary);margin:6px 0}.pending-file button,.attachment-hint button{border:0;background:transparent;color:var(--ui-primary);cursor:pointer}.composer .composer-footer{gap:12px;justify-content:flex-start}.draft-count{margin-right:auto;white-space:nowrap}.composer .model-picker{max-width:50%}.add-menu{position:relative}.add-menu>summary{list-style:none;font-size:26px;line-height:32px;width:32px;text-align:center;border:1px solid var(--ui-border);border-radius:6px}.add-menu>summary::-webkit-details-marker{display:none}.add-menu-panel{position:absolute;bottom:42px;left:0;z-index:10;width:280px;padding:16px;background:var(--ui-surface);border:1px solid var(--ui-border);border-radius:8px;box-shadow:0 8px 24px #0002;display:grid;gap:14px}.add-menu-panel>button{padding:8px;text-align:left;background:var(--ui-surface-subtle);border:1px solid var(--ui-border);border-radius:6px;color:var(--ui-text-primary)}.add-menu-panel label{display:grid;gap:6px}.add-menu-panel select{max-width:100%;margin:0}.add-menu-panel small{line-height:1.6;color:var(--ui-text-secondary)}
@media(max-width:767px){.draft-count{display:none}.composer .model-picker{margin-left:auto;max-width:60%}.composer .composer-footer{gap:8px}.model-picker select{max-width:140px}}

.knowledge-picker{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 24px;border-bottom:1px solid var(--ui-border);font-size:13px}.knowledge-picker select{max-width:240px}.knowledge-picker small{color:var(--ui-text-secondary)}
.chat-page{padding:24px;max-width:1600px;margin:auto}.chat-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}.chat-workspace{display:grid;grid-template-columns:250px minmax(0,1fr);height:calc(100dvh - 178px);min-height:520px;border:1px solid var(--ui-border);border-radius:var(--ui-radius-panel);background:var(--ui-surface);overflow:hidden}.conversation-panel{display:flex;flex-direction:column;min-height:0;padding:16px 10px;background:var(--ui-surface-subtle);border-right:1px solid var(--ui-border)}.new-chat{width:100%}.conversation-caption{font-size:12px;color:var(--ui-text-secondary);margin:20px 10px 10px}.conversation-list{overflow:auto;min-height:0}.empty-history{font-size:13px;line-height:1.8;padding:0 10px}.conversation-row{display:flex;align-items:center;margin:3px 0;border-radius:8px}.conversation-row.active{background:var(--ui-primary-soft)}.conversation-link{flex:1;min-width:0;text-align:left;border:0;background:transparent;padding:12px 10px;color:var(--ui-text-primary);cursor:pointer}.conversation-link span{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-weight:600}.conversation-link small{display:block;color:var(--ui-text-secondary);margin-top:5px}.conversation-link:hover{color:var(--ui-primary)}.dialogue-panel{display:flex;min-width:0;min-height:0;flex-direction:column}.dialogue-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:18px 24px;border-bottom:1px solid var(--ui-border)}.dialogue-heading strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.model-picker{display:flex;align-items:center;gap:6px;max-width:60%;min-width:0;flex-shrink:0;font-size:12px;white-space:nowrap;color:var(--ui-text-secondary)}.model-picker select{min-width:0;max-width:280px;margin-left:0;text-overflow:ellipsis}.chat-alert{flex-shrink:0;border-radius:0}.message-feed{flex:1;min-height:0;overflow:auto;padding:24px;scrollbar-gutter:stable}.chat-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100%;text-align:center;padding:24px}.empty-icon{box-sizing:content-box;width:32px;height:32px;flex-shrink:0;font-size:32px;color:var(--ui-primary);padding:18px;border-radius:16px;background:var(--ui-primary-soft)}.chat-empty h2{font-size:22px;margin:24px 0 8px}.chat-empty p{max-width:430px;line-height:1.8;color:var(--ui-text-secondary);font-size:14px}.suggested-questions{display:flex;gap:12px;flex-wrap:wrap;justify-content:center;margin-top:14px}.suggested-questions button{border:1px solid var(--ui-border);border-radius:8px;padding:10px 16px;background:var(--ui-surface);color:var(--ui-text-primary);cursor:pointer}.suggested-questions button:hover{border-color:var(--ui-primary);color:var(--ui-primary)}.message{max-width:900px;margin:0 auto 28px;padding:0 12px}.message.user{padding:16px;border-radius:12px;background:var(--ui-surface-subtle)}.message-meta{display:flex;flex-wrap:wrap;overflow-wrap:anywhere;align-items:center;gap:12px;margin-bottom:10px;font-size:13px}.message-meta span{font-size:12px;color:var(--ui-text-secondary)}.message-content{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.85;font-size:14px}.message-error{color:var(--ui-danger);font-size:13px}.message-sources{margin-top:16px;padding-top:12px;border-top:1px solid var(--ui-border);font-size:12px;color:var(--ui-text-secondary)}summary{cursor:pointer;line-height:1.8;overflow-wrap:anywhere}.source-item{margin-top:10px;padding:10px 12px;background:var(--ui-surface-subtle);border-radius:6px}.source-item span{color:var(--ui-primary)}.source-item p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;color:var(--ui-text-primary)}.composer{flex-shrink:0;padding:16px 24px 18px;border-top:1px solid var(--ui-border)}.composer-options{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;font-size:12px;color:var(--ui-text-secondary)}select{font:inherit;color:var(--ui-text-primary);border:1px solid var(--ui-border);background:var(--ui-surface);padding:5px 8px;border-radius:6px;margin-left:6px}.composer textarea{display:block;box-sizing:border-box;width:100%;resize:vertical;max-height:180px;min-height:72px;padding:12px;border:1px solid var(--ui-border-strong);border-radius:8px;background:var(--ui-surface);color:var(--ui-text-primary);font:inherit;line-height:1.7}.composer textarea:focus{outline:2px solid var(--ui-primary);outline-offset:1px}.composer-footer{display:flex;align-items:center;justify-content:space-between;margin-top:10px;font-size:12px;color:var(--ui-text-secondary)}button:focus-visible,summary:focus-visible,select:focus-visible{outline:2px solid var(--ui-primary);outline-offset:2px}button:disabled{cursor:not-allowed;opacity:.55}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:1000px){.chat-workspace{grid-template-columns:200px minmax(0,1fr)}.context-hint{display:none}.message-feed{padding:18px}.composer{padding:14px 18px}}
@media(max-width:767px){.chat-page{padding:12px}.chat-heading{margin-bottom:12px}.chat-workspace{display:flex;flex-direction:column;height:calc(100dvh - 154px);min-height:600px}.conversation-panel{max-height:145px;flex-shrink:0;border-right:0;border-bottom:1px solid var(--ui-border);padding:10px}.conversation-caption{display:none}.conversation-list{display:flex;gap:8px;overflow:auto;margin-top:6px}.conversation-row{min-width:190px;max-width:240px;flex-shrink:0}.empty-history{margin:6px}.dialogue-panel{flex:1}.dialogue-heading{padding:12px;align-items:stretch;flex-direction:column}.model-picker{max-width:100%}.model-picker select{flex:1;max-width:100%}.message-feed{padding:14px 8px}.composer{padding:12px}.verify-hint{display:none}.chat-empty{padding:12px}.chat-empty h2{font-size:19px}.chat-empty p{font-size:13px}.suggested-questions{gap:6px}.suggested-questions button{padding:8px 10px}}
</style>
