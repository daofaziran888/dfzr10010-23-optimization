<template>
  <section class="page" data-module="rollback">
    <header class="page-head">
      <div>
        <h2>数据回滚恢复管理</h2>
        <p class="page-desc">维护数据回滚的版本追踪、影响评估与恢复计时，为数据回滚恢复提供可审计的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记回滚记录</button>
        <button class="btn" type="button" @click="exportRows">导出数据回滚恢复清单</button>
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

    <section class="impact-panel">
      <h3>血缘影响联动</h3>
      <p class="page-desc">先声明血缘影响工作台中影响传播结果的兼容目标，再引用已有数据；失败时只重试未完成项，并沿用上一稳定口径。</p>
      <div class="filter-bar">
        <label class="filter-item">
          <span>操作批次</span>
          <input v-model="impactBatch" placeholder="如 20261115-18" />
        </label>
        <label class="filter-item">
          <span>兼容版本</span>
          <input v-model="compatVersion" placeholder="如 1" />
        </label>
        <button class="btn" type="button" @click="declareCompat">声明兼容目标</button>
        <button class="btn" type="button" @click="attachImpact">接入影响结果</button>
      </div>
      <p v-if="impactMessage" class="page-desc">{{ impactMessage }}</p>
    </section>

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
          <td :colspan="columns.length + 1" class="empty-state">暂无数据回滚恢复数据，可先登记回滚记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据回滚恢复记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/rollback'
const columns = ["记录编号", "表名称", "操作批次", "回滚版本", "回滚原因", "影响范围", "影响传播结果版本", "回滚状态"]
const actions = ["执行回滚", "确认恢复", "归档记录"]
const statuses = ["待回滚", "回滚中", "已回滚", "已恢复"]
const stats = [{"label": "回滚记录总数", "value": 0}, {"label": "回滚中记录", "value": 0}, {"label": "已回滚记录", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const impactBatch = ref('')
const compatVersion = ref('')
const impactMessage = ref('')

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '回滚记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '数据回滚恢复动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据回滚恢复操作失败'
  }
}

async function declareCompat() {
  errorMessage.value = ''
  impactMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/impact/declare`, {
      method: 'POST',
      body: JSON.stringify({ values: { 操作批次: impactBatch.value.trim(), 兼容版本: compatVersion.value.trim() } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message ?? '兼容目标声明失败')
    }
    impactMessage.value = payload.message ?? ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '兼容目标声明失败'
  }
}

async function attachImpact() {
  errorMessage.value = ''
  impactMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/impact/attach`, {
      method: 'POST',
      body: JSON.stringify({ values: { 操作批次: impactBatch.value.trim() } }),
    })
    const payload = await response.json()
    impactMessage.value = payload.message ?? ''
    if (!payload.ok) {
      errorMessage.value = payload.message ?? '接入影响结果失败'
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接入影响结果失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('回滚记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据回滚恢复列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.impact-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 12px;
}
.impact-panel h3 {
  margin: 0 0 4px;
  font-size: 14px;
}
</style>
