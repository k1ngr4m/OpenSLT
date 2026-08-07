# Run Detail Realtime Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将运行详情页改为 WebSocket 主推送、断线轮询兜底和日志游标增量补偿，消除连接正常时持续重复请求运行详情与全量日志的问题。

**Architecture:** 后端在现有运行日志列表接口上增加向后兼容的 `after_id` 游标。前端 `useRunLifecycle` 将详情同步与日志同步分离，通过 WebSocket 直接合并事件；只有断线时才启动 HTTP 兜底，并以退避策略重连。详情请求使用防抖和单飞队列，日志使用 ID 去重及分页补偿。

**Tech Stack:** FastAPI、SQLAlchemy、Pytest、Vue 3 Composition API、Axios、Vitest、TypeScript

## Global Constraints

- 保持 `GET /api/v1/runs/{run_id}/logs` 的列表响应以及 `level`、`source`、`keyword` 查询参数兼容。
- `after_id` 必须为非负整数；增量结果按日志 `id` 升序，每页最多 5000 条。
- `useRunLifecycle` 继续公开 `{ load, logs, run }`，其中 `load` 仅刷新运行详情。
- WebSocket 正常时不得启动固定轮询；断线轮询间隔固定为 5000ms。
- 重连退避依次为 `1000、2000、5000、10000、30000ms`，之后保持 30000ms。
- 终态为 `completed`、`cancelled`、`execution_failed`、`parse_failed`、`precheck_failed`、`timed_out`。
- 不新增前端或后端依赖，不修改数据库结构，不改变 WebSocket 服务端事件格式。
- 保留工作区内既有未提交改动；不得把无关修改加入本任务提交。

---

## File Map

- `backend/app/api/routes/runs.py`：为运行日志列表增加 `after_id` 过滤和确定性排序。
- `backend/tests/test_run_logs.py`：覆盖日志初始列表、增量游标及既有过滤条件组合。
- `frontend/src/composables/useRunLifecycle.ts`：实现详情单飞刷新、日志增量合并、WebSocket 主同步、断线轮询和退避重连。
- `frontend/src/composables/useRunLifecycle.test.ts`：使用可控 FakeWebSocket 和 fake timers 验证正常路径、断线恢复、终态及资源清理。
- `frontend/openapi.json`、`frontend/src/types/api.generated.ts`：重新生成新增查询参数的接口描述；这两个文件已有其他工作区改动，生成后只核对本接口差异，不单独提交既有内容。
- `RELEASES.json`：追加一条用户可见的 `fixed` 发布说明；该文件已有其他工作区改动，必须在原有 `unreleased` 数组上增量修改。

### Task 1: 后端日志游标接口

**Files:**
- Create: `backend/tests/test_run_logs.py`
- Modify: `backend/app/api/routes/runs.py:1039-1046`

**Interfaces:**
- Consumes: `LogRecord.id` 主键和现有 `GET /api/v1/runs/{run_id}/logs`。
- Produces: 可选查询参数 `after_id: int | None`；提供参数时返回 `id > after_id` 的最多 5000 条日志。

- [ ] **Step 1: 写入失败的后端接口测试**

创建 `backend/tests/test_run_logs.py`，通过现有测试助手创建运行，再直接插入三条可区分的运行日志：

