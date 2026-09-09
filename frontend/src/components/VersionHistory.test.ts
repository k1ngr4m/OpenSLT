import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import VersionHistory from './VersionHistory.vue'
import { appVersion, releaseHistory } from '@/releaseMetadata'


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

    expect(wrapper.get('.version-trigger').text()).toBe(`v${appVersion}`)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

    await wrapper.get('.version-trigger').trigger('click')
    await nextTick()

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.text()).toContain('版本更新说明')
    expect(dialog.attributes('data-width')).toBe('640px')
    expect(dialog.text()).toContain('当前版本')
    expect(dialog.text()).toContain(releaseHistory[0].date!)
    const releaseEntries = dialog.findAll('.release-entry')
    expect(releaseEntries).toHaveLength(releaseHistory.length)
    const labels = { added: '新增', changed: '变更', fixed: '修复', removed: '移除', security: '安全' }
    releaseHistory.forEach((release, index) => {
      const entry = releaseEntries[index]!
      expect(entry.get('summary').text()).toContain(`v${release.version}`)
      expect(entry.attributes('open')).toBe(index === 0 ? '' : undefined)
      const changes = entry.findAll('li')
      const typeOrder = Object.keys(labels)
      const orderedChanges = [...release.changes].sort((a, b) => typeOrder.indexOf(a.type) - typeOrder.indexOf(b.type))
      expect(changes.map(change => change.get('.change-type').text())).toEqual(orderedChanges.map(change => labels[change.type]))
      expect(changes.map(change => change.findAll('span')[1]!.text())).toEqual(orderedChanges.map(change => change.text))
    })
  })
})
