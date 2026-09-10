import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import MarkdownContent from './MarkdownContent.vue'

it('renders Markdown while keeping HTML and dangerous links inert, including streaming updates', async () => {
  const wrapper = mount(MarkdownContent, { props: { content: '# 标题\n\n**重点**\n\n- 项目\n\n| 列 | 值 |\n|---|---|\n| A | B |\n\n```js\nconst x = 1\n```\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))' } })
  expect(wrapper.get('h1').text()).toBe('标题')
  expect(wrapper.get('strong').text()).toBe('重点')
  expect(wrapper.get('li').text()).toBe('项目')
  expect(wrapper.get('td').text()).toBe('A')
  expect(wrapper.get('pre code').text()).toContain('const x = 1')
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.find('a[href^="javascript:"]').exists()).toBe(false)
  await wrapper.setProps({ content: '**正在生成' })
  await wrapper.setProps({ content: '**正在生成完成**' })
  expect(wrapper.get('strong').text()).toBe('正在生成完成')
  wrapper.unmount()
})