```python
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models import LogRecord
from conftest import create_plan_scenario, create_resource, publish_workflow


def create_run(client: TestClient, headers: dict[str, str]) -> dict:
    resource = create_resource(client, headers, "REM-log-cursor")
    plan, scenario = create_plan_scenario(client, headers, resource_ids=[resource["id"]])
    publish_workflow(client, headers, scenario, [resource["id"]], [{
        "node_key": "wiring",
        "node_type": "wiring_confirmation",
        "name": "确认接线",
        "config": {"diagram": "placeholder"},
    }])
    response = client.post("/api/v1/runs", headers=headers, json={
        "plan_id": plan["id"],
        "scenario_id": scenario["id"],
        "resource_ids": [resource["id"]],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_run_logs_support_incremental_after_id_and_existing_filters(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    run = create_run(client, admin_headers)
    with SessionLocal() as db:
        records = [
            LogRecord(log_type="run", level="INFO", event="cursor.first", message="first", trace_id=run["trace_id"], run_id=run["id"], source="worker", detail={}),
            LogRecord(log_type="run", level="WARNING", event="cursor.second", message="second", trace_id=run["trace_id"], run_id=run["id"], source="worker", detail={}),
            LogRecord(log_type="run", level="ERROR", event="cursor.third", message="third", trace_id=run["trace_id"], run_id=run["id"], source="api", detail={}),
        ]
        db.add_all(records)
        db.flush()
        ids = [record.id for record in records]
        db.commit()

    initial = client.get(f"/api/v1/runs/{run['id']}/logs", headers=admin_headers)
    assert initial.status_code == 200
    assert [item["id"] for item in initial.json()] == ids

    incremental = client.get(
        f"/api/v1/runs/{run['id']}/logs",
        headers=admin_headers,
        params={"after_id": ids[0]},
    )
    assert incremental.status_code == 200
    assert [item["id"] for item in incremental.json()] == ids[1:]

    filtered = client.get(
        f"/api/v1/runs/{run['id']}/logs",
        headers=admin_headers,
        params={"after_id": ids[0], "source": "api", "level": "ERROR"},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [ids[2]]

    invalid = client.get(
        f"/api/v1/runs/{run['id']}/logs",
        headers=admin_headers,
        params={"after_id": -1},
    )
    assert invalid.status_code == 422
```

- [ ] **Step 2: 运行测试并确认游标断言失败**

Run: `python -m pytest backend/tests/test_run_logs.py -q`

Expected: FAIL；当前接口忽略 `after_id`，增量结果仍包含第一条日志，负数参数也不会返回 422。

- [ ] **Step 3: 实现最小后端变更**

在 `backend/app/api/routes/runs.py` 导入已存在的 `Query`（若当前导入列表尚无该符号），将接口签名和查询构造调整为：

```python
@router.get("/runs/{run_id}/logs", response_model=typing.List[LogOut])
def list_run_logs(
    run_id: int,
    level: typing.Union[str, None] = None,
    source: typing.Union[str, None] = None,
    keyword: typing.Union[str, None] = None,
    after_id: typing.Union[int, None] = Query(default=None, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> typing.List[LogRecord]:
    query = select(LogRecord).where(LogRecord.run_id == run_id)
    if level:
        query = query.where(LogRecord.level == level.upper())
    if source:
        query = query.where(LogRecord.source == source)
    if keyword:
        query = query.where(LogRecord.message.contains(keyword))
    if after_id is not None:
        query = query.where(LogRecord.id > after_id)
    return list(db.scalars(query.order_by(LogRecord.id).limit(5000)).all())
```

- [ ] **Step 4: 运行后端目标测试**

Run: `python -m pytest backend/tests/test_run_logs.py -q`

Expected: PASS。

- [ ] **Step 5: 提交独立后端变更**

```bash
git add backend/app/api/routes/runs.py backend/tests/test_run_logs.py
git commit -m "Add incremental run log cursor"
```

提交前运行 `git diff --cached --name-only`，输出必须只包含上述两个文件。

### Task 2: WebSocket 正常连接路径与请求去重

**Files:**
- Modify: `frontend/src/composables/useRunLifecycle.test.ts`
- Modify: `frontend/src/composables/useRunLifecycle.ts`

**Interfaces:**
- Consumes: Task 1 的 `GET /runs/{id}/logs?after_id=<id>`。
- Produces: 保持 `{ load, logs, run }`；`load()` 只刷新详情，`snapshot` 不刷新 HTTP，`status` 只合并刷新详情，`log` 只合并日志。

- [ ] **Step 1: 扩展可控 FakeWebSocket**

将测试双替换为支持连接、关闭和多实例追踪的版本：

