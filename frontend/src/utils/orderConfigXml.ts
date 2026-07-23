import type { XmlAttribute, XmlNode, XmlNodeType } from '@/types/orderConfig'

let sequence = 0

function nextId() {
  sequence += 1
  return `xml-node-${sequence}`
}

export function cloneTree(node: XmlNode): XmlNode {
  return {
    type: node.type,
    name: node.name,
    attributes: node.attributes.map(item => ({ ...item })),
    text: node.text,
    children: node.children.map(cloneTree),
    clientId: nextId(),
  }
}

export function prepareTree(node: XmlNode): XmlNode {
  const prepared = cloneTree(node)
  return prepared
}

export function getAttribute(node: XmlNode, name: string) {
  return node.attributes.find(item => item.name === name)?.value || ''
}

export function setAttribute(node: XmlNode, name: string, value: string) {
  const existing = node.attributes.find(item => item.name === name)
  if (existing) existing.value = value
  else node.attributes.push({ name, value })
}

export function nodeLabel(node: XmlNode) {
  return getAttribute(node, 'disp') || node.name || '未命名节点'
}

export function isSensitiveNode(node: XmlNode) {
  const source = `${node.name || ''} ${getAttribute(node, 'disp')}`
  return /(password|passwd|secret|token|private.?key|密码|密钥)/i.test(source)
}

export function elementChildren(node: XmlNode) {
  return node.children
    .map((child, index) => ({ child, index }))
    .filter(item => item.child.type === 'element')
}

export function createElement(name = 'new_field'): XmlNode {
  return {
    type: 'element',
    name,
    attributes: [
      { name: 'disp', value: name.toUpperCase() },
      { name: 'default_value', value: '' },
      { name: 'value', value: '' },
    ],
    text: null,
    children: [],
    clientId: nextId(),
  }
}

function escapeText(value: string) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function escapeAttribute(value: string) {
  return escapeText(value).replace(/"/g, '&quot;').replace(/'/g, '&apos;')
}

function serializeNode(node: XmlNode): string {
  if (node.type === 'text') return escapeText(node.text || '')
  if (node.type === 'comment') return `<!--${node.text || ''}-->`
  if (node.type === 'cdata') return `<![CDATA[${node.text || ''}]]>`
  if (node.type === 'processing_instruction') return `<?${node.name || ''} ${node.text || ''}?>`
  const name = node.name || 'unnamed'
  const attributes = node.attributes.map(item => ` ${item.name}="${escapeAttribute(item.value)}"`).join('')
  if (!node.children.length) return `<${name}${attributes} />`
  return `<${name}${attributes}>${node.children.map(serializeNode).join('')}</${name}>`
}

export function serializeDocument(declaration: string, root: XmlNode) {
  return `${declaration || '<?xml version="1.0" encoding="utf-8"?>'}\n${serializeNode(root)}`
}

function domNodeToTree(node: Node): XmlNode {
  const typeByNode: Record<number, XmlNodeType> = {
    [Node.ELEMENT_NODE]: 'element',
    [Node.TEXT_NODE]: 'text',
    [Node.COMMENT_NODE]: 'comment',
    [Node.CDATA_SECTION_NODE]: 'cdata',
    [Node.PROCESSING_INSTRUCTION_NODE]: 'processing_instruction',
  }
  const type = typeByNode[node.nodeType] || 'text'
  const attributes: XmlAttribute[] = node instanceof Element
    ? Array.from(node.attributes).map(item => ({ name: item.name, value: item.value }))
    : []
  return {
    type,
    name: type === 'element' || type === 'processing_instruction' ? node.nodeName : null,
    attributes,
    text: type === 'element' ? null : node.nodeValue || '',
    children: Array.from(node.childNodes).map(domNodeToTree),
    clientId: nextId(),
  }
}

export function parseDocument(content: string): { declaration: string; document: XmlNode } {
  if (/<!\s*(DOCTYPE|ENTITY)\b/i.test(content)) throw new Error('XML 不允许包含 DOCTYPE 或实体声明')
  const parsed = new DOMParser().parseFromString(content, 'application/xml')
  const parserError = parsed.querySelector('parsererror')
  if (parserError) throw new Error(parserError.textContent?.trim() || 'XML 格式错误')
  if (!parsed.documentElement) throw new Error('XML 缺少根节点')
  const declaration = content.match(/^\uFEFF?\s*(<\?xml[^?]*\?>)/i)?.[1]
    || '<?xml version="1.0" encoding="utf-8"?>'
  return { declaration, document: domNodeToTree(parsed.documentElement) }
}

export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KiB`
}
