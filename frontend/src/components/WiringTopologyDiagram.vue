<script setup lang="ts">
import { computed, useId } from 'vue'
import { Connection } from '@element-plus/icons-vue'
import type { WiringSnapshot } from '@/utils/wiring'

const props = withDefaults(defineProps<{
  snapshot?: WiringSnapshot | null
  compact?: boolean
  emptyMessage?: string
}>(), {
  snapshot: null,
  compact: false,
  emptyMessage: '请先绑定 REM 与 SLNIC，并补全 REM 接线配置',
})

const instanceId = useId().replace(/[^a-zA-Z0-9_-]/g, '')
const uplinkArrow = `wiring-uplink-${instanceId}`
const downlinkArrow = `wiring-downlink-${instanceId}`
const isHardCore = computed(() => props.snapshot?.topology_kind !== 'soft_core')
const remBoxHeight = computed(() => isHardCore.value ? 324 : 226)
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

        <g class="interface client-interface">
          <rect x="210" y="194" width="140" height="58" />
          <text x="280" y="218">{{ snapshot.client_interface.name }}</text>
          <text x="280" y="239" class="ip">{{ snapshot.client_interface.ip_address }}</text>
        </g>
        <g class="interface market-interface">
          <rect x="210" y="276" width="140" height="58" />
          <text x="280" y="300">{{ snapshot.market_interface.name }}</text>
          <text x="280" y="321" class="ip">{{ snapshot.market_interface.ip_address }}</text>
        </g>
        <g v-if="isHardCore" class="interface auxiliary-interface">
          <rect x="210" y="350" width="140" height="40" />
          <text x="280" y="375">{{ snapshot.auxiliary_interfaces[0] }}</text>
          <rect x="210" y="394" width="140" height="40" />
          <text x="280" y="419">{{ snapshot.auxiliary_interfaces[1] }}</text>
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
          <path d="M520 111 H468 V214 H350" class="uplink" :marker-end="`url(#${uplinkArrow})`" />
          <path d="M468 214 H448 V494 H350" class="uplink" :marker-end="`url(#${uplinkArrow})`" />
          <path d="M642 350 H490 V296 H350" class="uplink" :marker-end="`url(#${uplinkArrow})`" />
          <path d="M520 350 V521 H350" class="uplink" :marker-end="`url(#${uplinkArrow})`" />

          <path d="M350 241 H448 V84 H520" class="downlink" :marker-end="`url(#${downlinkArrow})`" />
          <path d="M448 241 H430 V575 H350" class="downlink" :marker-end="`url(#${downlinkArrow})`" />
          <path d="M350 323 H548 V380 H642" class="downlink" :marker-end="`url(#${downlinkArrow})`" />
          <path d="M548 323 V548 H350" class="downlink" :marker-end="`url(#${downlinkArrow})`" />

          <circle cx="468" cy="214" r="4" class="uplink-dot" />
          <circle cx="520" cy="350" r="4" class="uplink-dot" />
          <circle cx="448" cy="241" r="4" class="downlink-dot" />
          <circle cx="548" cy="323" r="4" class="downlink-dot" />
        </g>
      </svg>
    </div>
  </div>
</template>

<style scoped>
.wiring-topology{width:100%;min-width:0;max-width:100%;overflow:hidden;border:1px solid var(--ui-border);border-radius:8px;background:#fbfdfd}.wiring-scroll{width:100%;min-width:0;overflow-x:auto;overscroll-behavior-inline:contain}.wiring-scroll:focus-visible{outline:2px solid var(--ui-primary);outline-offset:2px}.wiring-scroll svg{display:block;width:100%;min-width:760px;height:auto}.wiring-empty{display:flex;min-height:240px;flex-direction:column;align-items:center;justify-content:center;padding:28px;color:var(--ui-text-tertiary);text-align:center}.wiring-empty :deep(svg){width:34px;height:34px}.wiring-empty strong{margin-top:12px;color:var(--ui-text-secondary);font-size:14px}.wiring-empty span{max-width:34ch;margin-top:6px;font-size:12px;line-height:1.6}.device-shell,.switch-shell{fill:#fff;stroke:#9badb1;stroke-width:1.5}.switch-shell{fill:#f8fbfb}.device-copy,.switch-copy{display:flex;width:100%;height:100%;flex-direction:column;align-items:center;justify-content:center;color:#20383e;text-align:center}.device-copy small{color:#718489;font-size:11px}.device-copy strong{margin-top:4px;color:#0e806f;font-size:17px}.device-copy span{display:-webkit-box;max-width:100%;margin-top:7px;overflow:hidden;color:#52676c;font-size:11px;line-height:1.35;overflow-wrap:anywhere;-webkit-box-orient:vertical;-webkit-line-clamp:2}.switch-copy strong{display:-webkit-box;overflow:hidden;font-size:15px;line-height:1.4;-webkit-box-orient:vertical;-webkit-line-clamp:2}.switch-copy span{margin-top:7px;color:#718489;font-size:11px}.interface rect,.slnic-ports rect{fill:#dcefeb;stroke:#6da99e;stroke-width:1}.interface text{fill:#17353a;font-size:12px;font-weight:600;text-anchor:middle}.interface text.ip{font-family:"Cascadia Code",Consolas,monospace;font-size:11px;font-weight:500}.auxiliary-interface rect{fill:#edf4f3}.slnic-ports text{fill:#20383e;font-size:11px}.slnic-ports .port-label{fill:#62767b;font-size:9px}.links path,.legend line{fill:none;stroke-width:2}.uplink{stroke:#c43d47}.downlink{stroke:#326eaa}.legend text{fill:#52676c;font-size:12px}.uplink-dot{fill:#c43d47}.downlink-dot{fill:#326eaa}.compact .wiring-scroll svg{min-width:720px}@media(max-width:767px){.wiring-scroll svg{min-width:720px}.wiring-empty{min-height:200px}}
</style>
