import { describe, expect, it } from 'vitest'
import { useStagedWorkflowNode } from './useStagedWorkflowNode'

type Node = { node_key: string; name: string; config: { enabled: boolean } }

describe('useStagedWorkflowNode', () => {
  it('keeps edits isolated until a snapshot is committed', () => {
    const source: Node = { node_key: 'one', name: '原节点', config: { enabled: false } }
    const staged = useStagedWorkflowNode<Node>()
    staged.stage(source)

    staged.form.value!.name = '修改后的节点'
    staged.form.value!.config.enabled = true

    expect(staged.dirty.value).toBe(true)
    expect(source).toEqual({ node_key: 'one', name: '原节点', config: { enabled: false } })
    expect(staged.snapshot()).toEqual({ node_key: 'one', name: '修改后的节点', config: { enabled: true } })
  })

  it('restores the saved baseline when changes are cancelled', () => {
    const staged = useStagedWorkflowNode<Node>()
    staged.stage({ node_key: 'one', name: '已保存', config: { enabled: false } })
    staged.form.value!.name = '未保存'

    staged.reset()

    expect(staged.dirty.value).toBe(false)
    expect(staged.form.value?.name).toBe('已保存')
  })

  it('clears state when no node is selected', () => {
    const staged = useStagedWorkflowNode<Node>()
    staged.stage({ node_key: 'one', name: '节点', config: { enabled: false } })
    staged.stage(null)

    expect(staged.form.value).toBeNull()
    expect(staged.dirty.value).toBe(false)
  })
})
