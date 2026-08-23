import { useEffect, useMemo, useState } from 'react'
import { api, type ChainInfo, type DeskLights, type MesMatchRule } from '../api'
import { DESK_LIGHT_LEGEND_TIP } from '../lib/deskLights'
import { BusinessWarehouseRow } from './BusinessWarehouseRow'

type Props = {
  rows: ChainInfo[]
  lights?: DeskLights | null
  activeId?: string | null
  busy?: boolean
  onOpen: (row: ChainInfo) => void
  onUpload: (row: ChainInfo, files: File[]) => void | Promise<void>
  uploadingId?: string | null
  uploadErrorById?: Record<string, string>
}

const SUMMARY_COLLAPSE_KEY = 'gospd.deskSummaryCollapsed'

function lightClass(light?: string): string {
  if (light === 'green') return 'is-green'
  if (light === 'red') return 'is-red'
  if (light === 'yellow') return 'is-yellow'
  return 'is-wait'
}

/** 越小越靠上：待处理红/黄在前，已通过与已确认沉底 */
function actionRank(row: ChainInfo): number {
  if (row.reason === 'fail_closed' || row.light === 'green' || row.reason === 'ok') return 40
  if (row.light === 'red' || row.reason === 'missing_docs' || row.reason === 'fields_gap') return 0
  if (row.reason === 'amount_ambiguity' || row.reason === 'test_fail') return 0
  if (row.light === 'yellow' || row.reason === 'docs_uncertain') return 10
  if (row.reason === 'wait_docs') return 15
  if (row.reason === 'tests_pending' || row.light === 'wait') return 25
  return 30
}

function isSettled(row: ChainInfo): boolean {
  return actionRank(row) >= 40
}

function summarizeLights(rows: ChainInfo[], lights?: DeskLights | null): DeskLights {
  if (lights && typeof lights.green === 'number') {
    return {
      green: lights.green,
      yellow: lights.yellow || 0,
      red: lights.red || 0,
      wait: lights.wait || 0,
      issues: lights.issues || [],
      request_docs: lights.request_docs || [],
      progress: lights.progress,
      legend: lights.legend,
    }
  }
  const counts = { green: 0, yellow: 0, red: 0, wait: 0 }
  const issues: string[] = []
  const request_docs: string[] = []
  for (const row of rows) {
    const light = row.light || 'wait'
    if (light === 'green') counts.green += 1
    else if (light === 'yellow') counts.yellow += 1
    else if (light === 'red') counts.red += 1
    else counts.wait += 1
    if (light === 'red' || light === 'yellow') {
      issues.push(`${row.chain_id}：${row.label || row.reason || light}`)
    }
    for (const line of row.request_docs || []) {
      if (!request_docs.includes(line)) request_docs.push(line)
    }
    for (const miss of row.missing_doc_types || row.missing_doc_labels || []) {
      const line = `${row.chain_id}：请提供「${miss}」`
      if (!request_docs.includes(line)) request_docs.push(line)
    }
  }
  return { ...counts, issues, request_docs }
}

/** 后端未回 doc_slots 时，用已识别/缺单据拼矩阵，保证汇总可见 */
function synthesizeSlots(row: ChainInfo): Array<{ id: string; label: string; status: string }> {
  if (row.doc_slots?.length) return row.doc_slots
  const present = new Set(row.present_labels || [])
  const missing = new Set(row.missing_doc_labels || [])
  const uncertain = new Set(row.uncertain_doc_labels || [])
  const labels = [...present, ...missing, ...uncertain]
  if (!labels.length) return []
  return labels.map((label) => {
    let status = 'present'
    if (missing.has(label)) status = 'missing'
    else if (uncertain.has(label)) status = 'uncertain'
    return { id: label, label, status }
  })
}

function slotMark(status?: string): string {
  if (status === 'present') return '✓'
  if (status === 'uncertain') return '?'
  if (status === 'missing') return '×'
  return '—'
}

function rowTip(row: ChainInfo): string {
  if (row.reason === 'wait_docs') return '还没有对应凭证。点进去去上传。'
  if (row.reason === 'amount_ambiguity') {
    const gap = (row.missing_labels || []).join('、')
    return gap ? `同页多金额未决；还缺：${gap}。点进去核对。` : '同页多金额未决，必须进人工核对。'
  }
  if (row.reason === 'missing_docs') {
    return `缺必需单据：${(row.missing_doc_labels || []).join('、') || '发票等'}。先补凭证，不能当字段已齐。`
  }
  if (row.reason === 'docs_uncertain') {
    return `单据类型存疑：${(row.uncertain_doc_labels || []).join('、') || '需人工看'}。点进核对页确认。`
  }
  if (row.reason === 'fields_gap') {
    return `缺字段：${(row.missing_labels || row.missing_fields || []).join('、') || '必填字段'}。点进去补。`
  }
  if (row.reason === 'test_fail') {
    const d = (row.diff_lines || [])[0]
    return d ? `测试未通过：${d}` : '测试未通过，点进去看对不上的数据和测试逻辑。'
  }
  if (row.reason === 'fail_closed') return '测试未通过，已人工确认。点开可再看原因。'
  if (row.reason === 'tests_pending') return '字段已齐，测试在自动跑。'
  return '本笔已通过，点开看结果。'
}

