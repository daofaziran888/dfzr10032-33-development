<template>
  <section class="page" data-module="transform">
    <header class="page-head">
      <div>
        <h2>采集转换管道管理</h2>
        <p class="page-desc">
          维护转换节点并按节点依赖执行转换；每条结果都记录使用的输入版本，失败节点可追溯到具体明细。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记转换节点</button>
        <button class="btn" type="button" @click="exportRows">导出采集转换管道清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集转换管道数据，可先登记转换节点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集转换管道记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="panel">
      <header class="panel-head">
        <div>
          <h3>转换执行</h3>
          <p class="page-desc">
            运行开始时固定输入版本；前置节点未确认时后继节点直接阻塞，不做“拿旧值顶上”的处理。
          </p>
        </div>
        <div class="panel-actions">
          <label class="inline-field">
            <span>执行批次</span>
            <select v-model="selectedBatch">
              <option v-for="batch in batches" :key="batch.批次编号" :value="batch.批次编号">
                {{ batch.批次编号 }} · 数据版本 {{ batch.数据版本 }}
              </option>
            </select>
          </label>
          <button class="btn primary" type="button" :disabled="busy" @click="execute(true)">
            {{ busy ? '执行中…' : '按依赖执行转换' }}
          </button>
          <button class="btn" type="button" :disabled="busy" @click="execute(false)">全量重跑</button>
        </div>
      </header>

      <p v-if="runMessage" class="panel-note">{{ runMessage }}</p>

      <div v-if="latestRun" class="run-summary">
        <span>运行 {{ latestRun.运行编号 }}</span>
        <span>批次 {{ latestRun.数据批次 }}</span>
        <span>状态 {{ latestRun.执行状态 }}</span>
        <span>失败节点 {{ latestRun.失败节点 || '无' }}</span>
        <span>阻塞节点 {{ latestRun.阻塞节点 || '无' }}</span>
        <span>沿用节点 {{ latestRun.复用节点 || '无' }}</span>
      </div>

      <table v-if="latestRun" class="data-table">
        <thead>
          <tr>
            <th>节点</th>
            <th>执行状态</th>
            <th>结果数量</th>
            <th>失败节点</th>
            <th>失败原因</th>
            <th>沿用运行</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in latestRun.node_runs" :key="`${item.运行编号}-${item.节点编号}`">
            <td>{{ item.节点编号 }} · {{ item.节点名称 }}</td>
            <td>{{ item.执行状态 }}</td>
            <td>{{ item.结果数量 }}</td>
            <td>{{ item.失败节点 || '—' }}</td>
            <td>{{ item.失败原因 || '—' }}</td>
            <td>{{ item.复用运行编号 || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <header class="panel-head">
        <div>
          <h3>输入明细与更正</h3>
          <p class="page-desc">
            更正只影响被改动的字段版本；未被波及的分支在重试时沿用原结果，不会被刷新成新值。
          </p>
        </div>
        <div class="panel-actions">
          <label class="inline-field">
            <span>查看批次</span>
            <select v-model="viewBatch" @change="reloadInputs">
              <option value="">最新批次</option>
              <option v-for="batch in batches" :key="batch.批次编号" :value="batch.批次编号">
                {{ batch.批次编号 }}
              </option>
            </select>
          </label>
        </div>
      </header>

      <table class="data-table">
        <thead>
          <tr>
            <th>明细编号</th>
            <th>箱号</th>
            <th>地区</th>
            <th>重量克</th>
            <th>输入版本</th>
            <th>字段版本</th>
            <th>数据批次</th>
            <th>更正</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in inputs" :key="String(item.id)">
            <td>{{ item.明细编号 }}</td>
            <td>{{ item.箱号 }}</td>
            <td>{{ item.地区 }}</td>
            <td>{{ item.重量克 }}</td>
            <td>{{ item.输入版本 }}</td>
            <td class="version-cell">{{ formatVersions(item.字段版本) }}</td>
            <td>{{ item.数据批次 }}</td>
            <td>
              <button class="link" type="button" @click="openCorrection(item)">更正字段</button>
            </td>
          </tr>
          <tr v-if="!inputs.length">
            <td colspan="8" class="empty-state">该批次没有输入明细</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <header class="panel-head">
        <div>
          <h3>转换结果</h3>
          <p class="page-desc">
            每条结果带输入版本与输出字段版本；未确认的结果标注失败节点，不能用于后续节点或血缘起点。
          </p>
        </div>
        <div class="panel-actions">
          <label class="inline-field">
            <span>结果批次</span>
            <select v-model="resultBatch" @change="reloadResults">
              <option value="">全部批次</option>
              <option v-for="batch in batches" :key="batch.批次编号" :value="batch.批次编号">
                {{ batch.批次编号 }}
              </option>
            </select>
          </label>
          <label class="inline-field">
            <span>确认状态</span>
            <select v-model="resultStatus" @change="reloadResults">
              <option value="">全部</option>
              <option value="已确认">已确认</option>
              <option value="未确认">未确认</option>
            </select>
          </label>
        </div>
      </header>

      <table class="data-table">
        <thead>
          <tr>
            <th>结果编号</th>
            <th>节点</th>
            <th>明细/分组</th>
            <th>输出值</th>
            <th>输入版本</th>
            <th>输出字段版本</th>
            <th>数据批次</th>
            <th>确认状态</th>
            <th>失败节点</th>
            <th>追溯</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in results" :key="String(item.id)">
            <td>{{ item.结果编号 }}</td>
            <td>{{ item.节点编号 }}</td>
            <td>{{ item.分组键 || item.明细编号 || '—' }}</td>
            <td>{{ item.输出值 ?? '—' }}</td>
            <td class="version-cell">{{ item.输入版本 }}</td>
            <td class="version-cell">{{ item.输出字段版本 || '—' }}</td>
            <td>{{ item.数据批次 }}</td>
            <td>{{ item.确认状态 }}</td>
            <td>{{ item.失败节点 || '—' }}</td>
            <td>
              <button
                v-if="item.确认状态 === '已确认'"
                class="link"
                type="button"
                @click="traceResult(item)"
              >
                查看血缘
              </button>
              <span v-else>—</span>
            </td>
          </tr>
          <tr v-if="!results.length">
            <td colspan="10" class="empty-state">还没有转换结果，先执行一次转换</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="trace" class="panel">
      <header class="panel-head">
        <div>
          <h3>血缘追溯 · {{ trace.result.结果编号 }}</h3>
          <p class="page-desc">
            从已确认结果逐级向上定位原始字段版本；同一明细变更前后的结果分别对齐各自批次。
          </p>
        </div>
        <div class="panel-actions">
          <button class="btn ghost" type="button" @click="trace = null">收起</button>
        </div>
      </header>
      <ul class="trace-tree">
        <li v-for="(node, index) in flattenTrace(trace.sources)" :key="index" :style="{ paddingLeft: `${node.depth * 18}px` }">
          <span class="trace-node">{{ node.downstream }} ← {{ node.upstream || '原始输入' }}</span>
          <span class="trace-field">{{ node.sourceField }}（{{ node.sourceVersion || '—' }}）→ {{ node.targetField }}</span>
          <span class="trace-meta">{{ node.batch }} · {{ node.resultNumber }}</span>
        </li>
      </ul>
    </section>

    <section v-if="history.length" class="panel">
      <header class="panel-head">
        <div>
          <h3>批次对照 · {{ historyDetail }}</h3>
          <p class="page-desc">同一字段在不同批次的结果应始终与各自批次的字段版本一致。</p>
        </div>
        <div class="panel-actions">
          <button class="btn ghost" type="button" @click="history = []">收起</button>
        </div>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>结果编号</th>
            <th>节点</th>
            <th>输出值</th>
            <th>输入版本</th>
            <th>输入字段版本</th>
            <th>数据批次</th>
            <th>运行编号</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in history" :key="String(item.id)">
            <td>{{ item.结果编号 }}</td>
            <td>{{ item.节点编号 }}</td>
            <td>{{ item.输出值 ?? '—' }}</td>
            <td class="version-cell">{{ item.输入版本 }}</td>
            <td class="version-cell">{{ formatVersions(item.输入字段版本) }}</td>
            <td>{{ item.数据批次 }}</td>
            <td>{{ item.运行编号 }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记转换节点</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in columns.slice(0, 3)" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createFields[field]" :placeholder="`请输入${field}`" required />
            </label>
            <label class="modal-field">
              <span>转换表达式</span>
              <input v-model="createFields['转换表达式']" placeholder="例如 重量克 / 1000" />
            </label>
            <label class="modal-field">
              <span>依赖节点</span>
              <input v-model="createFields['依赖节点']" placeholder="多个依赖用逗号分隔" />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>

      <div v-if="correctionTarget" class="modal-overlay" @click.self="correctionTarget = null">
        <div class="modal-card">
          <header class="modal-head">
            <h3>更正输入明细 · {{ correctionTarget.明细编号 }}</h3>
            <button class="btn ghost" type="button" @click="correctionTarget = null">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCorrection">
            <p class="modal-hint">
              只有被改动的字段会换字段版本；未受影响的节点在重试时沿用原结果。
            </p>
            <label v-for="field in inputFields" :key="field" class="modal-field">
              <span>{{ field }}（当前：{{ correctionTarget[field] }}）</span>
              <input v-model="correctionValues[field]" :placeholder="`保持${field}不变`" />
            </label>
            <label class="modal-field">
              <span>更正说明</span>
              <input v-model="correctionRemark" placeholder="例如 分拣秤校准" />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="correctionTarget = null">取消</button>
              <button class="btn primary" type="submit">发布更正</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type BatchRow = { 批次编号: string; 数据版本: number; 明细数量?: number }
type NodeRun = {
  运行编号: string
  节点编号: string
  节点名称: string
  执行状态: string
  结果数量: number
  失败节点: string
  失败原因: string
  复用运行编号: string
}
type RunRow = {
  运行编号: string
  数据批次: string
  执行状态: string
  失败节点: string
  阻塞节点: string
  复用节点: string
  node_runs: NodeRun[]
  results: Row[]
}
type TraceLine = {
  depth: number
  upstream: string
  downstream: string
  sourceField: string
  sourceVersion: string
  targetField: string
  batch: string
  resultNumber: string
}
type TraceSource = { relation?: Record<string, string>; upstream?: TraceSource[] }
type TraceResponse = { result: Row; sources: TraceSource[] }

const ENDPOINT = '/api/transform'
const columns = ["节点编号", "输入来源", "输出目标", "转换表达式", "执行顺序", "依赖节点", "失败策略", "节点状态"]
const actions = ["提交校验", "启用节点", "停用节点"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const stats = [{"label": "节点总数", "value": 0}, {"label": "已启用节点", "value": 0}, {"label": "待校验节点", "value": 0}]
const inputFields = ["箱号", "地区", "重量克"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

const batches = ref<BatchRow[]>([])
const selectedBatch = ref('')
const viewBatch = ref('')
const inputs = ref<Row[]>([])
const results = ref<Row[]>([])
const resultBatch = ref('')
const resultStatus = ref('')
const latestRun = ref<RunRow | null>(null)
const runMessage = ref('')
const busy = ref(false)
const trace = ref<TraceResponse | null>(null)
const history = ref<Row[]>([])
const historyDetail = ref('')
const correctionTarget = ref<Row | null>(null)
const correctionValues = ref<Record<string, string>>({})
const correctionRemark = ref('')

function updateStats() {
  const data = rows.value
  stats.length > 0 && (stats[0].value = data.filter((r: Row) => r.status === statuses[1] || r.pending).length)
  stats.length > 1 && (stats[1].value = data.filter((r: Row) => r.status && !r.pending && !r.abnormal).length)
  stats.length > 2 && (stats[2].value = data.filter((r: Row) => r.abnormal).length)
}

function formatVersions(value: unknown): string {
  if (!value) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, string>)
      .map(([field, version]) => `${field}:${version || '—'}`)
      .join(' / ')
  }
  return String(value)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createFields.value }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.message || '登记失败')
    }
    showCreateModal.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('采集转换管道动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集转换管道操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('转换节点列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集转换管道列表读取失败'
  }
}

