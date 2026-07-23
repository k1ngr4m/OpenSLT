<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowDown, ArrowRight, Bottom, CopyDocument, Delete, Plus, Top } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import type { XmlNode } from '@/types/orderConfig'
import { cloneTree, createElement, elementChildren, getAttribute, isSensitiveNode, nodeLabel, setAttribute } from '@/utils/orderConfigXml'

const props = withDefaults(defineProps<{ node: XmlNode; depth?: number }>(), { depth: 0 })
const emit = defineEmits<{ changed: [] }>()
const collapsed = ref(props.depth > 1)
const showAttributes = ref(false)
const revealSensitive = ref(false)
const entries = computed(() => elementChildren(props.node))
const isGroup = computed(() => entries.value.length > 0 || props.depth === 0)

function indentText() {
  return { type: 'text', name: null, attributes: [], text: `\n${'  '.repeat(props.depth + 1)}`, children: [] } as XmlNode
}

function addChild() {
  const insertAt = props.node.children.length && props.node.children[props.node.children.length - 1]?.type === 'text'
    ? props.node.children.length - 1
    : props.node.children.length
  props.node.children.splice(insertAt, 0, indentText(), createElement())
  collapsed.value = false
  emit('changed')
}

function duplicateChild(index: number) {
  const source = props.node.children[index]
  props.node.children.splice(index + 1, 0, indentText(), cloneTree(source))
  emit('changed')
}

async function deleteChild(index: number) {
  const source = props.node.children[index]
  await ElMessageBox.confirm(`确定删除节点“${nodeLabel(source)}”？`, '删除 XML 节点', { type: 'warning' })
  props.node.children.splice(index, 1)
  if (index > 0 && props.node.children[index - 1]?.type === 'text' && !props.node.children[index - 1].text?.trim()) {
    props.node.children.splice(index - 1, 1)
  }
  emit('changed')
}

function moveChild(index: number, direction: -1 | 1) {
  const items = entries.value
  const position = items.findIndex(item => item.index === index)
  const target = items[position + direction]
  if (!target) return
  const currentNode = props.node.children[index]
  props.node.children[index] = props.node.children[target.index]
  props.node.children[target.index] = currentNode
  emit('changed')
}

function addAttribute() {
  let suffix = 1
  let name = 'attribute'
  while (props.node.attributes.some(item => item.name === name)) name = `attribute_${suffix++}`
  props.node.attributes.push({ name, value: '' })
  emit('changed')
}

function removeAttribute(index: number) {
  props.node.attributes.splice(index, 1)
  emit('changed')
}
</script>