function readCollapsedPref(fallback: boolean): boolean {
  try {
    const raw = sessionStorage.getItem(SUMMARY_COLLAPSE_KEY)
    if (raw === '1') return true
    if (raw === '0') return false
  } catch {
    /* ignore */
  }
  return fallback
}

function writeCollapsedPref(collapsed: boolean) {
  try {
    sessionStorage.setItem(SUMMARY_COLLAPSE_KEY, collapsed ? '1' : '0')
  } catch {
    /* ignore */
  }
}

export function SampleDeskList({
  rows,
  lights,
  activeId,
  busy,
  onOpen,
  onUpload,
  uploadingId,
  uploadErrorById = {},
}: Props) {
  const [rules, setRules] = useState<MesMatchRule[]>([])
  const [copied, setCopied] = useState(false)
  const [showPassed, setShowPassed] = useState(false)
  const pendingCount = useMemo(
    () => rows.filter((r) => !isSettled(r)).length,
    [rows],
  )
  const [summaryCollapsed, setSummaryCollapsed] = useState(() =>
    readCollapsedPref(pendingCount > 0),
  )

  useEffect(() => {
    let cancelled = false
    void api
      .mesMatchRules()
      .then((r) => {
        if (!cancelled) setRules(r.rules || [])
      })
      .catch(() => {
        if (!cancelled) setRules([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  const summary = useMemo(() => summarizeLights(rows, lights), [rows, lights])

  const sortedRows = useMemo(() => {
    return [...rows].sort((a, b) => {
      const d = actionRank(a) - actionRank(b)
      if (d !== 0) return d
      return String(a.chain_id).localeCompare(String(b.chain_id), 'zh')
    })
  }, [rows])

  const { pendingRows, settledRows } = useMemo(() => {
    const pending: ChainInfo[] = []
    const settled: ChainInfo[] = []
    for (const row of sortedRows) {
      if (isSettled(row)) settled.push(row)
      else pending.push(row)
    }
    return { pendingRows: pending, settledRows: settled }
  }, [sortedRows])

  const visibleRows = useMemo(
    () => (showPassed ? [...pendingRows, ...settledRows] : pendingRows),
    [pendingRows, settledRows, showPassed],
  )

  const rowsWithSlots = useMemo(
    () =>
      visibleRows.map((row) => ({
        ...row,
        doc_slots: synthesizeSlots(row),
      })),
    [visibleRows],
  )

  const slotHeaders = useMemo(() => {
    const seen = new Map<string, string>()
    for (const row of rowsWithSlots) {
      for (const s of row.doc_slots || []) {
        if (!seen.has(s.id)) seen.set(s.id, s.label)
      }
    }
    return [...seen.entries()].map(([id, label]) => ({ id, label }))
  }, [rowsWithSlots])

  const requestText = useMemo(() => (summary.request_docs || []).join('\n'), [summary])

  const setCollapsed = (next: boolean) => {
    setSummaryCollapsed(next)
    writeCollapsedPref(next)
  }

  const copyRequest = async () => {
    if (!requestText) return
    try {
      await navigator.clipboard.writeText(requestText)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      /* ignore */
    }
  }

  if (!rows.length) {
    return <p className="preview-empty">还没有样本笔。请先在本页上传抽样清单。</p>
  }

  return (
    <div className="desk-sample-wrap">
      <div className={`desk-summary-panel${summaryCollapsed ? ' is-collapsed' : ''}`}>
        <div className="desk-light-bar" aria-label="样本灯汇总" data-tip={DESK_LIGHT_LEGEND_TIP}>
          <strong className="desk-light-title">样本汇总</strong>
          <span className="desk-light-chip is-green" title={DESK_LIGHT_LEGEND_TIP}>
            绿 {summary.green}
          </span>
          <span className="desk-light-chip is-yellow" title={DESK_LIGHT_LEGEND_TIP}>
            黄 {summary.yellow}
          </span>
          <span className="desk-light-chip is-red" title={DESK_LIGHT_LEGEND_TIP}>
            红 {summary.red}
          </span>
          <span className="desk-light-chip is-wait" title={DESK_LIGHT_LEGEND_TIP}>
            待办 {summary.wait}
          </span>
          <span className="desk-light-legend hint" title={DESK_LIGHT_LEGEND_TIP}>
            绿可继续 · 黄人裁 · 红须处理
          </span>
          {requestText ? (
            <button
              type="button"
              className="btn compact"
              onClick={() => void copyRequest()}
              data-tip="复制缺件索要清单，发给客户补资料。"
            >
              {copied ? '已复制' : '复制缺件索要'}
            </button>
          ) : null}
          <button
            type="button"
            className="btn compact desk-summary-toggle"
            onClick={() => setCollapsed(!summaryCollapsed)}
            data-tip={
              summaryCollapsed
                ? '展开完整性矩阵与规则说明。'
                : '收起汇总矩阵，腾出空间处理下方红灯。'
            }
          >
            {summaryCollapsed ? '展开矩阵' : '收起汇总'}
          </button>
        </div>

        {!summaryCollapsed && slotHeaders.length > 0 && (
          <div className="desk-slot-matrix" role="table" aria-label="样本单据完整性">
            <div className="desk-slot-head" role="row">
              <span role="columnheader">样本</span>
              {slotHeaders.map((h) => (
                <span key={h.id} role="columnheader">
                  {h.label}
                </span>
              ))}
              <span role="columnheader">状态</span>
            </div>
            {rowsWithSlots.map((row) => {
              const byId = new Map((row.doc_slots || []).map((s) => [s.id, s]))
              return (
                <button
                  key={`mx-${row.chain_id}`}
                  type="button"
                  className={`desk-slot-row${row.chain_id === activeId ? ' is-on' : ''}`}
                  disabled={busy}
                  onClick={() => onOpen(row)}
                  data-tip={rowTip(row)}
                >
                  <span className="desk-slot-id">{row.chain_id}</span>
                  {slotHeaders.map((h) => {
                    const st = byId.get(h.id)?.status
                    return (
                      <span
                        key={h.id}
                        className={`desk-slot-cell is-${st || 'empty'}`}
                        title={st || ''}
                      >
                        {slotMark(st)}
                      </span>
                    )
                  })}
                  <span className={`desk-slot-light ${lightClass(row.light)}`}>{row.label}</span>
                </button>
              )
            })}
          </div>
        )}

        {!summaryCollapsed && rules.length > 0 && (
          <details className="desk-rules">
            <summary>三单/截止规则（字段对字段）</summary>
            <p className="hint desk-rules-intro">
              匹配=写清「哪张单的哪个字段」与「哪张单的哪个字段」比较；日期先后进截止，不进三单放行。
            </p>
            <ul>
              {rules.map((r) => (
                <li key={r.rule_id}>
                  <strong>{r.label}</strong>
                  {r.left && r.right ? (
                    <div className="desk-rules-vs">
                      <span>{r.left}</span>
                      <em>vs</em>
                      <span>{r.right}</span>
                    </div>
                  ) : null}
                  {r.formula ? <div className="hint">{r.formula}</div> : null}
                  {r.fail_example ? <div className="hint">失败例：{r.fail_example}</div> : null}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {pendingRows.length > 0 && (
        <div className="desk-sample-section">
          <div className="desk-sample-section-title">
            待处理 <span className="hint">{pendingRows.length} 笔</span>
          </div>
          <ul className="desk-sample-list" aria-label="待处理样本">
            {pendingRows.map((row) => (
              <BusinessWarehouseRow
                key={row.chain_id}
                row={row}
                active={row.chain_id === activeId}
                busy={busy}
                uploading={uploadingId === row.chain_id}
                uploadError={uploadErrorById[row.chain_id] || ''}
                onOpen={onOpen}
                onUpload={onUpload}
              />
            ))}
          </ul>
        </div>
      )}

      {settledRows.length > 0 && (
        <button
          type="button"
          className="btn compact desk-passed-toggle"
          aria-expanded={showPassed}
          onClick={() => setShowPassed((value) => !value)}
        >
          {showPassed ? '收起已通过' : `查看已通过 ${settledRows.length} 笔`}
        </button>
      )}

      {showPassed && settledRows.length > 0 && (
        <div className="desk-sample-section is-settled">
          <div className="desk-sample-section-title">
            已通过 / 已确认 <span className="hint">{settledRows.length} 笔</span>
          </div>
          <ul className="desk-sample-list" aria-label="已通过样本">
            {settledRows.map((row) => (
              <BusinessWarehouseRow
                key={row.chain_id}
                row={row}
                active={row.chain_id === activeId}
                busy={busy}
                uploading={uploadingId === row.chain_id}
                uploadError={uploadErrorById[row.chain_id] || ''}
                onOpen={onOpen}
                onUpload={onUpload}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
