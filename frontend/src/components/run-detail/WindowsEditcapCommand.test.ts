import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WindowsEditcapCommand from '@/components/run-detail/WindowsEditcapCommand.vue'

const { copyTextMock, message } = vi.hoisted(() => ({
  copyTextMock: vi.fn(),
  message: { error: vi.fn(), success: vi.fn() },
}))
vi.mock('@/utils/clipboard', () => ({ copyText: copyTextMock }))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: message }))

describe('WindowsEditcapCommand', () => {
  beforeEach(() => {
    copyTextMock.mockReset()
    message.error.mockReset()
    message.success.mockReset()
  })

  it('shows the command and copies it for local Windows execution', async () => {
    copyTextMock.mockResolvedValue(undefined)
    const command = '"D:\\Program Files\\Wireshark\\editcap.exe" -F pcapng "in" "out"'
    const wrapper = mount(WindowsEditcapCommand, {
      props: { command },
      global: {
        components: {
          ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        },
      },
    })

    expect(wrapper.text()).toContain('Windows editcap 命令')
    expect(wrapper.find('code').text()).toBe(command)
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(copyTextMock).toHaveBeenCalledWith(command)
    expect(message.success).toHaveBeenCalledWith('Windows editcap 命令已复制')
    expect(message.error).not.toHaveBeenCalled()
  })

  it('asks the operator to select the command when every copy path fails', async () => {
    copyTextMock.mockRejectedValue(new Error('Clipboard copy failed'))
    const wrapper = mount(WindowsEditcapCommand, {
      props: { command: 'editcap command' },
      global: {
        components: {
          ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        },
      },
    })

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(message.success).not.toHaveBeenCalled()
    expect(message.error).toHaveBeenCalledWith('复制失败，请手动选择命令')
  })

  it('is hidden until the merge command has been prepared', () => {
    const wrapper = mount(WindowsEditcapCommand, {
      props: { command: '' },
      global: { stubs: { ElButton: true } },
    })

    expect(wrapper.html()).toBe('<!--v-if-->')
  })
})