```typescript
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close = vi.fn(() => this.onclose?.({ code: 1000 } as CloseEvent))

  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }

  open() {
    this.onopen?.(new Event("open"))
  }

  message(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>)
  }

  disconnect(code = 1006) {
    this.onclose?.({ code } as CloseEvent)
  }
}
```

将类型导入扩展为 `import type { RunDetail, RunLog } from '@/types/run'`，并加入以下测试助手：

```typescript
let initialLogs: RunLog[] = []
let runStatus: RunDetail["status"] = "draft"
let runProgress = 0

function runLog(id: number): RunLog {
  return {
    id,
    event_id: null,
    log_type: "run",
    level: "INFO",
    event: "run.test",
    message: `log-${id}`,
    trace_id: "trace-9",
    user_id: null,
    run_id: 9,
    step_id: null,
    source: "test",
    duration_ms: null,
    result: null,
    http_method: null,
    http_status: null,
    database_scope: null,
    sql_fingerprint: null,
    detail: {},
    is_redacted: true,
    created_at: "2026-08-07T12:00:00+08:00",
  }
}

function requestsFor(url: string) {
  return vi.mocked(api.get).mock.calls.filter(([value]) => value === url)
}

function mountLifecycle() {
  let lifecycle: ReturnType<typeof useRunLifecycle>
  const wrapper = mount(defineComponent({
    setup() {
      lifecycle = useRunLifecycle(9)
      return () => null
    },
  }))
  return { lifecycle: lifecycle!, wrapper }
}
```

在 `beforeEach` 中重置 `FakeWebSocket.instances = []`、`initialLogs = []`、`runStatus = "draft"` 和 `runProgress = 0`，并使用以下 mock 保持日志和详情调用可区分：

```typescript
vi.mocked(api.get).mockImplementation(async url => {
  if (url === "/runs/9/logs") return { data: initialLogs }
  return {
    data: { id: 9, status: runStatus, progress: runProgress, steps: [] } as unknown as RunDetail,
  }
})
```

- [ ] **Step 2: 写入正常路径失败测试**

增加以下三个测试场景，断言使用 `vi.mocked(api.get).mock.calls` 按 URL 分类：

```typescript
it("does not poll or reload for an initial snapshot while connected", async () => {
  const { wrapper } = mountLifecycle()
  await flushPromises()
  const socket = FakeWebSocket.instances[0]
  socket.open()
  socket.message({ type: "snapshot", status: "draft", progress: 0 })
  await vi.advanceTimersByTimeAsync(15_000)
  expect(requestsFor("/runs/9")).toHaveLength(1)
  expect(requestsFor("/runs/9/logs")).toHaveLength(1)
  wrapper.unmount()
})

it("coalesces status messages into one detail-only refresh", async () => {
  const { lifecycle, wrapper } = mountLifecycle()
  await flushPromises()
  const socket = FakeWebSocket.instances[0]
  socket.open()
  socket.message({ type: "status", status: "running", progress: 10 })
  socket.message({ type: "status", status: "running", progress: 20 })
  expect(lifecycle.run.value?.progress).toBe(20)
  await vi.advanceTimersByTimeAsync(200)
  await flushPromises()
  expect(requestsFor("/runs/9")).toHaveLength(2)
  expect(requestsFor("/runs/9/logs")).toHaveLength(1)
  wrapper.unmount()
})

it("merges websocket logs by id without HTTP reload", async () => {
  const { lifecycle, wrapper } = mountLifecycle()
  await flushPromises()
  const socket = FakeWebSocket.instances[0]
  socket.open()
  const item = { id: 41, level: "INFO", event: "run.test", message: "once", step_id: null, detail: {}, created_at: "2026-08-07T12:00:00+08:00" }
  socket.message({ type: "log", data: item })
  socket.message({ type: "log", data: item })
  expect(lifecycle.logs.value.map(log => log.id)).toEqual([41])
  expect(requestsFor("/runs/9/logs")).toHaveLength(1)
  wrapper.unmount()
})
```

