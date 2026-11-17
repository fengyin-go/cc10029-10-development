<template>
  <section class="page" data-module="batchscope">
    <header class="page-head">
      <div>
        <h2>归属受控批次数据集</h2>
        <p class="page-desc">
          按爬虫任务授权归属汇总采集批次，越权与无归属批次不得进入团队结果；
          数据集与目标站点执行快照同事务生成，权限由爬虫引擎统一控制。
        </p>
      </div>
    </header>

    <!-- 团队身份切换：归属看爬虫引擎，只读账户不能改动 -->
    <div class="identity-bar" data-testid="identity-bar">
      <label><span>当前团队</span>
        <select v-model="teamId" data-testid="team-select" @change="onTeamChange">
          <option v-for="team in teams" :key="team.team_id" :value="team.team_id">
            {{ team.team_name }}（{{ team.team_id }}）
          </option>
        </select>
      </label>
      <label><span>账户角色</span>
        <select v-model="role" data-testid="role-select" @change="reloadAll">
          <option value="write">读写账户</option>
          <option value="readonly">只读账户</option>
        </select>
      </label>
      <span class="identity-badge" :class="role" data-testid="identity-badge">
        {{ teamName }} · {{ role === 'write' ? '读写' : '只读' }}
      </span>
      <button class="btn" type="button" data-testid="reload-all" @click="reloadAll">刷新数据</button>
    </div>

    <div v-if="errorMessage" class="error-banner" data-testid="error-banner">{{ errorMessage }}</div>
    <div v-if="noticeMessage" class="notice-banner" data-testid="notice-banner">{{ noticeMessage }}</div>

    <nav class="tab-bar" data-testid="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        :data-testid="`tab-${tab.key}`"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- 任务归属与只读交接 -->
    <div v-show="activeTab === 'ownership'" class="tab-panel" data-testid="panel-ownership">
      <h3>任务归属与启动权限</h3>
      <table class="data-table">
        <thead>
          <tr><th>任务编号</th><th>任务名称</th><th>归属状态</th><th>归属团队</th><th>写权限团队</th><th>授权站点</th><th>可启动新批次</th><th>交接操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in ownership" :key="row.id" :data-testid="`own-row-${row.id}`">
            <td>{{ row.task_code }}</td>
            <td>{{ row.task_name }}</td>
            <td>{{ row.status }}</td>
            <td>{{ row.owner_team_id ?? '—（无归属授权）' }}</td>
            <td>{{ row.write_team_id ?? '—' }}</td>
            <td>{{ (row.site_codes || []).join('、') || '—' }}</td>
            <td>
              <span :data-testid="`launch-${row.id}`">{{ launchMap[row.id]?.can_launch_new_batch ? '允许' : '禁止' }}</span>
              <p class="cell-hint">{{ launchMap[row.id]?.reason }}</p>
            </td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`handover-open-${row.id}`" @click="openHandover(row)">发起交接</button>
            </td>
          </tr>
        </tbody>
      </table>

      <h3 style="margin-top:18px">只读交接记录</h3>
      <table class="data-table">
        <thead>
          <tr><th>交接单号</th><th>任务</th><th>原团队</th><th>接收团队</th><th>状态</th><th>原因</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="h in handovers" :key="h.id" :data-testid="`handover-row-${h.id}`">
            <td>{{ h.handover_no }}</td>
            <td>{{ h.task_code }}</td>
            <td>{{ h.from_team_id }}</td>
            <td>{{ h.to_team_id }}</td>
            <td :data-testid="`handover-status-${h.id}`">{{ h.status }}</td>
            <td>{{ h.reason || '—' }}</td>
            <td class="row-actions">
              <template v-if="h.status === '待交接'">
                <button class="link" type="button" :data-testid="`handover-complete-${h.id}`" @click="completeHandover(h.id)">完成并裁决冲突</button>
                <button class="link" type="button" :data-testid="`handover-cancel-${h.id}`" @click="cancelHandover(h.id)">撤销</button>
              </template>
              <span v-else>已留痕</span>
            </td>
          </tr>
          <tr v-if="!handovers.length"><td colspan="7" class="empty-state">当前团队暂无交接记录</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 批次运行 -->
    <div v-show="activeTab === 'runs'" class="tab-panel" data-testid="panel-runs">
      <h3>当前团队可见的采集批次（无归属/越权批次已由爬虫引擎剔除）</h3>
      <table class="data-table">
        <thead>
          <tr><th>批次号</th><th>任务</th><th>归属团队</th><th>站点</th><th>状态</th><th>记录数</th><th>冲突裁决</th><th>启动时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="run in runs" :key="run.id" :data-testid="`run-row-${run.id}`">
            <td>{{ run.run_no }}</td>
            <td>{{ run.task_code }}</td>
            <td>{{ run.owner_team_id ?? '无归属（已排除）' }}</td>
            <td>{{ run.site_code }}</td>
            <td :data-testid="`run-status-${run.id}`">{{ run.status }}</td>
            <td>{{ run.records }}</td>
            <td>
              <template v-if="run.conflict_resolved">
                已裁决 → {{ run.superseded_by || '权威批次' }}
              </template>
              <template v-else>—</template>
            </td>
            <td>{{ run.started_at }}</td>
            <td class="row-actions">
              <button v-if="run.status === '运行中'" class="link" type="button" :data-testid="`run-finish-${run.id}`" @click="finishRun(run.id)">收口为完整批次</button>
            </td>
          </tr>
          <tr v-if="!runs.length"><td colspan="9" class="empty-state">当前团队没有可见批次</td></tr>
        </tbody>
      </table>

      <h3 style="margin-top:18px">启动新批次</h3>
      <div class="filter-bar">
        <label><span>爬虫任务</span>
          <select v-model="newRunTaskId" data-testid="new-run-task">
            <option v-for="row in ownership" :key="row.id" :value="row.id">{{ row.task_code }}（{{ row.status }}）</option>
          </select>
        </label>
        <button class="btn primary" type="button" data-testid="start-run" @click="startRun">启动批次</button>
        <span class="cell-hint">交接冻结期、只读账户、无归属任务都会被引擎拒绝。</span>
      </div>
    </div>

    <!-- 归属受控数据集 -->
    <div v-show="activeTab === 'datasets'" class="tab-panel" data-testid="panel-datasets">
      <h3>生成归属受控爬虫批次数据集（与站点快照同事务）</h3>
      <div class="filter-bar">
        <label><span>爬虫任务</span>
          <select v-model="datasetTaskId" data-testid="dataset-task">
            <option v-for="row in ownership" :key="row.id" :value="row.id">{{ row.task_code }}</option>
          </select>
        </label>
        <label><span>幂等键</span><input v-model="idempotencyKey" data-testid="idempotency-key" placeholder="重复提交只记一次" /></label>
        <button class="btn primary" type="button" data-testid="create-dataset" @click="createDataset()">生成数据集</button>
        <button class="btn" type="button" data-testid="create-dataset-fail" @click="createDatasetFailing()">模拟快照失败（验证回滚）</button>
      </div>

      <table class="data-table" style="margin-top:12px">
        <thead>
          <tr><th>数据集编号</th><th>任务</th><th>版本</th><th>状态</th><th>批次清单</th><th>批次数</th><th>快照编号</th><th>回执编号</th><th>生成时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="ds in datasets" :key="ds.id" :data-testid="`ds-row-${ds.id}`" :data-status="ds.status">
            <td>{{ ds.dataset_no }}</td>
            <td>{{ ds.task_code }}</td>
            <td>v{{ ds.version }}</td>
            <td :data-testid="`ds-status-${ds.id}`">{{ ds.status }}</td>
            <td>{{ (ds.run_nos || []).join('、') }}</td>
            <td>{{ ds.run_count }}</td>
            <td>{{ ds.snapshot_no }}</td>
            <td>{{ ds.receipt_no }}</td>
            <td>{{ ds.created_at }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`ds-activate-${ds.id}`" @click="activateDataset(ds.id)">切换为生效版本</button>
              <button class="link" type="button" :data-testid="`ds-detail-${ds.id}`" @click="showDatasetDetail(ds.id)">排除明细</button>
            </td>
          </tr>
          <tr v-if="!datasets.length"><td colspan="10" class="empty-state">当前团队尚未生成归属受控数据集</td></tr>
        </tbody>
      </table>

      <div v-if="datasetDetail" class="detail-box" data-testid="ds-detail-box">
        <h4>{{ datasetDetail.dataset_no }} 被排除的批次（越权/无归属/未完成/冲突替代）</h4>
        <ul>
          <li v-for="ex in datasetDetail.excluded_runs" :key="ex.run_no" :data-testid="`excluded-${ex.run_no}`">
            <strong>{{ ex.run_no }}</strong>：{{ ex.reasons.join('；') }}
          </li>
          <li v-if="!datasetDetail.excluded_runs.length">无排除批次</li>
        </ul>
        <p class="cell-hint">输入字段：{{ datasetDetail.input_fields.join('、') }}</p>
        <p class="cell-hint">输出字段：{{ datasetDetail.output_fields.join('、') }}</p>
      </div>
    </div>

    <!-- 目标站点执行快照 -->
    <div v-show="activeTab === 'snapshots'" class="tab-panel" data-testid="panel-snapshots">
      <h3>目标站点执行快照清单（跨业务面采集目标，只含本团队授权站点）</h3>
      <table class="data-table">
        <thead>
          <tr><th>快照编号</th><th>任务</th><th>授权站点</th><th>站点数</th><th>批次清单</th><th>来源</th><th>边界说明</th><th>生成时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="snap in snapshots" :key="snap.id" :data-testid="`snap-row-${snap.id}`">
            <td>{{ snap.snapshot_no }}</td>
            <td>{{ snap.task_code }}</td>
            <td>{{ (snap.site_codes || []).join('、') }}</td>
            <td>{{ snap.site_count }}</td>
            <td>{{ (snap.run_nos || []).join('、') }}</td>
            <td>{{ snap.source }}</td>
            <td>{{ snap.boundary_note }}</td>
            <td>{{ snap.created_at }}</td>
          </tr>
          <tr v-if="!snapshots.length"><td colspan="8" class="empty-state">当前团队暂无站点快照</td></tr>
        </tbody>
      </table>
      <p class="cell-hint" style="margin-top:8px">
        同一清单也可通过采集目标模块的 <code>/api/targetsite/execution-snapshot</code> 按当前团队身份获取。
      </p>
    </div>

    <!-- 团队审计 -->
    <div v-show="activeTab === 'audit'" class="tab-panel" data-testid="panel-audit">
      <h3>按团队核对任务归属、批次与只读交接记录</h3>
      <div v-if="audit" class="stat-row">
        <article class="stat-card"><span class="stat-label">归属任务</span><strong class="stat-value">{{ audit.ownership_records.length }}</strong></article>
        <article class="stat-card"><span class="stat-label">交接记录</span><strong class="stat-value">{{ audit.handover_records.length }}</strong></article>
        <article class="stat-card"><span class="stat-label">可见批次</span><strong class="stat-value">{{ audit.run_records.length }}</strong></article>
        <article class="stat-card"><span class="stat-label">数据集</span><strong class="stat-value">{{ audit.dataset_records.length }}</strong></article>
        <article class="stat-card"><span class="stat-label">站点快照</span><strong class="stat-value">{{ audit.snapshot_records.length }}</strong></article>
      </div>
      <table class="data-table" style="margin-top:12px">
        <thead>
          <tr><th>任务</th><th>归属状态</th><th>写权限团队</th><th>启动权限复核</th><th>批次数</th><th>交接次数</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in audit?.task_audit ?? []" :key="item.crawler_task_id" :data-testid="`audit-row-${item.crawler_task_id}`">
            <td>{{ item.task_code }}</td>
            <td>{{ item.ownership_status }}</td>
            <td>{{ item.write_team_id ?? '—' }}</td>
            <td :data-testid="`audit-launch-${item.crawler_task_id}`">
              {{ item.launch_permission.can_launch_new_batch ? '允许启动' : '禁止启动' }}
              <p class="cell-hint">{{ item.launch_permission.reason }}</p>
            </td>
            <td>{{ item.runs.length }}</td>
            <td>{{ item.handovers.length }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 发起交接弹窗 -->
    <div v-if="handoverTarget" class="modal-backdrop" data-testid="handover-modal">
      <form class="modal" @submit.prevent="submitHandover">
        <h3>发起归属交接：{{ handoverTarget.task_code }}</h3>
        <label class="create-field"><span>接收团队</span>
          <select v-model="handoverToTeam" data-testid="handover-to-team">
            <option v-for="team in teams.filter(t => t.team_id !== teamId)" :key="team.team_id" :value="team.team_id">
              {{ team.team_name }}（{{ team.team_id }}）
            </option>
          </select>
        </label>
        <label class="create-field"><span>交接原因</span><input v-model="handoverReason" data-testid="handover-reason" /></label>
        <div class="page-actions">
          <button class="btn" type="button" @click="handoverTarget = null">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-handover">确认发起</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const ENDPOINT = '/api/batchscope'
const tabs = [
  { key: 'ownership', label: '任务归属与交接' },
  { key: 'runs', label: '批次运行' },
  { key: 'datasets', label: '归属受控数据集' },
  { key: 'snapshots', label: '站点执行快照' },
  { key: 'audit', label: '团队审计核对' },
]

const activeTab = ref('ownership')
const teams = ref<any[]>([])
const ownership = ref<any[]>([])
const handovers = ref<any[]>([])
const runs = ref<any[]>([])
const datasets = ref<any[]>([])
const snapshots = ref<any[]>([])
const audit = ref<any>(null)
const launchMap = ref<Record<number, any>>({})
const errorMessage = ref('')
const noticeMessage = ref('')

const teamId = computed({ get: () => session.teamId, set: (v: string) => { session.teamId = v } })
const role = computed({ get: () => session.role, set: (v: 'write' | 'readonly') => { session.setRole(v) } })
const teamName = computed(() => session.teamName)

const newRunTaskId = ref<number | null>(null)
const datasetTaskId = ref<number | null>(null)
const idempotencyKey = ref('')
const datasetDetail = ref<any>(null)
const handoverTarget = ref<any>(null)
const handoverToTeam = ref('')
const handoverReason = ref('')

function headers() {
  return session.identityHeaders
}

async function callApi(path: string, init?: RequestInit): Promise<any> {
  const response = await request(path, { ...init, headers: headers() })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload.detail || `请求失败（${response.status}）`)
  }
  return payload
}

