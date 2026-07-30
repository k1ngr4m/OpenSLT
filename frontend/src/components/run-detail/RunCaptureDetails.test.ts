import { mount } from '@vue/test-utils'
import { defineComponent, h, inject, provide, toRef, unref, type PropType, type Ref } from 'vue'
import { describe, expect, it } from 'vitest'
import RunCaptureDetails from './RunCaptureDetails.vue'
import type { CaptureItem, CaptureSnapshot } from '@/types/run'


const ElTableStub = defineComponent({
  props: { data: { type: Array as PropType<CaptureItem[]>, default: () => [] } },
  setup(props, { slots }) {
    provide('tableRows', toRef(props, 'data'))
    return () => h('div', { class: 'el-table-stub' }, slots.default?.())
  },
})

const ElTableColumnStub = defineComponent({
  setup(_, { slots }) {
    const rows = inject<Ref<CaptureItem[]>>('tableRows')
    return () => h(
      'div',
      { class: 'el-table-column-stub' },
      (unref(rows) || []).map(row => h('div', { class: 'table-cell' }, slots.default?.({ row }))),
    )
  },
})

const ElTagStub = defineComponent({
  setup(_, { slots }) {
    return () => h('span', { class: 'el-tag-stub' }, slots.default?.())
  },
})

function snapshot(sourceType: 'database' | 'server', items: CaptureItem[]): CaptureSnapshot {
  return {
    id: sourceType === 'database' ? 1 : 2,
    scope: 'run',
    source_type: sourceType,
    resource_id: 3,
    database_name: sourceType === 'database' ? 'alpha_config' : null,
    status: 'succeeded',
    attempt: 1,
    error_message: null,
    started_at: '2026-07-30T15:34:04+08:00',
    finished_at: '2026-07-30T15:34:05+08:00',
    items,
  }
}

function item(overrides: Partial<CaptureItem> = {}): CaptureItem {
  return {
    id: 1,
    item_key: 'ACCOUNT_BP_CHANGE_INTERVAL_MS',
    item_label: 'ACCOUNT_BP_CHANGE_INTERVAL_MS',
    item_description: '账户 BP 变更间隔',
    value_text: '500',
    source_reference: 'alpha_config.t_global_settings.setting_key/setting_value',
    raw_output: '500',
    exit_code: 0,
    status: 'succeeded',
    error_message: null,
    ...overrides,
  }
}

function render(snapshots: CaptureSnapshot[]) {
  return mount(RunCaptureDetails, {
    props: {
      signature: 'loaded',
      snapshots,
      resourceName: () => '数据库资源',
      resourceMeta: () => '资源 ID 3',
    },
    global: {
      stubs: {
        ElAlert: true,
        ElSkeleton: true,
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElTag: ElTagStub,
      },
    },
  })
}

describe('RunCaptureDetails', () => {
  it('shows database keys with their captured descriptions and a missing-description fallback', () => {
    const wrapper = render([snapshot('database', [
      item(),
      item({
        id: 2,
        item_key: 'ALLOW_CASH_OUT_NEGATIVE',
        item_label: 'ALLOW_CASH_OUT_NEGATIVE',
        item_description: null,
      }),
    ])])

    expect(wrapper.findAll('strong.mono').map(node => node.text())).toEqual([
      'ACCOUNT_BP_CHANGE_INTERVAL_MS',
      'ALLOW_CASH_OUT_NEGATIVE',
    ])
    expect(wrapper.findAll('.capture-description').map(node => node.text())).toEqual([
      '账户 BP 变更间隔',
      '暂无描述',
    ])
  })

  it('keeps server labels and technical keys unchanged', () => {
    const wrapper = render([snapshot('server', [item({
      item_key: 'cpu_model',
      item_label: 'CPU 型号',
      item_description: null,
      value_text: '兆芯 KX-7000',
    })])])

    expect(wrapper.get('.table-cell strong').text()).toBe('CPU 型号')
    expect(wrapper.get('.capture-key').text()).toBe('cpu_model')
    expect(wrapper.find('.capture-description').exists()).toBe(false)
  })
})