`mountLifecycle()` 必须挂载一个调用 `useRunLifecycle(9)` 的最小组件并返回 `{ lifecycle, wrapper }`；`requestsFor(url)` 返回 `api.get.mock.calls.filter(([value]) => value === url)`。

- [ ] **Step 3: 运行测试并确认失败原因正确**

Run: `npm --prefix frontend run test -- --run src/composables/useRunLifecycle.test.ts`

Expected: FAIL；初始 `snapshot` 和固定 5 秒定时器仍产生重复请求，`status` 同时请求日志，重复 WebSocket 日志未去重。

- [ ] **Step 4: 实现职责拆分、日志合并和详情防抖单飞**

在 `useRunLifecycle.ts` 中加入以下状态和内部操作：

```typescript
const LOG_PAGE_SIZE = 5000
const TERMINAL_STATUSES = new Set<RunDetail["status"]>([
  "completed", "cancelled", "execution_failed", "parse_failed", "precheck_failed", "timed_out",
])

let runLoadPromise: Promise<void> | null = null
let runReloadQueued = false
let refreshTimer: number | undefined
let initialLogLoadPromise: Promise<void> | null = null
let lastHttpLogId = 0

function mergeLogs(items: RunLog[]) {
  const merged = new Map(logs.value.map(item => [item.id, item]))
  for (const item of items) merged.set(item.id, item)
  logs.value = [...merged.values()].sort((left, right) => left.id - right.id)
}

async function requestRun() {
  const response = await api.get<RunDetail>(`/runs/${runId}`)
  run.value = response.data
}

async function loadRun() {
  if (runLoadPromise) {
    runReloadQueued = true
    return runLoadPromise
  }
  runLoadPromise = (async () => {
    do {
      runReloadQueued = false
      await requestRun()
    } while (runReloadQueued)
  })().finally(() => {
    runLoadPromise = null
  })
  return runLoadPromise
}

async function loadInitialLogs() {
  if (initialLogLoadPromise) return initialLogLoadPromise
  initialLogLoadPromise = api.get<RunLog[]>(`/runs/${runId}/logs`)
    .then(response => {
      mergeLogs(response.data)
      for (const item of response.data) lastHttpLogId = Math.max(lastHttpLogId, item.id)
    })
    .finally(() => {
      initialLogLoadPromise = null
    })
  return initialLogLoadPromise
}

function scheduleRunRefresh() {
  if (refreshTimer) window.clearTimeout(refreshTimer)
  refreshTimer = window.setTimeout(() => {
    refreshTimer = undefined
    void loadRun().catch(() => undefined)
  }, 200)
}
```

挂载时并行调用 `loadRun()`、`loadInitialLogs()` 和 `connect()`。消息处理规则必须精确为：

```typescript
// WebSocket 日志只合并显示，不推进 lastHttpLogId。
if (payload.type === "log" && payload.data) mergeLogs([payload.data])
if ((payload.type === "status" || payload.type === "snapshot") && run.value) {
  if (payload.status) run.value.status = payload.status
  if (typeof payload.progress === "number") run.value.progress = payload.progress
}
if (payload.type === "status") scheduleRunRefresh()
```

删除固定 `setInterval(load, 5000)`。返回值改为 `{ load: loadRun, logs, run }`，卸载时清理 `refreshTimer`。

- [ ] **Step 5: 运行前端目标测试**

Run: `npm --prefix frontend run test -- --run src/composables/useRunLifecycle.test.ts`

Expected: Task 2 的正常路径测试 PASS；Task 3 尚未加入断线测试。

- [ ] **Step 6: 提交正常路径变更**

```bash
git add frontend/src/composables/useRunLifecycle.ts frontend/src/composables/useRunLifecycle.test.ts
git commit -m "Use websocket events for run detail updates"
```

提交前确认暂存区只包含这两个文件。

### Task 3: 断线轮询、退避重连与终态