function fail(message: string) {
  errorMessage.value = message
}

async function loadTeams() {
  const payload = await request(`${ENDPOINT}/teams`).then(r => r.json())
  teams.value = payload.items || []
  const current = teams.value.find(t => t.team_id === session.teamId)
  if (current) session.setTeam(current.team_id, current.team_name)
}

function onTeamChange() {
  const current = teams.value.find(t => t.team_id === teamId.value)
  if (current) session.setTeam(current.team_id, current.team_name)
  void reloadAll()
}

async function reloadAll() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const [own, hand, run, ds, snap, aud] = await Promise.all([
      callApi(`${ENDPOINT}/ownership`),
      callApi(`${ENDPOINT}/handovers`),
      callApi(`${ENDPOINT}/runs`),
      callApi(`${ENDPOINT}/datasets`),
      callApi(`${ENDPOINT}/snapshots`),
      callApi(`${ENDPOINT}/audit`),
    ])
    ownership.value = own.items || []
    handovers.value = hand.items || []
    runs.value = run.items || []
    datasets.value = ds.items || []
    snapshots.value = snap.items || []
    audit.value = aud
    if (newRunTaskId.value === null && ownership.value.length) newRunTaskId.value = ownership.value[0].id
    if (datasetTaskId.value === null && ownership.value.length) datasetTaskId.value = ownership.value[0].id
    const map: Record<number, any> = {}
    await Promise.all(ownership.value.map(async (row: any) => {
      map[row.id] = await callApi(`${ENDPOINT}/tasks/${row.id}/launch-permission`)
    }))
    launchMap.value = map
  } catch (error) {
    fail(error instanceof Error ? error.message : '数据加载失败')
  }
}

