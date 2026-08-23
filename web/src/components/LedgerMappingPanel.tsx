import { useEffect, useMemo, useState } from 'react'
import { api, type LedgerOption } from '../api'
import type { Job } from '../types'

type Props = {
  job: Job
  onJob: (j: Job) => void
}

const NONE = '（不使用）'
const NO_PICK = '（不指定）'

function colOptions(columns: string[], allowNone: boolean) {
  return allowNone ? [NONE, ...columns] : columns
}

export function LedgerMappingPanel({ job, onJob }: Props) {
  const columns = useMemo(() => {
    if (job.ledger_columns?.length) return job.ledger_columns
    const row = job.ledger_rows?.[0]
    return row ? Object.keys(row) : []
  }, [job.ledger_columns, job.ledger_rows])

  const preview = useMemo(
    () => (job.ledger_rows || []).slice(0, 5) as Array<Record<string, unknown>>,
    [job.ledger_rows],
  )

  const suggested = job.ledger_mapping || {}
  const autoOk = !!job.ledger_auto_ok
  const [forceManual, setForceManual] = useState(!autoOk)
  const [posting, setPosting] = useState('')
  const [biz, setBiz] = useState(NONE)
  const [amount, setAmount] = useState(NONE)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [options, setOptions] = useState<LedgerOption[]>([])
  const [picks, setPicks] = useState<Record<string, string>>({})

  useEffect(() => {
    setForceManual(!autoOk)
    setPosting(suggested.posting_date || columns[0] || '')
    setBiz(suggested.biz_id || NONE)
    setAmount(suggested.amount || NONE)
    setErr('')
    setMsg('')
  }, [job.ledger_path, job.job_id, autoOk, columns, suggested.posting_date, suggested.biz_id, suggested.amount])

  useEffect(() => {
    if (!job.ledger_path || !job.ledger_mapping) {
      setOptions([])
      return
    }
    let cancelled = false
    api
      .ledgerOptions(job.job_id)
      .then((r) => {
        if (!cancelled) setOptions(r.options || [])
      })
      .catch(() => {
        if (!cancelled) setOptions([])
      })
    return () => {
      cancelled = true
    }
  }, [job.job_id, job.ledger_path, job.ledger_mapping, job.classified])

  if (!job.ledger_path && !job.ledger_rows?.length) return null

  const showEditors = forceManual || !autoOk
  const fileLabel = (job.ledger_path || '').split(/[/\\]/).pop() || '序时账'
  const unmatched = (job.classified || []).filter(
    (d) =>
      d.ledger_evaluated &&
      !d.ledger_match_ok &&
      (d.doc_type === 'invoice' || d.doc_type === 'order'),
  )
  const matchedBanner = (job.classified || []).find(
    (d) => d.doc_type === 'invoice' && d.ledger_match_ok && d.ledger_posting_date,
  )

  const apply = async () => {
    if (!posting) {
      setErr('请选择入账日期列')
      return
    }
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const mapping = {
        posting_date: posting,
        biz_id: biz === NONE ? null : biz,
        amount: amount === NONE ? null : amount,
      }
      const next = await api.applyLedger(job.job_id, mapping)
      onJob(next)
      setMsg(
        '列映射已保存' +
          (next.classified?.length ? '，并已套用到已识别单据' : '（开始处理后会自动套用）'),
      )
      setForceManual(false)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const applyManual = async (fileName: string, label: string) => {
    if (!label || label === NO_PICK) return
    setBusy(true)
    setErr('')
    try {
      onJob(await api.manualLedgerMatch(job.job_id, { file_name: fileName, label }))
      setMsg(`已为 ${fileName} 指定序时账行`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const std = job.ledger_standard_map || {}

  return (
    <div className="plan-box mt-12">
      <div className="toolbar between">
        <div>
          <strong>序时账</strong>
          <span className="hint"> · {fileLabel}</span>
          {matchedBanner ? (
            <div className="ok-text mt-8" style={{ display: 'inline-block' }}>
              序时账已匹配
              {matchedBanner.ledger_match_manual ? '（人工）' : ''}：业务编号{' '}
              <strong>{matchedBanner.ledger_matched_biz_id || '—'}</strong>，入账日期{' '}
              <strong>{matchedBanner.ledger_posting_date}</strong>
            </div>
          ) : autoOk && !forceManual ? (
            <div className="ok-text mt-8" style={{ display: 'inline-block' }}>
              列映射已自动识别：业务编号→{std['业务编号'] || suggested.biz_id || '—'}，入账日期→
              {std['入账日期'] || suggested.posting_date || '—'}，金额→
              {std['金额'] || suggested.amount || '—'}
            </div>
          ) : (
            <div className="hint mt-8">
              {autoOk ? '可手改映射后点「应用列映射」。' : '列没认全，请手选对应列后应用。'}
            </div>
          )}
          {job.classified?.some((d) => d.ledger_evaluated && !d.ledger_match_ok && d.doc_type === 'invoice') && (
            <div className="err mt-8" style={{ display: 'inline-block' }}>
              序时账未自动匹配发票，可在下方人工指定行。
            </div>
          )}
        </div>
        {autoOk && (
          <label className="hint" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox"
              checked={forceManual}
              onChange={(e) => setForceManual(e.target.checked)}
            />
            手动调整列映射
          </label>
        )}
      </div>

      {showEditors && (
        <>
          <div className="manual-grid mt-12">
            <label>
              入账日期列
              <select className="field-select" value={posting} onChange={(e) => setPosting(e.target.value)}>
                {colOptions(columns, false).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label>
              业务编号列
              <select className="field-select" value={biz} onChange={(e) => setBiz(e.target.value)}>
                {colOptions(columns, true).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label>
              金额列
              <select className="field-select" value={amount} onChange={(e) => setAmount(e.target.value)}>
                {colOptions(columns, true).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="toolbar mt-8">
            <button
              type="button"
              className="btn primary"
              disabled={busy || !columns.length}
              onClick={() => void apply()}
              data-tip="按你选的列，把序时账金额对上单据。"
            >
              {busy ? '应用中…' : '应用列映射'}
            </button>
          </div>
        </>
      )}

      {unmatched.length > 0 && options.length > 0 && (
        <div className="mt-12">
          <div className="section-title">序时账人工匹配</div>
          <p className="hint mb-12">自动匹配失败时，可手选序时账行补入账日。</p>
          {unmatched.map((d) => (
            <div key={d.file_name} className="toolbar mt-8" style={{ alignItems: 'center' }}>
              <span className="flex-1 mono-sm">{d.file_name}</span>
              <select
                className="field-select"
                style={{ maxWidth: 320, marginBottom: 0 }}
                value={picks[d.file_name] || NO_PICK}
                onChange={(e) => {
                  const v = e.target.value
                  setPicks((m) => ({ ...m, [d.file_name]: v }))
                  void applyManual(d.file_name, v)
                }}
                disabled={busy}
              >
                <option value={NO_PICK}>{NO_PICK}</option>
                {options.map((o) => (
                  <option key={o.label} value={o.label}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}

      {msg && <p className="ok-text mt-8">{msg}</p>}
      {err && <p className="err mt-8">{err}</p>}

      <div className="section-title mt-12">序时账前 5 行预览</div>
      {!preview.length ? (
        <p className="preview-empty">无预览行</p>
      ) : (
        <div style={{ overflow: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => (
                    <td key={c}>{row[c] == null || row[c] === '' ? '—' : String(row[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="hint mt-8">共 {(job.ledger_rows || []).length} 行</p>
    </div>
  )
}
