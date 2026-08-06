import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WindowsEditcapCommand from '@/components/run-detail/WindowsEditcapCommand.vue'

const message = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: message }))

describe('WindowsEditcapCommand', () => {
  beforeEach(() => {
    message.error.mockReset()
    message.success.mockReset()
  })

  it('shows the command and copies it for local Windows execution', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
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

    expect(writeText).toHaveBeenCalledWith(command)
    expect(message.success).toHaveBeenCalledWith('Windows editcap 命令已复制')
  })

  it('is hidden until the merge command has been prepared', () => {
    const wrapper = mount(WindowsEditcapCommand, {
      props: { command: '' },
      global: { stubs: { ElButton: true } },
    })

    expect(wrapper.html()).toBe('<!--v-if-->')
  })
})
