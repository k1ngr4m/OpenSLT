<script setup lang="ts">
import { computed, useId } from 'vue'
import { Connection } from '@element-plus/icons-vue'
import type { WiringSnapshot } from '@/utils/wiring'

const props = withDefaults(defineProps<{
  snapshot?: WiringSnapshot | null
  compact?: boolean
  emptyMessage?: string
  editable?: boolean
}>(), {
  snapshot: null,
  compact: false,
  emptyMessage: '请先绑定 REM、模拟市场与 SLNIC，并补全资源 IP 配置',
  editable: false,
})

const emit = defineEmits<{
  'interface-name-change': [
    slot: 'client' | 'market' | 'auxiliary',
    value: string,
    index?: number,
  ]
}>()

const instanceId = useId().replace(/[^a-zA-Z0-9_-]/g, '')
const uplinkArrow = `wiring-uplink-${instanceId}`
const downlinkArrow = `wiring-downlink-${instanceId}`
const isHardCore = computed(() => props.snapshot?.topology_kind !== 'soft_core')
const remBoxHeight = computed(() => isHardCore.value ? 324 : 226)
const hardCoreInterfaces = computed(() => {
  const value = props.snapshot
  if (!value) return []
  return [
    { slot: 'client' as const, name: value.client_interface.name, ip: value.client_interface.ip_address },
    { slot: 'market' as const, name: value.market_interface.name, ip: value.market_interface.ip_address },
    { slot: 'auxiliary' as const, name: value.auxiliary_interfaces[0] || '', ip: '', auxiliaryIndex: 0 },
    { slot: 'auxiliary' as const, name: value.auxiliary_interfaces[1] || '', ip: '', auxiliaryIndex: 1 },
  ]
})
const marketUplinkMainPath = computed(() => isHardCore.value
  ? 'M350 272 H490 V350 H642'
  : 'M350 296 H490 V350 H642')
const marketDownlinkMainPath = computed(() => isHardCore.value
  ? 'M642 380 H548 V299 H350'
  : 'M642 380 H548 V323 H350')
const marketDownlinkSlnicPath = computed(() => isHardCore.value
  ? 'M548 299 V548 H350'
  : 'M548 323 V548 H350')
const marketDownlinkJunctionY = computed(() => isHardCore.value ? 299 : 323)
const remTitle = computed(() => {
  const value = props.snapshot
  if (!value) return ''
  return `${value.rem.name || 'REM'}${value.rem.host ? ` (${value.rem.host})` : ''}`
})
const slnicTitle = computed(() => {
  const value = props.snapshot
  if (!value) return ''
  return `${value.slnic.name || 'SLNIC'}${value.slnic.host ? ` (${value.slnic.host})` : ''}`
})

function updateInterfaceName(
  slot: 'client' | 'market' | 'auxiliary',
  event: Event,
  index?: number,
) {
  emit('interface-name-change', slot, (event.target as HTMLInputElement).value, index)
}
</script>

