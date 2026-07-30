import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import VersionHistory from './VersionHistory.vue'


const ElDialogStub = {
  props: ['modelValue', 'title'],
  template: '<section v-if="modelValue" role="dialog"><h2>{{ title }}</h2><slot /></section>',
}

const ElTagStub = {
  template: '<span><slot /></span>',
}


describe('VersionHistory', () => {
  it('opens the release history with the current version first', async () => {
    const wrapper = mount(VersionHistory, {
      global: {
        stubs: { ElDialog: ElDialogStub, ElTag: ElTagStub },
      },
    })

    expect(wrapper.get('.version-trigger').text()).toBe('v0.2.0')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

    await wrapper.get('.version-trigger').trigger('click')
    await nextTick()

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.text()).toContain('版本更新说明')
    expect(dialog.text()).toContain('当前版本')
    expect(dialog.text()).toContain('2026-07-29')
    expect(dialog.text().indexOf('v0.2.0')).toBeLessThan(dialog.text().indexOf('v0.2.0'))

    const currentReleaseChanges = dialog.findAll('.release-entry')[0].findAll('li')
    expect(currentReleaseChanges.map(change => change.get('.change-type').text())).toEqual([
      '新增',
      '新增',
      '变更',
      '变更',
      '安全',
    ])
    expect(currentReleaseChanges.map(change => change.findAll('span')[1].text())).toEqual([
      '工作流支持同版本暂停编辑、重新启用、新增版本和历史版本归档。',
      '日志中心新增全量 HTTP、WebSocket 和平台及资源数据库 SQL 的结构化检索与管理员详情。',
      '工作流编辑改为全屏双页签，并为节点和资源修改增加独立保存及未保存保护。',
      '主界面按首页与管理中心重组导航，并预留暂未开放的图表入口。',
      '接口报文、请求头和 SQL 参数统一执行敏感字段脱敏、正文限长和二进制摘要记录。',
    ])
  })
})
