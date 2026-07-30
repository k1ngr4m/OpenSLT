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
    expect(dialog.text().indexOf('v0.2.0')).toBeLessThan(dialog.text().indexOf('v0.1.0'))

    const currentReleaseChanges = dialog.findAll('.release-entry')[0].findAll('li')
    expect(currentReleaseChanges.map(change => change.get('.change-type').text())).toEqual([
      '新增',
      '新增',
      '新增',
      '新增',
      '新增',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '修复',
      '修复',
      '修复',
      '安全',
    ])
    expect(currentReleaseChanges.map(change => change.findAll('span')[1].text())).toEqual([
      '运行详情的接线拓扑支持在确认前修改并保存网卡名称。',
      '工作流支持同版本暂停编辑、重新启用、新增版本和历史版本归档。',
      '日志中心新增全量 HTTP、WebSocket 和平台及资源数据库 SQL 的结构化检索与管理员详情。',
      '方案与场景支持按一级目录归类，并可在目录之间移动完整方案。',
      '主界面右上角展示当前版本，并支持查看历次版本更新说明。',
      '运行详情的产物与报告按生成时间倒序展示。',
      '运行详情将统计指标名称与数据来源分开展示。',
      '运行详情不再展示所有节点的原始配置和原始结果。',
      '启动模拟市场节点改为通过运行详情中的 SSH 终端下发脚本并人工确认完成。',
      '工作流编辑改为全屏双页签，并为节点和资源修改增加独立保存及未保存保护。',
      '主界面按首页与管理中心重组导航，并预留暂未开放的图表入口。',
      '主界面右上角移除时间显示，将版本入口调整到管理中心右侧，并仅高亮当前选中的管理中心入口。',
      '工作流节点之间的连接线改为带方向的向下箭头。',
      'Python 包、API、前端和离线部署包统一使用同一版本号。',
      '数据库配置采集详情展示采集时的配置项描述。',
      '统计输入仅显示当前节点前最近一次成功解析生成的 CSV。',
      '修复数据库配置预采集期间并发读取配置项可能因平台数据库锁冲突而失败的问题。',
      '接口报文、请求头和 SQL 参数统一执行敏感字段脱敏、正文限长和二进制摘要记录。',
    ])
  })
})
