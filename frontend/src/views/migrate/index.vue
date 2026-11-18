<template>
  <section class="page" data-module="migrate">
    <header class="page-head">
      <div>
        <h2>数据迁移管理管理</h2>
        <p class="page-desc">维护数据迁移的源目标配置、迁移方式与校验规则，为数据迁移管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记迁移任务</button>
        <button class="btn" type="button" @click="exportRows">导出数据迁移管理清单</button>
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
      <h3>血缘影响核对</h3>
      <p class="page-desc">读取血缘影响工作台的影响传播结果，核对迁移映射是否被采纳，并把核对结果同步到血缘影响工作台。</p>
      <div class="filter-bar">
        <label class="filter-item">
          <span>操作批次</span>
          <input v-model="impactBatch" placeholder="如 20261115-18" />
        </label>
        <button class="btn" type="button" @click="reviewImpact">核对影响结果</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无数据迁移管理数据，可先登记迁移任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据迁移管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/migrate'
const columns = ["任务编号", "源表名", "目标表名", "迁移方式", "数据范围", "校验规则", "操作批次", "任务状态"]
const actions = ["执行迁移", "回滚迁移", "归档任务"]
const statuses = ["待执行", "迁移中", "已完成", "已回滚"]
const stats = [{"label": "迁移任务总数", "value": 0}, {"label": "迁移中任务", "value": 0}, {"label": "已完成任务", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const impactBatch = ref('')
const impactMessage = ref('')

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '迁移任务登记入口尚未接入审批流'
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
      throw new Error(payload.message ?? '数据迁移管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据迁移管理操作失败'
  }
}

async function reviewImpact() {
  errorMessage.value = ''
  impactMessage.value = ''
  const batch = impactBatch.value.trim()
  if (!batch) {
    errorMessage.value = '请先填写操作批次'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/impact-review/${encodeURIComponent(batch)}`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message ?? '影响结果核对失败')
    }
    impactMessage.value = payload.message ?? ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响结果核对失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('迁移任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据迁移管理列表读取失败'
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
