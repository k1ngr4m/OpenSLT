import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import VersionHistory from './VersionHistory.vue'


const ElDialogStub = {
  props: ['modelValue', 'title', 'width'],
  template: '<section v-if="modelValue" role="dialog" :data-width="width"><h2>{{ title }}</h2><slot /></section>',
}

const ElTagStub = {
  template: '<span><slot /></span>',
}

const ElIconStub = {
  template: '<span><slot /></span>',
}


describe('VersionHistory', () => {
  it('opens the release history with the current version first', async () => {
    const wrapper = mount(VersionHistory, {
      global: {
        stubs: { ElDialog: ElDialogStub, ElTag: ElTagStub, ElIcon: ElIconStub },
      },
    })

    expect(wrapper.get('.version-trigger').text()).toBe('v0.2.2')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

    await wrapper.get('.version-trigger').trigger('click')
    await nextTick()

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.text()).toContain('版本更新说明')
    expect(dialog.attributes('data-width')).toBe('640px')
    expect(dialog.text()).toContain('当前版本')
    expect(dialog.text()).toContain('2026-08-06')
    expect(dialog.text().indexOf('v0.2.2')).toBeLessThan(dialog.text().indexOf('v0.2.1'))
    expect(dialog.text().indexOf('v0.2.1')).toBeLessThan(dialog.text().indexOf('v0.2.0'))

    const releaseEntries = dialog.findAll('.release-entry')
    expect(releaseEntries).toHaveLength(4)
    expect(releaseEntries[0].attributes('open')).toBe('')
    expect(releaseEntries[1].attributes('open')).toBeUndefined()
    expect(releaseEntries[2].attributes('open')).toBeUndefined()
    expect(releaseEntries[3].attributes('open')).toBeUndefined()

    const currentReleaseChanges = releaseEntries[0].findAll('li')
    expect(currentReleaseChanges.map(change => change.get('.change-type').text())).toEqual([
      '新增',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '变更',
      '修复',
    ])
    expect(currentReleaseChanges.map(change => change.findAll('span')[1].text())).toEqual([
      '运行流转到发单节点后可切换或编辑 XML 配置并修改网卡接口。',
      '运行详情不再展示运行配置快照摘要。',
      '数据解析节点改为通过运行详情中的 SSH 终端启动解析工具，并支持配置和下发解析指令。',
      '版本更新说明改为固定尺寸，并默认收起历史版本。',
      '日志中心不再采集和展示平台数据库及资源数据库 SQL 日志。',
      '资源新增与编辑抽屉改为紧凑双列表单布局。',
      '合并 pcapng 节点改为在 SLNIC 生成 pcap 后，由操作员在本机 Windows 使用 editcap 转换并归档产物。',
      '接线确认按 REM 实际采集的 180 段和 51 段网卡名称及 IP 展示，并支持确认前补录。',
    ])
  })
})