**Files:**
- Modify: `frontend/src/composables/useRunLifecycle.test.ts`
- Modify: `frontend/src/composables/useRunLifecycle.ts`

**Interfaces:**
- Consumes: Task 2 的 `loadRun()`、`mergeLogs()` 和消息处理器。
- Produces: 断线校准、5000ms 兜底轮询、指定退避重连、重连增量补偿和终态停止策略。

- [ ] **Step 1: 写入断线与重连失败测试**

增加测试，先让初始日志返回 ID 10，再断开首个已打开的 WebSocket：

```typescript
it("polls incrementally while disconnected and stops after reconnect", async () => {
  initialLogs = [runLog(10)]
  const { wrapper } = mountLifecycle()
  await flushPromises()
  const first = FakeWebSocket.instances[0]
  first.open()
  first.message({ type: "log", data: runLog(99) })
  first.disconnect()
  await flushPromises()

  // WebSocket 的 99 只合并显示；HTTP 补偿仍从最后一次 HTTP 游标 10 开始。
  expect(api.get).toHaveBeenCalledWith("/runs/9/logs", { params: { after_id: 10 } })
  await vi.advanceTimersByTimeAsync(1_000)
  const second = FakeWebSocket.instances[1]
  expect(second).toBeTruthy()
  second.open()
  await flushPromises()

  const callsAfterReconnect = api.get.mock.calls.length
  await vi.advanceTimersByTimeAsync(5_000)
  expect(api.get.mock.calls).toHaveLength(callsAfterReconnect)
  wrapper.unmount()
})
```

再增加重连未成功时的退避序列测试：

```typescript
it("retries one websocket at a time with capped backoff", async () => {
  const { wrapper } = mountLifecycle()
  await flushPromises()
  const delays = [1_000, 2_000, 5_000, 10_000, 30_000, 30_000]
  for (const [index, delay] of delays.entries()) {
    FakeWebSocket.instances[index].disconnect()
    await vi.advanceTimersByTimeAsync(delay - 1)
    expect(FakeWebSocket.instances).toHaveLength(index + 1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeWebSocket.instances).toHaveLength(index + 2)
  }
  wrapper.unmount()
})
```

- [ ] **Step 2: 写入终态与卸载失败测试**

覆盖两条终态路径：

```typescript
it("does not reconnect after a terminal status but keeps the open socket for final logs", async () => {
  const { lifecycle, wrapper } = mountLifecycle()
  await flushPromises()
  const socket = FakeWebSocket.instances[0]
  socket.open()
  runStatus = "completed"
  runProgress = 100
  socket.message({ type: "status", status: "completed", progress: 100 })
  socket.message({ type: "log", data: runLog(99) })
  expect(socket.close).not.toHaveBeenCalled()
  socket.disconnect()
  await vi.advanceTimersByTimeAsync(60_000)
  expect(lifecycle.logs.value.some(log => log.id === 99)).toBe(true)
  expect(FakeWebSocket.instances).toHaveLength(1)
  wrapper.unmount()
})

it("clears reconnect, polling, and refresh timers on unmount", async () => {
  const { wrapper } = mountLifecycle()
  await flushPromises()
  FakeWebSocket.instances[0].disconnect()
  wrapper.unmount()
  await vi.advanceTimersByTimeAsync(60_000)
  expect(FakeWebSocket.instances).toHaveLength(1)
  expect(vi.getTimerCount()).toBe(0)
})

it("closes the concurrently opened socket when the initial run is terminal", async () => {
  runStatus = "completed"
  runProgress = 100
  const { wrapper } = mountLifecycle()
  await flushPromises()
  expect(FakeWebSocket.instances[0].close).toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(60_000)
  expect(FakeWebSocket.instances).toHaveLength(1)
  wrapper.unmount()
})
```

- [ ] **Step 3: 运行测试并确认断线能力缺失**

Run: `npm --prefix frontend run test -- --run src/composables/useRunLifecycle.test.ts`

Expected: FAIL；当前实现没有 `after_id` 增量补偿、轮询兜底、重连和终态计时器管理。