async function startRun() {
  if (!newRunTaskId.value) return
  try {
    await callApi(`${ENDPOINT}/tasks/${newRunTaskId.value}/runs`, { method: 'POST' })
    await reloadAll()
  } catch (error) { fail(error instanceof Error ? error.message : '启动失败') }
}

async function finishRun(runId: number) {
  try {
    await callApi(`${ENDPOINT}/runs/${runId}/finish`, {
      method: 'POST', body: JSON.stringify({ records: 100, failed: false }),
    })
    await reloadAll()
  } catch (error) { fail(error instanceof Error ? error.message : '收口失败') }
}

async function createDataset(forceFailure = false) {
  if (!datasetTaskId.value || !idempotencyKey.value.trim()) {
    fail('请选择任务并填写幂等键')
    return
  }
  try {
    const payload = await callApi(`${ENDPOINT}/datasets`, {
      method: 'POST',
      body: JSON.stringify({
        crawler_task_id: datasetTaskId.value,
        idempotency_key: idempotencyKey.value.trim(),
        force_snapshot_failure: forceFailure,
      }),
    })
    const receipt = payload.entry?.deduplicated ? payload.entry.receipt_no : null
    await reloadAll()
    if (receipt) {
      // 刷新会清空提示，因此在刷新成功后再展示幂等回执。
      noticeMessage.value = `幂等命中：第二次提交只记一次，按成功回执 ${receipt} 返回同一条数据集`
    }
  } catch (error) { fail(error instanceof Error ? error.message : '数据集生成失败') }
}

