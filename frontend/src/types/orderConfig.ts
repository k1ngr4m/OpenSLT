export type XmlNodeType = 'element' | 'text' | 'comment' | 'cdata' | 'processing_instruction'

export interface XmlAttribute {
  name: string
  value: string
}

export interface XmlNode {
  type: XmlNodeType
  name: string | null
  attributes: XmlAttribute[]
  text: string | null
  children: XmlNode[]
  clientId?: string
}

export interface OrderConfigFile {
  name: string
  size: number
  modified_at: string
}

export interface OrderConfigDetail extends OrderConfigFile {
  checksum: string
  content: string
  declaration: string
  document: XmlNode
  tool: string
  simulated: boolean
}