- [ ] **Step 4: 实现增量日志分页**

加入串行增量同步，重复触发时复用同一个 Promise：

```typescript
let logSyncPromise: Promise<void> | null = null

async function syncIncrementalLogs() {
  if (logSyncPromise) return logSyncPromise
  logSyncPromise = (async () => {
    if (lastHttpLogId === 0) {
      await loadInitialLogs()
      return
    }
    let afterId = lastHttpLogId
    while (true) {
      const response = await api.get<RunLog[]>(`/runs/${runId}/logs`, {
        params: { after_id: afterId },
      })
      mergeLogs(response.data)
      for (const item of response.data) lastHttpLogId = Math.max(lastHttpLogId, item.id)
      if (response.data.length < LOG_PAGE_SIZE || lastHttpLogId <= afterId) break
      afterId = lastHttpLogId
    }
  })().finally(() => {
    logSyncPromise = null
  })
  return logSyncPromise
}
```

`lastHttpLogId` 只能由初始或增量 HTTP 响应推进。即使 `logs` 中已经存在 ID 更大的 WebSocket 日志，补偿请求仍从 `lastHttpLogId` 开始，返回的重复项交由 `mergeLogs()` 去重。

- [ ] **Step 5: 实现连接状态机**

在 composable 内加入：

```typescript
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000] as const
const POLL_INTERVAL = 5000

let mounted = false
let terminal = false
let socket: WebSocket | null = null
let pollTimer: number | undefined
let reconnectTimer: number | undefined
let reconnectAttempt = 0
let reconnectSyncRequired = false
let initialRunPending = true

async function reconcile() {
  await Promise.allSettled([
    loadRun(),
    syncIncrementalLogs(),
  ])
}

function stopFallback() {
  if (pollTimer !== undefined) window.clearInterval(pollTimer)
  if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
  pollTimer = undefined
  reconnectTimer = undefined
}

function startFallback() {
  if (!mounted || terminal || pollTimer !== undefined) return
  void reconcile()
  pollTimer = window.setInterval(() => void reconcile(), POLL_INTERVAL)
}

function scheduleReconnect() {
  if (!mounted || terminal || reconnectTimer !== undefined) return
  const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)]
  reconnectAttempt += 1
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined
    connect()
  }, delay)
}
```

`connect()` 必须为每个候选 socket 绑定闭包，并忽略已经被替换的旧实例事件：

```typescript
function connect() {
  if (!mounted || terminal || socket) return
  const token = localStorage.getItem("access_token")
  const protocol = location.protocol === "https:" ? "wss" : "ws"
  const candidate = new WebSocket(`${protocol}://${location.host}/api/v1/ws/runs/${runId}?token=${token}`)
  socket = candidate
  candidate.onopen = () => {
    if (socket !== candidate) return
    stopFallback()
    reconnectAttempt = 0
    if (reconnectSyncRequired) void reconcile()
    reconnectSyncRequired = false
  }
  candidate.onmessage = event => handleMessage(event)
  candidate.onerror = () => candidate.close()
  candidate.onclose = () => {
    if (socket !== candidate) return
    socket = null
    if (!mounted || terminal) return
    reconnectSyncRequired = true
    startFallback()
    scheduleReconnect()
  }
}
```

将消息解析提取为 `handleMessage`，复用 Task 2 的规则。每次从 HTTP 或 WebSocket 得到状态后使用以下函数更新终态控制：

```typescript
function observeStatus(status: RunDetail["status"], initialHttp = false) {
  const wasTerminal = terminal
  terminal = TERMINAL_STATUSES.has(status)
  if (terminal) {
    stopFallback()
    if (initialHttp) {
      const current = socket
      socket = null
      current?.close()
    }
    return
  }
  if (wasTerminal && mounted && !socket) connect()
}

async function requestRun() {
  const response = await api.get<RunDetail>(`/runs/${runId}`)
  const isInitial = initialRunPending
  initialRunPending = false
  run.value = response.data
  observeStatus(response.data.status, isInitial)
}

