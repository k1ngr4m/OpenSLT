<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
const props = defineProps<{ content: string }>()
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true })
const rendered = computed(() => markdown.render(props.content))
</script>

<template><div class="markdown-content" v-html="rendered" /></template>

<style scoped>
.markdown-content{overflow-wrap:anywhere;line-height:1.85;font-size:14px;min-width:0}
.markdown-content :deep(> :first-child){margin-top:0}.markdown-content :deep(> :last-child){margin-bottom:0}
.markdown-content :deep(pre){overflow:auto;padding:14px;background:var(--ui-surface-subtle);border:1px solid var(--ui-border);border-radius:6px;white-space:pre}
.markdown-content :deep(code){font-family:monospace;background:var(--ui-surface-subtle);padding:2px 4px;border-radius:3px}
.markdown-content :deep(pre code){padding:0}.markdown-content :deep(blockquote){margin-left:0;padding-left:16px;border-left:3px solid var(--ui-border-strong);color:var(--ui-text-secondary)}
.markdown-content :deep(table){display:block;overflow:auto;border-collapse:collapse;max-width:100%}.markdown-content :deep(th),.markdown-content :deep(td){padding:8px 12px;border:1px solid var(--ui-border);text-align:left}
.markdown-content :deep(img){max-width:100%}.markdown-content :deep(a){color:var(--ui-primary)}.markdown-content :deep(ul),.markdown-content :deep(ol){padding-left:24px}
</style>
