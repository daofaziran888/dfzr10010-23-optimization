<template>
  <section class="page" data-module="impact">
    <header class="page-head">
      <div>
        <h2>血缘影响工作台</h2>
        <p class="page-desc">按操作批次生成去重断环后的影响传播结果：同一批次只发布一份，环不重复展开，节点与边均携带字段路径。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="buildResult">生成影响传播结果</button>
        <button class="btn" type="button" :disabled="!detail" @click="downloadManifest">下载字段路径</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">影响结果总数</span>
        <strong class="stat-value">{{ total }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待复核</span>
        <strong class="stat-value">{{ countByStatus('待复核') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已核对</span>
        <strong class="stat-value">{{ countByStatus('已核对') }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>操作批次</span>
        <input v-model="batchInput" placeholder="如 20261115-18" />
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
            <button class="link" type="button" @click="viewResult(row)">查看</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无影响传播结果，可先输入操作批次生成</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="detail-panel">
      <h3>{{ detail.结果编号 }} · v{{ detail.版本 }} · {{ detail.结果状态 }}</h3>
      <p class="page-desc">
        去重后 {{ detail.节点数 }} 个节点、{{ detail.边数 }} 条边，断环 {{ detail.环数量 }} 处；
        导出状态：{{ detail.导出状态 }}；稳定口径：{{ detail.稳定口径版本 ? `v${detail.稳定口径版本}` : '暂无' }}
        <span v-if="detail.异常来源?.length" class="error-text">；异常来源：{{ detail.异常来源.join('、') }}</span>
      </p>

      <template v-if="detail.批次冲突?.length">
        <h4>批次冲突（同一字段只采纳一条方向）</h4>
        <table class="data-table">
          <thead>
            <tr><th>字段</th><th>迁移批次</th><th>回滚批次</th><th>采纳方向</th><th>采纳映射</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in detail.批次冲突" :key="item.字段">
              <td>{{ item.字段 }}</td>
              <td>{{ item.迁移批次 }}</td>
              <td>{{ item.回滚批次 }}</td>
              <td>{{ item.采纳方向 }}</td>
              <td>{{ item.采纳映射 }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <h4>去重节点（含字段路径）</h4>
      <table class="data-table">
        <thead>
          <tr><th>表名</th><th>深度</th><th>字段路径</th></tr>
        </thead>
        <tbody>
          <tr v-for="node in detail.节点清单" :key="node.表名">
            <td>{{ node.表名 }}</td>
            <td>{{ node.深度 }}</td>
            <td><div v-for="path in node.字段路径" :key="path">{{ path }}</div></td>
          </tr>
        </tbody>
      </table>

      <h4>去重边（含字段路径）</h4>
      <table class="data-table">
        <thead>
          <tr><th>上游表名</th><th>下游表名</th><th>字段路径</th><th>来源批次</th></tr>
        </thead>
        <tbody>
          <tr v-for="edge in detail.边清单" :key="`${edge.上游表名}-${edge.下游表名}`">
            <td>{{ edge.上游表名 }}</td>
            <td>{{ edge.下游表名 }}</td>
            <td><div v-for="path in edge.字段路径" :key="path">{{ path }}</div></td>
            <td>{{ (edge.来源批次 || []).join('、') }}</td>
          </tr>
        </tbody>
      </table>

      <h4>无环路径</h4>
      <ul class="plain-list">
        <li v-for="(path, index) in detail.无环路径" :key="index">{{ path.join(' → ') }}</li>
      </ul>

      <h4>断环边（环不重复展开）</h4>
      <ul class="plain-list">
        <li v-for="cut in detail.断环边" :key="`${cut.上游表名}-${cut.下游表名}`">
          {{ cut.上游表名 }} → {{ cut.下游表名 }}（{{ cut.原因 }}）
        </li>
      </ul>

      <h4>导出血缘清单</h4>
      <div class="filter-bar">
        <label class="filter-item">
          <span>每批导出节点数（留空则全部）</span>
          <input v-model="exportLimit" placeholder="如 2" />
        </label>
        <button class="btn" type="button" @click="exportResult">
          {{ detail.导出状态 === '已中断' ? '继续导出（从已确认节点继续）' : '导出血缘清单' }}
        </button>
      </div>
      <p v-if="exportMessage" class="page-desc">{{ exportMessage }}</p>
      <p class="page-desc">已确认节点：{{ (detail.已确认节点 || []).join('、') || '无' }}</p>

      <h4>上传复核包</h4>
      <textarea
        v-model="reviewText"
        class="review-box"
        rows="4"
        placeholder='{"操作批次":"20261115-18","来源版本":"1","核对模块":"复核包","核对结果":"通过"}'
      ></textarea>
      <div>
        <button class="btn" type="button" @click="importReview">上传复核包</button>
      </div>

      <template v-if="detail.核对记录?.length">
        <h4>核对记录</h4>
        <ul class="plain-list">
          <li v-for="(record, index) in detail.核对记录" :key="index">
            [{{ record.核对时间 }}] {{ record.来源模块 }} · 来源版本 v{{ record.来源版本 }} · {{ record.核对结果 }}
          </li>
        </ul>
      </template>

      <template v-if="detail.同步记录?.length">
        <h4>同步记录</h4>
        <ul class="plain-list">
          <li v-for="(record, index) in detail.同步记录" :key="index">
            [{{ record.同步时间 }}] {{ record.来源模块 }} 同步 v{{ record.版本 }} 至血缘影响工作台
          </li>
        </ul>
      </template>

      <template v-if="detail.兼容目标?.length">
        <h4>兼容目标</h4>
        <ul class="plain-list">
          <li v-for="(record, index) in detail.兼容目标" :key="index">
            {{ record.模块 }} 声明兼容 v{{ record.兼容版本 }}（{{ record.声明时间 }}）
          </li>
        </ul>
      </template>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 份影响传播结果</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/impact'
const columns = ["结果编号", "操作批次", "版本", "节点数", "边数", "环数量", "导出状态", "结果状态"]
const actions = ["发布结果", "确认核对", "归档结果"]

const route = useRoute()
const rows = ref<Row[]>([])
const total = ref(0)
const detail = ref<Row | null>(null)
const batchInput = ref('')
const exportLimit = ref('')
const exportMessage = ref('')
const reviewText = ref('')
const errorMessage = ref('')

function countByStatus(status: string) {
  return rows.value.filter((row) => row['结果状态'] === status).length
}

function resetFilters() {
  batchInput.value = ''
  detail.value = null
  void reload()
}

function viewResult(row: Row) {
  batchInput.value = String(row['操作批次'])
  void viewBatch(batchInput.value)
}

async function viewBatch(batch: string, silent = false) {
  try {
    const response = await request(`${ENDPOINT}/result/${encodeURIComponent(batch)}`)
    if (!response.ok) {
      detail.value = null
      if (!silent) {
        errorMessage.value = `操作批次 ${batch} 尚未发布影响传播结果`
      }
      return
    }
    detail.value = await response.json()
  } catch (error) {
    if (!silent) {
      errorMessage.value = error instanceof Error ? error.message : '影响传播结果读取失败'
    }
  }
}

async function buildResult() {
  errorMessage.value = ''
  const batch = batchInput.value.trim()
  if (!batch) {
    errorMessage.value = '请先填写操作批次'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/build`, {
      method: 'POST',
      body: JSON.stringify({ values: { 操作批次: batch } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message ?? '影响传播结果生成失败'
      return
    }
    exportMessage.value = payload.message ?? ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响传播结果生成失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message ?? '血缘影响工作台动作未生效'
      return
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '血缘影响工作台操作失败'
  }
}

async function exportResult() {
  if (!detail.value) {
    return
  }
  errorMessage.value = ''
  const values: Record<string, number> = {}
  if (exportLimit.value.trim()) {
    values['节点数上限'] = Number(exportLimit.value)
  }
  try {
    const response = await request(
      `${ENDPOINT}/result/${encodeURIComponent(String(detail.value['操作批次']))}/export`,
      { method: 'POST', body: JSON.stringify({ values }) },
    )
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      errorMessage.value = payload.detail ?? payload.message ?? '血缘清单导出失败'
      return
    }
    const manifest = payload.manifest
    exportMessage.value = `${payload.message}；本次导出 ${manifest['本次导出节点'].length} 个节点，待确认：${manifest['待确认节点'].join('、') || '无'}`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '血缘清单导出失败'
  }
}

function downloadManifest() {
  if (!detail.value) {
    return
  }
  window.open(`${ENDPOINT}/result/${encodeURIComponent(String(detail.value['操作批次']))}/manifest`, '_blank')
}

async function importReview() {
  errorMessage.value = ''
  let values: Record<string, unknown>
  try {
    values = JSON.parse(reviewText.value) as Record<string, unknown>
  } catch {
    errorMessage.value = '复核包不是合法 JSON，请检查后重试'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/import`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message ?? '复核包导入失败'
      await reload()
      return
    }
    exportMessage.value = payload.message ?? ''
    reviewText.value = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核包导入失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = batchInput.value.trim()
    ? `?keyword=${encodeURIComponent(batchInput.value.trim())}`
    : ''
  try {
    const response = await request(`${ENDPOINT}${query}`)
    if (!response.ok) {
      throw new Error('影响传播结果列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (batchInput.value.trim()) {
      await viewBatch(batchInput.value.trim(), true)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响传播结果列表读取失败'
  }
}

onMounted(() => {
  const batch = String(route.query.batch ?? '')
  if (batch) {
    batchInput.value = batch
  }
  void reload()
})
</script>

<style scoped>
.detail-panel {
  margin-top: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
}
.detail-panel h4 {
  margin: 16px 0 8px;
}
.plain-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
}
.review-box {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
  font-family: monospace;
  margin-bottom: 8px;
}
</style>