async function loadBatches() {
  const payload = await fetchJson<{ items: BatchRow[] }>(`${ENDPOINT}/batches`)
  batches.value = payload.items ?? []
  if (!selectedBatch.value && batches.value.length) {
    selectedBatch.value = batches.value[batches.value.length - 1].批次编号
  }
}

async function reloadInputs() {
  try {
    const query = viewBatch.value ? `?batch=${encodeURIComponent(viewBatch.value)}` : ''
    const payload = await fetchJson<{ items: Row[] }>(`${ENDPOINT}/inputs${query}`)
    inputs.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '输入明细读取失败'
  }
}

async function reloadResults() {
  const params = new URLSearchParams()
  if (resultBatch.value) params.set('batch', resultBatch.value)
  if (resultStatus.value) params.set('status', resultStatus.value)
  try {
    const payload = await fetchJson<{ items: Row[] }>(`${ENDPOINT}/results?${params.toString()}`)
    results.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转换结果读取失败'
  }
}

async function execute(retry: boolean) {
  busy.value = true
  runMessage.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/runs`, {
      method: 'POST',
      body: JSON.stringify({
        values: { batch: selectedBatch.value, retry },
      }),
    })
    const payload = await response.json()
    runMessage.value = payload.message ?? ''
    if (!response.ok) {
      throw new Error(payload.message || '转换执行失败')
    }
    latestRun.value = payload.entry ?? null
    await Promise.all([loadBatches(), reloadInputs(), reloadResults()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转换执行失败'
  } finally {
    busy.value = false
  }
}

async function traceResult(row: Row) {
  try {
    trace.value = await fetchJson<TraceResponse>(
      `${ENDPOINT}/results/${row.结果编号}/lineage`,
    )
    if (row.明细编号) {
      historyDetail.value = String(row.明细编号)
      const payload = await fetchJson<{ items: Row[] }>(
        `${ENDPOINT}/results/${row.明细编号}/history`,
      )
      history.value = payload.items ?? []
    } else {
      history.value = []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '血缘追溯失败'
  }
}

function flattenTrace(sources: TraceSource[], depth = 0): TraceLine[] {
  const flat: TraceLine[] = []
  for (const source of sources ?? []) {
    const relation = source.relation ?? {}
    flat.push({
      depth,
      upstream: relation.上游节点,
      downstream: relation.下游节点,
      sourceField: relation.来源字段,
      sourceVersion: relation.来源字段版本,
      targetField: relation.目标字段,
      batch: relation.数据批次,
      resultNumber: relation.结果编号,
    })
    flat.push(...flattenTrace(source.upstream ?? [], depth + 1))
  }
  return flat
}

function openCorrection(row: Row) {
  correctionTarget.value = row
  correctionValues.value = {}
  correctionRemark.value = ''
}

async function submitCorrection() {
  if (!correctionTarget.value) return
  const values: Record<string, string> = {}
  for (const field of inputFields) {
    const raw = correctionValues.value[field]
    if (raw !== undefined && raw !== '') values[field] = raw
  }
  if (!Object.keys(values).length) {
    errorMessage.value = '请至少填写一个要更正的字段'
    correctionTarget.value = null
    return
  }
  try {
    const response = await request(
      `${ENDPOINT}/inputs/${correctionTarget.value.明细编号}/corrections`,
      {
        method: 'POST',
        body: JSON.stringify({ values: { ...values, remark: correctionRemark.value } }),
      },
    )
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.message || '更正发布失败')
    }
    runMessage.value = payload.message ?? ''
    correctionTarget.value = null
    await Promise.all([loadBatches(), reloadInputs()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '更正发布失败'
  }
}

onMounted(async () => {
  await reload()
  try {
    await loadBatches()
    await Promise.all([reloadInputs(), reloadResults()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转换执行数据读取失败'
  }
})
</script>

<style scoped>
.panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 10px;
}
.panel-head h3 {
  margin: 0;
  font-size: 15px;
}
.panel-actions {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  flex-wrap: wrap;
}
.panel-note {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--muted);
}
.inline-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.inline-field select,
.modal-field input {
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.run-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 10px;
}
.version-cell {
  font-size: 12px;
  color: #334155;
  word-break: break-all;
}
.trace-tree {
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 13px;
}
.trace-tree li {
  padding: 5px 0;
  border-top: 1px dashed var(--border);
}
.trace-tree li:first-child {
  border-top: none;
}
.trace-node {
  font-weight: 600;
  margin-right: 10px;
}
.trace-field {
  color: #334155;
  margin-right: 10px;
}
.trace-meta {
  color: var(--muted);
  font-size: 12px;
}
.modal-hint {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}
</style>
