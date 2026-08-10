import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from '@/utils/clipboard'

const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const execCommandDescriptor = Object.getOwnPropertyDescriptor(document, 'execCommand')

function setClipboard(value: Pick<Clipboard, 'writeText'> | undefined) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value })
}

function setExecCommand(value: (commandId: string) => boolean) {
  Object.defineProperty(document, 'execCommand', { configurable: true, value })
}

afterEach(() => {
  if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor)
  else Reflect.deleteProperty(navigator, 'clipboard')
  if (execCommandDescriptor) Object.defineProperty(document, 'execCommand', execCommandDescriptor)
  else Reflect.deleteProperty(document, 'execCommand')
  document.body.replaceChildren()
})

describe('copyText', () => {
  it('uses the async Clipboard API without creating a fallback element', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const execCommand = vi.fn(() => true)
    setClipboard({ writeText })
    setExecCommand(execCommand)

    await copyText('RUN-20260810-001')

    expect(writeText).toHaveBeenCalledWith('RUN-20260810-001')
    expect(execCommand).not.toHaveBeenCalled()
    expect(document.querySelector('[data-clipboard-fallback]')).toBeNull()
  })

  it('falls back on HTTP and preserves text, focus, selection, and DOM state', async () => {
    const value = '"D:\\Program Files\\Wireshark\\editcap.exe" "\\\\server\\抓包\\in.pcap" <>&'
    const host = document.createElement('div')
    host.tabIndex = 0
    host.textContent = '原选区'
    document.body.appendChild(host)
    host.focus()
    const originalRange = document.createRange()
    originalRange.selectNodeContents(host)
    const selection = document.getSelection()!
    selection.removeAllRanges()
    selection.addRange(originalRange)
    let copiedValue = ''
    setClipboard(undefined)
    setExecCommand(commandId => {
      expect(commandId).toBe('copy')
      copiedValue = (document.activeElement as HTMLTextAreaElement).value
      return true
    })

    await copyText(value)

    expect(copiedValue).toBe(value)
    expect(document.querySelector('[data-clipboard-fallback]')).toBeNull()
    expect(document.activeElement).toBe(host)
    expect(document.getSelection()?.toString()).toBe('原选区')
  })

  it('falls back when the async Clipboard API rejects', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new DOMException('Denied', 'NotAllowedError')) })
    const execCommand = vi.fn(() => true)
    setExecCommand(execCommand)

    await copyText('trace-123')

    expect(execCommand).toHaveBeenCalledWith('copy')
  })

  it.each([
    ['returns false', () => false],
    ['throws', () => { throw new Error('blocked') }],
  ])('rejects and cleans up when the fallback %s', async (_, implementation) => {
    setClipboard(undefined)
    setExecCommand(implementation)

    await expect(copyText('cannot-copy')).rejects.toThrow('Clipboard copy failed')

    expect(document.querySelector('[data-clipboard-fallback]')).toBeNull()
  })
})