<template>
  <section class="xml-node" :class="{ group: isGroup }">
    <header class="node-header">
      <button v-if="isGroup" class="collapse-button" type="button" :aria-label="collapsed ? '展开节点' : '收起节点'" @click="collapsed = !collapsed">
        <el-icon><ArrowRight v-if="collapsed" /><ArrowDown v-else /></el-icon>
      </button>
      <span v-else class="leaf-marker" />
      <div class="node-heading">
        <strong>{{ nodeLabel(node) }}</strong>
        <span class="node-tag mono">&lt;{{ node.name }}&gt;</span>
      </div>
      <button class="attribute-toggle" type="button" @click="showAttributes = !showAttributes">{{ showAttributes ? '收起属性' : '属性' }}</button>
      <el-button v-if="isGroup" :icon="Plus" text circle title="新增子节点" aria-label="新增子节点" @click="addChild" />
    </header>

    <div v-if="!isGroup" class="leaf-fields">
      <el-input
        :model-value="getAttribute(node, 'value')"
        :type="isSensitiveNode(node) && !revealSensitive ? 'password' : 'text'"
        class="value-input"
        placeholder="value"
        @update:model-value="value => { setAttribute(node, 'value', value); emit('changed') }"
      />
      <el-input
        :model-value="getAttribute(node, 'default_value')"
        class="default-input"
        placeholder="default_value"
        @update:model-value="value => { setAttribute(node, 'default_value', value); emit('changed') }"
      />
      <el-button v-if="isSensitiveNode(node)" text @click="revealSensitive = !revealSensitive">{{ revealSensitive ? '隐藏' : '显示' }}</el-button>
    </div>

    <div v-if="showAttributes" class="attribute-editor">
      <label class="attribute-row"><span>节点名</span><el-input v-model="node.name" @input="emit('changed')" /></label>
      <label v-for="(attribute, index) in node.attributes" :key="index" class="attribute-row">
        <el-input v-model="attribute.name" class="attribute-name mono" @input="emit('changed')" />
        <el-input v-model="attribute.value" @input="emit('changed')" />
        <el-button :icon="Delete" text circle type="danger" aria-label="删除属性" @click="removeAttribute(index)" />
      </label>
      <el-button :icon="Plus" text @click="addAttribute">新增属性</el-button>
    </div>

    <div v-if="isGroup && !collapsed" class="node-children">
      <template v-for="entry in entries" :key="entry.child.clientId">
        <div class="child-row">
          <div class="child-actions">
            <el-button :icon="Top" text circle :disabled="entry.index === entries[0]?.index" title="上移" aria-label="上移节点" @click="moveChild(entry.index, -1)" />
            <el-button :icon="Bottom" text circle :disabled="entry.index === entries[entries.length - 1]?.index" title="下移" aria-label="下移节点" @click="moveChild(entry.index, 1)" />
            <el-button :icon="CopyDocument" text circle title="复制" aria-label="复制节点" @click="duplicateChild(entry.index)" />
            <el-button :icon="Delete" text circle type="danger" title="删除" aria-label="删除节点" @click="deleteChild(entry.index)" />
          </div>
          <OrderConfigNodeEditor :node="entry.child" :depth="depth + 1" @changed="emit('changed')" />
        </div>
      </template>
      <div v-for="comment in node.children.filter(item => item.type === 'comment')" :key="comment.clientId" class="xml-comment mono">&lt;!--{{ comment.text }}--&gt;</div>
    </div>
  </section>
</template>

<style scoped>
.xml-node{min-width:0}.xml-node.group{border-left:2px solid #d9e2ea;padding-left:10px}.node-header{display:flex;align-items:center;gap:8px;min-height:38px}.collapse-button,.attribute-toggle{border:0;background:transparent;color:#617080;cursor:pointer}.collapse-button{width:24px;height:24px;padding:0}.leaf-marker{width:8px;height:8px;margin:0 8px;border-radius:2px;background:#6fae9d}.node-heading{display:flex;align-items:baseline;gap:8px;min-width:160px;flex:1}.node-heading strong{font-size:13px;font-weight:600;color:#253341}.node-tag{color:#91a0ae;font-size:11px}.attribute-toggle{font-size:12px}.leaf-fields{display:grid;grid-template-columns:minmax(220px,1fr) minmax(160px,.7fr) auto;gap:8px;margin:2px 0 8px 32px}.attribute-editor{display:grid;gap:7px;margin:4px 0 10px 32px;padding:10px;background:#f5f8fa;border:1px solid #e3e9ef;border-radius:6px}.attribute-row{display:grid;grid-template-columns:150px minmax(180px,1fr) auto;gap:8px;align-items:center}.attribute-row:first-child{grid-template-columns:150px minmax(180px,1fr)}.attribute-row>span{font-size:12px;color:#687786}.attribute-name{font-size:12px}.node-children{display:grid;gap:4px;margin:2px 0 6px 8px}.child-row{display:grid;grid-template-columns:30px minmax(0,1fr);gap:3px}.child-actions{display:flex;flex-direction:column;padding-top:4px;opacity:.18;transition:opacity .2s}.child-row:hover>.child-actions,.child-actions:focus-within{opacity:1}.child-actions :deep(.el-button){margin:0;width:26px;height:24px}.xml-comment{margin:4px 0 6px 38px;color:#81909c;font-size:11px;white-space:pre-wrap}.mono{font-family:Cascadia Code,Consolas,monospace}
</style>
