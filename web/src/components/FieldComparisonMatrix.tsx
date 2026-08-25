import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { ClassifiedDoc, Job } from '../types'
import {
  buildFieldComparison,
  countUnverifiedMismatches,
  resolveDocForCell,
  verifiedFieldKeys,
  type CompareColumn,
  type DraftFieldOverlay,
  type RequiredCompareRow,
} from '../lib/fieldComparison'

type Props = {
  job: Job
  chainFileNames?: string[] | null
  onSelectCell: (doc: ClassifiedDoc, fieldKey: string) => void
  onJob?: (j: Job) => void
  /** 未保存草稿，保存前即时刷新对照表 */
  draftOverlay?: DraftFieldOverlay | null
  refreshKey?: number
  requiredRows?: RequiredCompareRow[] | null
}

function rowVerifyKey(jobId: string, chainId: string, fieldKey: string): string {
  return `gospd.rowVerified.${jobId}.${chainId || 'job'}.${fieldKey}`
}

function loadLegacyVerified(jobId: string, chainId: string, keys: string[]): Set<string> {
  const s = new Set<string>()
  try {
    for (const k of keys) {
      if (localStorage.getItem(rowVerifyKey(jobId, chainId, k)) === '1') s.add(k)
    }
  } catch {
    /* ignore */
  }
  return s
}

export function FieldComparisonMatrix({
  job,
  chainFileNames,
  onSelectCell,
  onJob,
  draftOverlay,
  refreshKey = 0,
  requiredRows,
}: Props) {
  const overlaySig = JSON.stringify(draftOverlay || {})
  const reqSig = JSON.stringify(requiredRows || [])
  const jobRev = job.updated_at || job.fields_confirm_sig || job.job_id
  const { columns, rows, timing } = useMemo(
    () => buildFieldComparison(job, chainFileNames, draftOverlay, requiredRows),
    [job, jobRev, chainFileNames, overlaySig, refreshKey, reqSig],
  )
  const chainId = job.active_chain_id || 'job'
  const serverVerified = useMemo(
    () => verifiedFieldKeys(job, chainId),
    [job.field_row_verifications, chainId],
  )
  const [verified, setVerified] = useState<Set<string>>(() => new Set())
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    const merged = new Set(serverVerified)
    for (const k of loadLegacyVerified(
      job.job_id,
      chainId,
      rows.map((r) => r.fieldKey),
    )) {
      if (!merged.has(k)) merged.add(k)
    }
    setVerified(merged)
  }, [job.job_id, chainId, serverVerified, rows.map((r) => r.fieldKey).join('|')])

  const toggleVerify = async (fieldKey: string, on: boolean) => {
    setBusyKey(fieldKey)
    setErr('')
    const prev = new Set(verified)
    setVerified((s) => {
      const next = new Set(s)
      if (on) next.add(fieldKey)
      else next.delete(fieldKey)
      return next
    })
    try {
      const next = await api.verifyFieldRow(job.job_id, {
        chain_id: chainId,
        field_key: fieldKey,
        verified: on,
      })
      onJob?.(next)
      setVerified(verifiedFieldKeys(next, chainId))
      try {
        localStorage.setItem(rowVerifyKey(job.job_id, chainId, fieldKey), on ? '1' : '0')
      } catch {
        /* ignore */
      }
    } catch (e) {
      setVerified(prev)
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyKey(null)
    }
  }

  const onCell = (col: CompareColumn, fieldKey: string) => {
    const doc = resolveDocForCell(columns, col.id)
    if (doc) onSelectCell(doc, fieldKey)
  }

  const mismatchCount = rows.filter((r) => !r.match && !verified.has(r.fieldKey)).length

  if (!rows.length) {
    return <p className="preview-empty">当前笔无可对照字段</p>
  }

  return (
    <div className="compare-matrix">
      <div className="compare-legend hint">
        全部已提取字段在本表横向展开。绿勾=本应一致的字段在适用单据间取值一致（≠已在原件定位成功）；
        各单据自身编号、日期和专有信息仅展示，不做跨单据相等判断。关联订单号参与串联核对。
        点单元格→右侧高亮；账列来自工作台上传的抽样清单。
        <span className={`badge ${timing.status === 'PASS' ? '' : 'warn'} ml-8`} data-tip={timing.summary}>
          时序 {timing.status === 'PASS' ? '通过' : timing.status === 'REVIEW' ? '待复核' : '异常'}
        </span>
        {mismatchCount > 0 && (
          <span className="badge warn ml-8">待核 {mismatchCount} 行</span>
        )}
      </div>
      {err && <p className="err">{err}</p>}
      <div className="compare-scroll">
        <table className="compare-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>抽样清单</th>
              {columns.map((c) => (
                <th key={c.id}>
                  {c.label}
                  {!c.doc ? ' (缺)' : ''}
                </th>
              ))}
              <th>验证通过</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const ok = row.match || verified.has(row.fieldKey)
              const twBad =
                row.threeWayStatus &&
                /fail|未通过|warn/i.test(String(row.threeWayStatus))
              return (
                <tr key={row.fieldKey} className={ok && !twBad ? 'row-ok' : 'row-warn'}>
                  <td className="compare-label">
                    <span
                      className={row.pickReason || row.quantityRolesHint ? 'cmp-slot-tip' : undefined}
                      data-tip={
                        [row.pickReason, row.quantityRolesHint ? `本笔：${row.quantityRolesHint}` : '']
                          .filter(Boolean)
                          .join(' ') || undefined
                      }
                    >
                      {row.label}
                    </span>
                    {twBad ? <span className="badge warn ml-4">三单</span> : null}
                    {row.manualReviewOnly ? <span className="badge ml-4">人工核对</span> : null}
                    {row.quantityRolesHint ? (
                      <div className="hint cmp-qty-roles">{row.quantityRolesHint}</div>
                    ) : null}
                  </td>
                  <td className="compare-cell muted">{row.ledger || '—'}</td>
                  {columns.map((col) => {
                    const val = row.cells[col.id] || ''
                    const empty = !val
                    const isDraft = row.cellDraft?.[col.id]
                    return (
                      <td key={col.id} className="compare-cell">
                        <button
                          type="button"
                          className={`compare-cell-btn${empty ? ' empty' : ''}${isDraft ? ' draft-pending' : ''}`}
                          disabled={!col.doc}
                          data-tip={
                            col.doc
                              ? isDraft
                                ? `未保存草稿 · 点此在原件上定位：${col.label}`
                                : `点此在原件上定位该字段：${col.label}`
                              : '本笔没有这份单据'
                          }
                          onClick={() => onCell(col, row.fieldKey)}
                        >
                          {empty ? '—' : val}
                          {isDraft ? <span className="badge warn ml-4">未存</span> : null}
                          {!empty && row.match && !row.manualReviewOnly && !isDraft && <span className="cmp-ok"> ✓</span>}
                        </button>
                      </td>
                    )
                  })}
                  <td className="compare-verify">
                    <input
                      type="checkbox"
                      checked={verified.has(row.fieldKey)}
                      disabled={busyKey === row.fieldKey}
                      onChange={(e) => void toggleVerify(row.fieldKey, e.target.checked)}
                      data-tip="勾选表示本行已人工核对，会写入核对记录。"
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function countMatrixMismatch(
  job: Job,
  chainFileNames?: string[] | null,
  draftOverlay?: DraftFieldOverlay | null,
): number {
  return countUnverifiedMismatches(job, chainFileNames, draftOverlay)
}