<template>
  <div class="wiring-topology" :class="{ compact }">
    <div v-if="!snapshot" class="wiring-empty">
      <el-icon><Connection /></el-icon>
      <strong>接线图尚未就绪</strong>
      <span>{{ emptyMessage }}</span>
    </div>
    <div v-else class="wiring-scroll" tabindex="0" aria-label="接线拓扑，可横向滚动查看">
      <svg viewBox="0 0 900 620" role="img" :aria-label="`${snapshot.model_label}接线确认图`">
        <defs>
          <marker :id="uplinkArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L8,4 L0,8 Z" fill="#c43d47" />
          </marker>
          <marker :id="downlinkArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L8,4 L0,8 Z" fill="#326eaa" />
          </marker>
        </defs>

        <g class="legend">
          <line x1="32" y1="35" x2="70" y2="35" class="uplink" :marker-end="`url(#${uplinkArrow})`" />
          <text x="82" y="40">上行穿越</text>
          <line x1="32" y1="64" x2="70" y2="64" class="downlink" :marker-end="`url(#${downlinkArrow})`" />
          <text x="82" y="69">下行穿越</text>
        </g>

        <rect x="24" y="122" width="346" :height="remBoxHeight" class="device-shell" />
        <foreignObject x="42" y="140" width="150" height="88">
          <div class="device-copy">
            <small>REM 柜台</small>
            <strong>{{ snapshot.model_label }}</strong>
            <span>{{ remTitle }}</span>
          </div>
        </foreignObject>

        <template v-if="!isHardCore">
          <g class="interface client-interface">
            <rect x="210" y="194" width="140" height="58" />
            <foreignObject x="220" y="199" width="120" height="25">
              <div xmlns="http://www.w3.org/1999/xhtml" class="interface-name">
                <input
                  v-if="editable"
                  :value="snapshot.client_interface.name"
                  maxlength="32"
                  aria-label="第 1 个接口名称"
                  @input="updateInterfaceName('client', $event)"
                />
                <span v-else :title="snapshot.client_interface.name">{{ snapshot.client_interface.name }}</span>
              </div>
            </foreignObject>
            <text x="280" y="239" class="ip">{{ snapshot.client_interface.ip_address }}</text>
          </g>
          <g class="interface market-interface">
            <rect x="210" y="276" width="140" height="58" />
            <foreignObject x="220" y="281" width="120" height="25">
              <div xmlns="http://www.w3.org/1999/xhtml" class="interface-name">
                <input
                  v-if="editable"
                  :value="snapshot.market_interface.name"
                  maxlength="32"
                  aria-label="第 2 个接口名称"
                  @input="updateInterfaceName('market', $event)"
                />
                <span v-else :title="snapshot.market_interface.name">{{ snapshot.market_interface.name }}</span>
              </div>
            </foreignObject>
            <text x="280" y="321" class="ip">{{ snapshot.market_interface.ip_address }}</text>
          </g>
        </template>
        <g v-else class="hard-core-interface-list">
          <g
            v-for="(row, index) in hardCoreInterfaces"
            :key="index"
            class="interface hard-core-interface-row"
            :class="`${row.slot}-interface`"
            :data-interface-position="index + 1"
          >
            <rect x="210" :y="194 + index * 58" width="140" height="58" />
            <foreignObject
              x="220"
              :y="199 + index * 58 + (row.ip ? 0 : 12)"
              width="120"
              height="25"
            >
              <div xmlns="http://www.w3.org/1999/xhtml" class="interface-name">
                <input
                  v-if="editable"
                  :value="row.name"
                  maxlength="32"
                  :aria-label="`第 ${index + 1} 个接口名称`"
                  @input="updateInterfaceName(row.slot, $event, row.auxiliaryIndex)"
                />
                <span v-else :title="row.name">{{ row.name }}</span>
              </div>
            </foreignObject>
            <text v-if="row.ip" x="280" :y="239 + index * 58" class="ip">{{ row.ip }}</text>
          </g>
        </g>

        <rect x="24" y="468" width="346" height="132" class="device-shell" />
        <foreignObject x="42" y="491" width="155" height="82">
          <div class="device-copy">
            <small>抓包节点</small>
            <strong>SLNIC 板卡</strong>
            <span>{{ slnicTitle }}</span>
          </div>
        </foreignObject>
        <g class="slnic-ports">
          <g v-for="(port, index) in snapshot.slnic_ports" :key="port.port">
            <rect x="258" :y="482 + index * 27" width="92" height="24" />
            <text x="278" :y="499 + index * 27">{{ port.port }}</text>
            <text x="304" :y="499 + index * 27" class="port-label">{{ port.label }}</text>
          </g>
        </g>

        <rect x="520" y="28" width="270" height="112" class="switch-shell" />
        <foreignObject x="542" y="52" width="226" height="66"><div class="switch-copy"><strong>{{ snapshot.client_switch_label }}</strong><span>客户端</span></div></foreignObject>
        <rect x="642" y="288" width="230" height="126" class="switch-shell" />
        <foreignObject x="662" y="319" width="190" height="66"><div class="switch-copy"><strong>{{ snapshot.market_switch_label }}</strong><span>市场端</span></div></foreignObject>

        <g class="links">
          <path data-link="client-uplink-main" d="M520 111 H468 V214 H350" class="uplink" :marker-end="`url(#${uplinkArrow})`" />
          <path data-link="client-uplink-slnic-0" d="M468 214 V494 H350" class="uplink" :marker-end="`url(#${uplinkArrow})`" />
          <path data-link="market-uplink-main" :d="marketUplinkMainPath" class="uplink" :marker-end="`url(#${uplinkArrow})`" />
          <path data-link="market-uplink-slnic-1" d="M520 350 V521 H350" class="uplink" :marker-end="`url(#${uplinkArrow})`" />

          <path data-link="client-downlink-main" d="M350 241 H448 V84 H520" class="downlink" :marker-end="`url(#${downlinkArrow})`" />
          <path data-link="client-downlink-slnic-3" d="M448 241 H430 V575 H350" class="downlink" :marker-end="`url(#${downlinkArrow})`" />
          <path data-link="market-downlink-main" :d="marketDownlinkMainPath" class="downlink" :marker-end="`url(#${downlinkArrow})`" />
          <path data-link="market-downlink-slnic-2" :d="marketDownlinkSlnicPath" class="downlink" :marker-end="`url(#${downlinkArrow})`" />

          <circle cx="468" cy="214" r="4" class="uplink-dot" />
          <circle cx="520" cy="350" r="4" class="uplink-dot" />
          <circle cx="448" cy="241" r="4" class="downlink-dot" />
          <circle cx="548" :cy="marketDownlinkJunctionY" r="4" class="downlink-dot" />
        </g>
      </svg>
    </div>
  </div>