function createDatasetFailing() {
  void createDataset(true)
}

async function activateDataset(id: number) {
  try {
    await callApi(`${ENDPOINT}/datasets/${id}/activate`, { method: 'POST' })
    await reloadAll()
  } catch (error) { fail(error instanceof Error ? error.message : '版本切换失败') }
}

async function showDatasetDetail(id: number) {
  try {
    datasetDetail.value = await callApi(`${ENDPOINT}/datasets/${id}`)
  } catch (error) { fail(error instanceof Error ? error.message : '明细读取失败') }
}

function openHandover(row: any) {
  handoverTarget.value = row
  handoverToTeam.value = teams.value.find(t => t.team_id !== teamId.value)?.team_id || ''
  handoverReason.value = ''
}

async function submitHandover() {
  if (!handoverTarget.value) return
  try {
    await callApi(`${ENDPOINT}/tasks/${handoverTarget.value.id}/handovers`, {
      method: 'POST',
      body: JSON.stringify({ to_team_id: handoverToTeam.value, reason: handoverReason.value }),
    })
    handoverTarget.value = null
    await reloadAll()
  } catch (error) { fail(error instanceof Error ? error.message : '交接发起失败') }
}

async function completeHandover(id: number) {
  try {
    await callApi(`${ENDPOINT}/handovers/${id}/complete`, { method: 'POST' })
    await reloadAll()
  } catch (error) { fail(error instanceof Error ? error.message : '交接裁决失败') }
}

