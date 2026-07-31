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

    expect(wrapper.get('.version-trigger').text()).toBe('v0.2.1')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

    await wrapper.get('.version-trigger').trigger('click')
    await nextTick()

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.text()).toContain('版本更新说明')
    expect(dialog.text()).toContain('当前版本')
    expect(dialog.text()).toContain('2026-07-31')
    expect(dialog.text().indexOf('v0.2.1')).toBeLessThan(dialog.text().indexOf('v0.2.0'))

    const currentReleaseChanges = dialog.findAll('.release-entry')[0].findAll('li')
    expect(currentReleaseChanges.map(change => change.get('.change-type').text())).toEqual([
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '修复',
    ])
    expect(currentReleaseChanges.map(change => change.findAll('span')[1].text())).toEqual([
      '离线制包支持复用 RPM、Node、npm 和 pip 缓存以缩短重复打包时间。',
      '优化日志、审计、任务队列和数据库控制台相关数据库操作的查询性能。',
      '平台名称改为 OpenSLT 自动化测试平台，并调整首页侧边栏分组。',
      '优化工作流版本下拉入口的图标样式。',
      '优化运行详情节点详情页的信息层级与样式。',
      '优化创建测速运行抽屉的信息层级与样式。',
      '优化方案与场景弹窗的信息层级与样式。',
      '优化方案目录中方案卡片折叠入口和标题行对齐效果。',
      '修复慢速环境下观测日志索引逐条提交导致后台 flush 超时的问题。',
    ])
  })
})