</template>

<style scoped>
.wiring-topology{width:100%;min-width:0;max-width:100%;overflow:hidden;border:1px solid var(--ui-border);border-radius:8px;background:#fbfdfd}.wiring-scroll{width:100%;min-width:0;overflow-x:auto;overscroll-behavior-inline:contain}.wiring-scroll:focus-visible{outline:2px solid var(--ui-primary);outline-offset:2px}.wiring-scroll svg{display:block;width:100%;min-width:760px;height:auto}.wiring-empty{display:flex;min-height:240px;flex-direction:column;align-items:center;justify-content:center;padding:28px;color:var(--ui-text-tertiary);text-align:center}.wiring-empty :deep(svg){width:34px;height:34px}.wiring-empty strong{margin-top:12px;color:var(--ui-text-secondary);font-size:14px}.wiring-empty span{max-width:34ch;margin-top:6px;font-size:12px;line-height:1.6}.device-shell,.switch-shell{fill:#fff;stroke:#9badb1;stroke-width:1.5}.switch-shell{fill:#f8fbfb}.device-copy,.switch-copy{display:flex;width:100%;height:100%;flex-direction:column;align-items:center;justify-content:center;color:#20383e;text-align:center}.device-copy small{color:#718489;font-size:11px}.device-copy strong{margin-top:4px;color:#0e806f;font-size:17px}.device-copy span{display:-webkit-box;max-width:100%;margin-top:7px;overflow:hidden;color:#52676c;font-size:11px;line-height:1.35;overflow-wrap:anywhere;-webkit-box-orient:vertical;-webkit-line-clamp:2}.switch-copy strong{display:-webkit-box;overflow:hidden;font-size:15px;line-height:1.4;-webkit-box-orient:vertical;-webkit-line-clamp:2}.switch-copy span{margin-top:7px;color:#718489;font-size:11px}.interface rect,.slnic-ports rect{fill:#dcefeb;stroke:#6da99e;stroke-width:1}.interface text{fill:#17353a;font-size:12px;font-weight:600;text-anchor:middle}.interface text.ip{font-family:"Cascadia Code",Consolas,monospace;font-size:11px;font-weight:500}.interface-name{display:flex;width:100%;height:100%;align-items:center;justify-content:center}.interface-name input,.interface-name span{box-sizing:border-box;width:100%;height:22px;color:#17353a;font-family:inherit;font-size:12px;font-weight:600;line-height:20px;text-align:center}.interface-name span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.interface-name input{border:1px solid #4c9588;border-radius:3px;outline:0;background:#fff;padding:0 4px}.interface-name input:focus{border-color:#0e806f;box-shadow:0 0 0 1px #0e806f}.slnic-ports text{fill:#20383e;font-size:11px}.slnic-ports .port-label{fill:#62767b;font-size:9px}.links path,.legend line{fill:none;stroke-width:2}.uplink{stroke:#c43d47}.downlink{stroke:#326eaa}.legend text{fill:#52676c;font-size:12px}.uplink-dot{fill:#c43d47}.downlink-dot{fill:#326eaa}.compact .wiring-scroll svg{min-width:720px}@media(max-width:767px){.wiring-scroll svg{min-width:720px}.wiring-empty{min-height:200px}}
</style>
