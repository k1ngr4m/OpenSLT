import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import RunLogPanel from './RunLogPanel.vue'
import type { RunLog } from '@/types/run'

const log = (id: number) => ({ id, level: 'INFO', source: 'test', event: 'output', message: `log-${id}`, created_at: '2026-09-08T10:00:00Z' }) as RunLog

it('bounds a large log list and preserves the historical page while new logs arrive', async () => {
  const logs = Array.from({ length: 10000 }, (_, i) => log(i + 1))
  const wrapper = mount(RunLogPanel, { props: { logs, total: logs.length, scopeLabel: '全部日志', scoped: false }, global: { stubs: { ElButton: { template: '<button><slot /></button>' }, ElTag: true } } })
  expect(wrapper.findAll('.log-line')).toHaveLength(200)
  expect(wrapper.find('.log-line').text()).toContain('log-9801')
  const button = (label: string) => wrapper.findAll('button').find(item => item.text().includes(label))!
  await button('上一页').trigger('click')
  expect(wrapper.find('.log-line').text()).toContain('log-9601')
  await wrapper.setProps({ logs: [...logs, log(10001)], total: 10001 })
  expect(wrapper.find('.log-line').text()).toContain('log-9601')
  expect(wrapper.text()).toContain('1 条新日志')
  await button('查看最新').trigger('click')
  expect(wrapper.findAll('.log-line')).toHaveLength(1)
  expect(wrapper.find('.log-line').text()).toContain('log-10001')
  wrapper.unmount()
})
