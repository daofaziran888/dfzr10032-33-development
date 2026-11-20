<template>
  <section class="page" data-module="lineage">
    <header class="page-head">
      <div>
        <h2>数据血缘追踪管理</h2>
        <p class="page-desc">维护血缘关系，围绕关系编号、上游节点、下游节点、来源字段做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记血缘关系</button>
        <button class="btn" type="button" @click="exportRows">导出数据血缘追踪清单</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无数据血缘追踪数据，可先登记血缘关系</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据血缘追踪记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="panel">
      <header class="panel-head">
        <div>
          <h3>转换血缘</h3>
          <p class="page-desc">
            转换执行会把“输入字段版本 → 输出字段版本”的关系写进血缘表；这里可以从已确认结果反查原始字段，
            或按字段版本定位受影响的结果。
          </p>
        </div>
        <div class="panel-actions">
          <label class="inline-field">
            <span>追溯结果编号</span>
            <input v-model="traceNumber" placeholder="例如 RES-0009" />
          </label>
          <button class="btn primary" type="button" @click="runTrace">追溯原始字段</button>
        </div>
      </header>

      <form class="filter-bar" @submit.prevent="reloadRelations">
        <label class="filter-item">
          <span>数据批次</span>
          <input v-model="relationFilters.batch" placeholder="例如 BATCH-002" />
        </label>
        <label class="filter-item">
          <span>节点编号</span>
          <input v-model="relationFilters.node" placeholder="例如 TRAN-KG" />
        </label>
        <label class="filter-item">
          <span>字段版本</span>
          <input v-model="relationFilters.field_version" placeholder="例如 D-0001-重量克-v2" />
        </label>
        <button class="btn" type="submit">查询关系</button>
        <button class="btn ghost" type="button" @click="resetRelations">重置条件</button>
      </form>

      <p v-if="traceNote" class="panel-note">{{ traceNote }}</p>
      <ul v-if="traceLines.length" class="trace-tree">
        <li v-for="(line, index) in traceLines" :key="index" :style="{ paddingLeft: `${line.depth * 18}px` }">
          <span class="trace-node">{{ line.downstream }} ← {{ line.upstream || '原始输入' }}</span>
          <span class="trace-field">{{ line.sourceField }}（{{ line.sourceVersion || '—' }}）→ {{ line.targetField }}</span>
          <span class="trace-meta">{{ line.batch }} · {{ line.resultNumber }}</span>
        </li>
      </ul>

      <table class="data-table">
        <thead>
          <tr>
            <th>关系编号</th>
            <th>结果编号</th>
            <th>上游节点</th>
            <th>下游节点</th>
            <th>来源字段</th>
            <th>来源字段版本</th>
            <th>目标字段</th>
            <th>输出字段版本</th>
            <th>数据批次</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in relations" :key="String(item.id)">
            <td>{{ item.关系编号 }}</td>
            <td>{{ item.结果编号 }}</td>
            <td>{{ item.上游节点 }}</td>
            <td>{{ item.下游节点 }}</td>
            <td>{{ item.来源字段 }}</td>
            <td class="version-cell">{{ item.来源字段版本 || '—' }}</td>
            <td>{{ item.目标字段 }}</td>
            <td class="version-cell">{{ item.输出字段版本 || '—' }}</td>
            <td>{{ item.数据批次 }}</td>
          </tr>
          <tr v-if="!relations.length">
            <td colspan="9" class="empty-state">还没有执行期血缘关系，先到采集转换管道执行一次转换</td>
          </tr>
        </tbody>
      </table>
    </section>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记血缘关系</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in columns.slice(0, 3)" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createFields[field]" :placeholder="`请输入${field}`" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
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

const ENDPOINT = '/api/lineage'
const columns = ["关系编号", "上游节点", "下游节点", "来源字段", "目标字段", "转换说明", "采集批次", "关系版本"]
const actions = ["记录关系", "验证关系", "归档关系"]
const statuses = ["草稿", "已记录", "已验证", "已归档"]
const stats = [{"label": "关系总数", "value": 0}, {"label": "已验证关系", "value": 0}, {"label": "待验证关系", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

const relations = ref<Row[]>([])
const relationFilters = ref<Record<string, string>>({})
const traceNumber = ref('')
const traceNote = ref('')
const traceLines = ref<TraceLine[]>([])

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

function updateStats() {
  const data = rows.value
  stats.length > 0 && (stats[0].value = data.filter((r: Row) => r.status === statuses[1] || r.pending).length)
  stats.length > 1 && (stats[1].value = data.filter((r: Row) => r.status && !r.pending && !r.abnormal).length)
  stats.length > 2 && (stats[2].value = data.filter((r: Row) => r.abnormal).length)
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
      throw new Error('数据血缘追踪动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据血缘追踪操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('血缘关系列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据血缘追踪列表读取失败'
  }
}

async function reloadRelations() {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(relationFilters.value)) {
    if (value) params.set(key, value)
  }
  try {
    const payload = await fetchJson<{ items: Row[] }>(
      `${ENDPOINT}/relations?${params.toString()}`,
    )
    relations.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '执行期血缘关系读取失败'
  }
}

function resetRelations() {
  relationFilters.value = {}
  void reloadRelations()
}

type TraceSource = {
  relation?: Record<string, string>
  upstream?: TraceSource[]
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

async function runTrace() {
  traceNote.value = ''
  traceLines.value = []
  const number = traceNumber.value.trim()
  if (!number) {
    traceNote.value = '请输入要追溯的结果编号'
    return
  }
  try {
    const payload = await fetchJson<{ result: Row; sources: TraceSource[] }>(
      `${ENDPOINT}/trace/${encodeURIComponent(number)}`,
    )
    traceLines.value = flattenTrace(payload.sources)
    traceNote.value = `结果 ${payload.result.结果编号} 为已确认结果，共 ${traceLines.value.length} 条字段版本关系`
  } catch (error) {
    traceNote.value = error instanceof Error ? error.message : '血缘追溯失败'
  }
}

onMounted(() => {
  void reload()
  void reloadRelations()
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
.inline-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.inline-field input {
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.panel-note {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--muted);
}
.version-cell {
  font-size: 12px;
  color: #334155;
  word-break: break-all;
}
.trace-tree {
  list-style: none;
  margin: 0 0 12px;
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
</style>