function handleMessage(event: MessageEvent<string>) {
  const payload = JSON.parse(event.data) as RunSocketPayload
  if (payload.type === "log" && payload.data) mergeLogs([payload.data])
  if ((payload.type === "status" || payload.type === "snapshot") && run.value) {
    if (payload.status) {
      run.value.status = payload.status
      observeStatus(payload.status)
    }
    if (typeof payload.progress === "number") run.value.progress = payload.progress
  }
  if (payload.type === "status") scheduleRunRefresh()
}
```

- [ ] **Step 6: 启动并清理所有生命周期资源**

挂载时并行启动初始 HTTP 和 WebSocket，并显式吸收后台同步错误；卸载时按以下顺序清理：

```typescript
onMounted(() => {
  mounted = true
  void loadRun().catch(() => undefined)
  void loadInitialLogs().catch(() => undefined)
  connect()
})

onBeforeUnmount(() => {
  mounted = false
  stopFallback()
  if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
  refreshTimer = undefined
  const current = socket
  socket = null
  current?.close()
})
```

- [ ] **Step 7: 运行目标测试并提交**

Run: `npm --prefix frontend run test -- --run src/composables/useRunLifecycle.test.ts`

Expected: 全部 PASS，fake timers 结束时没有残留计时器。

```bash
git add frontend/src/composables/useRunLifecycle.ts frontend/src/composables/useRunLifecycle.test.ts
git commit -m "Add run detail websocket recovery"
```

提交前确认暂存区只包含这两个文件。

### Task 4: 接口产物、发布说明与完整验证

**Files:**
- Modify: `frontend/openapi.json`
- Modify: `frontend/src/types/api.generated.ts`
- Modify: `RELEASES.json`

**Interfaces:**
- Consumes: Task 1 的 FastAPI OpenAPI 参数定义以及 Task 2/3 的用户可见行为。
- Produces: 与后端一致的前端 API Schema、发布说明和完整验证证据。

- [ ] **Step 1: 重新生成前端 API 类型**

Run: `npm --prefix frontend run generate:api`

Expected: `frontend/openapi.json` 和 `frontend/src/types/api.generated.ts` 中 `/api/v1/runs/{run_id}/logs` 出现可选、最小值为 0 的 `after_id` query 参数。使用下面命令核对：

```bash
rg -n 'after_id' frontend/openapi.json frontend/src/types/api.generated.ts
```

这两个文件执行前已经存在其他未提交改动，不得假定整文件差异都属于本任务，也不得将既有差异单独提交为本任务内容。

- [ ] **Step 2: 增量追加发布说明**

在 `RELEASES.json` 的现有 `unreleased` 数组末尾加入：

```json
{
  "type": "fixed",
  "text": "运行详情采用 WebSocket 实时更新，并在断线时增量补齐状态和日志，避免持续重复请求。"
}
```

保留数组内全部既有条目和顺序。

- [ ] **Step 3: 验证发布元数据**

Run: `python tools/release_metadata.py`

Expected: 退出码 0。

- [ ] **Step 4: 运行后端完整测试**

Run: `python -m pytest`

Expected: 全部 PASS。

- [ ] **Step 5: 运行前端完整测试**

Run: `npm --prefix frontend run test`

Expected: 全部 PASS。

- [ ] **Step 6: 运行前端生产构建**

Run: `npm --prefix frontend run build`

Expected: `vue-tsc -b` 和 Vite production build 均成功。

- [ ] **Step 7: 检查最终差异范围**

运行：

```bash
git diff --check
git status --short
git diff -- backend/app/api/routes/runs.py backend/tests/test_run_logs.py frontend/src/composables/useRunLifecycle.ts frontend/src/composables/useRunLifecycle.test.ts RELEASES.json
```

确认本任务只改变增量日志接口、运行详情同步逻辑、对应测试、生成的 API 描述和一条发布说明；现有其他未提交改动保持原样。
