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
    expect(dialog.text()).toContain('日期未记录')
    expect(dialog.text().indexOf('v0.2.0')).toBeLessThan(dialog.text().indexOf('v0.1.0'))
  })
})