async function cancelHandover(id: number) {
  try {
    await callApi(`${ENDPOINT}/handovers/${id}/cancel`, { method: 'POST' })
    await reloadAll()
  } catch (error) { fail(error instanceof Error ? error.message : '撤销失败') }
}

onMounted(async () => {
  await loadTeams()
  await reloadAll()
})
</script>

<style scoped>
.identity-bar { display: flex; gap: 14px; align-items: flex-end; background: #fff; border: 1px solid var(--border);
  border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; flex-wrap: wrap; }
.identity-bar label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.identity-bar select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; min-width: 180px; }
.identity-badge { padding: 6px 12px; border-radius: 999px; font-size: 12px; }
.identity-badge.write { background: #dcfce7; color: #166534; }
.identity-badge.readonly { background: #fef3c7; color: #92400e; }
.tab-bar { display: flex; gap: 6px; border-bottom: 1px solid var(--border); margin-bottom: 14px; flex-wrap: wrap; }
.tab-btn { border: none; background: none; padding: 8px 14px; cursor: pointer; font-size: 13px; color: var(--muted);
  border-bottom: 2px solid transparent; }
.tab-btn.active { color: var(--brand); border-bottom-color: var(--brand); font-weight: 600; }
.tab-panel h3 { font-size: 15px; margin: 6px 0 10px; }
.cell-hint { margin: 2px 0 0; font-size: 11px; color: var(--muted); }
.error-banner { background: #fef2f2; border: 1px solid #fecaca; color: #b42318; border-radius: 8px;
  padding: 8px 12px; font-size: 13px; margin-bottom: 12px; }
.notice-banner { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; border-radius: 8px;
  padding: 8px 12px; font-size: 13px; margin-bottom: 12px; }
.detail-box { margin-top: 14px; background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; }
.detail-box ul { margin: 8px 0; padding-left: 18px; font-size: 13px; }
</style>
