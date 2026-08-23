import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Job } from '../types'
import { LedgerMappingPanel } from '../components/LedgerMappingPanel'
import { ChainPicker } from '../components/ChainPicker'
import { PendingChainPreview } from '../components/PendingChainPreview'
import { packetNeedsReview } from '../lib/workflowGuide'

const TYPE_LABELS: Record<string, string> = {
  contract: '合同',
  order: '订单',
  delivery: '发货单',
  receipt: '签收/验收',
  invoice: '发票',
  payment: '回款',
  other: '其他',
}

const ACCEPT =
  '.pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,application/pdf,image/*'

type TabId = 'upload' | 'pending' | 'done'

type Props = {
  job: Job
  onJob: (j: Job) => void
  ocrBusy?: boolean
  ocrMsg?: string
  onProcess?: (force?: boolean) => Promise<void>
  onGo?: (step: string) => void
}

export function UploadPage({ job, onJob, ocrBusy = false, ocrMsg = '', onProcess, onGo }: Props) {
  const [ocr, setOcr] = useState<{ configured: boolean; message?: string } | null>(null)
  const [busy, setBusy] = useState<'idle' | 'upload'>('idle')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [tab, setTab] = useState<TabId>('upload')
  const inputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)

  const pending = job.pending_files || []
  const classified = job.classified || []
  const hasPending = pending.length > 0
  const requiresPacketReview = packetNeedsReview(job)
  const locked = busy !== 'idle' || ocrBusy
  const prog = job.ocr_progress
  const progPct =
    prog && prog.total > 0 ? Math.min(100, Math.round((prog.done / prog.total) * 100)) : null

  useEffect(() => {
    api.ocrStatus().then(setOcr).catch(() => setOcr({ configured: false, message: '状态未知' }))
  }, [])

  useEffect(() => {
    if (ocrMsg) setMsg(ocrMsg)
  }, [ocrMsg])

  useEffect(() => {
    if (hasPending) setTab('pending')
    else if (classified.length && !hasPending) setTab('done')
  }, [hasPending, classified.length, job.job_id])

  const onFiles = async (list: FileList | File[] | null) => {
    if (!list?.length || locked) return
    setBusy('upload')
    setErr('')
    setMsg('')
    try {
      const files = Array.from(list)
      const docs = files.filter((f) => !/\.(xlsx|xls|csv)$/i.test(f.name))
      const ledgers = files.filter((f) => /\.(xlsx|xls|csv)$/i.test(f.name))
      if (ledgers.length && !docs.length) {
        setErr('抽样清单请在工作台上传，这里只收凭证。')
        return
      }
      if (!docs.length) {
        setErr('请拖入 PDF 或图片凭证')
        return
      }
      const next = await api.upload(job.job_id, docs, { process: false })
      onJob(next)
      setMsg(`已加入待处理 ${docs.length} 个单据（已轻量分类）。请核对类型后点「开始处理」`)
      setTab('pending')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('idle')
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const startProcess = async (force = false) => {
    if (locked) return
    if (!force && !hasPending && !classified.length) {
      setErr('请先拖入或选择凭证')
      return
    }
    if (force && !classified.length && !hasPending) {
      setErr('没有可重识别的单据')
      return
    }
    setErr('')
    setMsg('')
    try {
      if (onProcess) {
        await onProcess(force)
      } else {
        let next = job
        if (force || hasPending || classified.length) {
          next = await api.process(job.job_id, { force })
        }
        onJob(next)
      }
      setTab('done')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const changeType = async (fileName: string, docType: string) => {
    if (locked) return
    setErr('')
    try {
      onJob(await api.reclassify(job.job_id, fileName, docType))
      setMsg(`已改类型并重抽：${fileName}`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const changePendingType = async (fileName: string, docType: string) => {
    if (locked) return
    setErr('')
    try {
      onJob(await api.patchPendingType(job.job_id, fileName, docType))
      setMsg(`已改待处理类型：${fileName}`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const seed = async () => {
    if (locked) return
    setBusy('upload')
    setErr('')
    try {
      onJob(await api.seedDemo(job.job_id))
      setMsg(
        '已载入演示单据（非正式 OCR：ocr_source=demo_seed，勿当真 OCR 验收；正式请上传后点开始处理）',
      )
      setTab('done')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('idle')
    }
  }

  const pendingTable = (
    <table className="data-table mb-14">
      <thead>
        <tr>
          <th>文件</th>
          <th>类型</th>
          <th>大小</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        {pending.map((p) => (
          <tr key={p.file_name}>
            <td>{p.file_name}</td>
            <td>
              <select
                className="field-select"
                style={{ marginBottom: 0, minHeight: 32, padding: '0.2rem 0.35rem' }}
                value={p.doc_type || 'other'}
                disabled={locked}
                onChange={(e) => void changePendingType(p.file_name, e.target.value)}
              >
                {Object.entries(TYPE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </td>
            <td>{p.size != null ? `${Math.max(1, Math.round(p.size / 1024))} KB` : '-'}</td>
            <td>
              <span className="badge pending">待 OCR</span>
              {p.doc_type_source === 'manual' ? (
                <span className="badge ok ml-8">人工类型</span>
              ) : p.light_confident === false ? (
                <span className="badge warn ml-8">类型待核</span>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )

  const classifiedTable = (
    <table className="data-table">
      <thead>
        <tr>
          <th>文件</th>
          <th>类型</th>
          <th>来源</th>
          <th>关键字段</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        {classified.map((d) => (
          <tr key={d.file_name}>
            <td>{d.file_name}</td>
            <td>
              <select
                className="field-select"
                style={{ marginBottom: 0, minHeight: 32, padding: '0.2rem 0.35rem' }}
                value={d.doc_type}
                disabled={locked}
                onChange={(e) => void changeType(d.file_name, e.target.value)}
              >
                {Object.entries(TYPE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </td>
            <td>
              <code>{d.ocr_source || '-'}</code>
            </td>
            <td className="hint">
              {Object.entries(d.fields || {})
                .filter(([k, v]) => !k.startsWith('_') && v != null && String(v))
                .slice(0, 4)
                .map(([k, v]) => `${k}=${v}`)
                .join('；') || '-'}
            </td>
            <td>
              {d.error ? (
                <span className="badge danger">{d.error}</span>
              ) : d.ledger_match_ok ? (
                <span className="badge ok">
                  账齐 {d.ledger_posting_date || ''}
                  {d.ledger_match_manual ? '（人工）' : ''}
                </span>
              ) : d.ledger_evaluated ? (
                <span className="badge warn">{d.ledger_match_message || '账未匹配'}</span>
              ) : (
                <span className="badge ok">已识别</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )

  return (
    <div className="panel panel-fill upload-page">
      <div className="panel-head">
        <div>
          <h3>上传凭证</h3>
          <div className="hint">只收 PDF/图片。抽样清单在工作台上传。混装须先拆包，再开始识别。</div>
        </div>
        <div className="toolbar">
          <span
            className="tip-anchor"
            data-tip="非正式 OCR，只用来联调演示，不要当正式识别结果验收。"
          >
            <button
              className="btn"
              disabled={locked}
              onClick={seed}
            >
              演示数据（非正式 OCR）
            </button>
          </span>
          <span
            className="tip-anchor"
            data-tip="丢弃旧识别结果，对已上传单据重新跑 OCR。"
          >
            <button
              className="btn"
              disabled={locked || !classified.length}
              onClick={() => void startProcess(true)}
            >
              强制重识别
            </button>
          </span>
          {!requiresPacketReview ? (
            <span
              className="tip-anchor"
              data-tip="按底稿目标抽字段并识别；可切到其它页等待。"
            >
              <button
                className="btn primary"
                disabled={locked || (!hasPending && !classified.length)}
                onClick={() => void startProcess(false)}
              >
                {ocrBusy ? '处理中…' : `开始处理${hasPending ? `（${pending.length}）` : ''}`}
              </button>
            </span>
          ) : null}
        </div>
      </div>

      <div className="panel-body upload-body">
        {requiresPacketReview && (
          <div className="packet-upload-banner">
            <span>待处理里有混装凭证包，识别前须先拆包分笔。</span>
            <span
              className="tip-anchor"
              data-tip="混装扫描件要先切开、归到业务笔，确认后才能识别。"
            >
              <button
                type="button"
                className="btn compact primary"
                disabled={locked}
                onClick={() => onGo?.('packet_unpack')}
              >
                去拆包分笔
              </button>
            </span>
          </div>
        )}
        {ocrBusy && (
          <div className="ocr-progress-bar" role="status">
            <div className="ocr-progress-track">
              <div
                className="ocr-progress-fill"
                style={{ width: progPct != null ? `${progPct}%` : '30%' }}
              />
            </div>
            <span className="hint">
              {job.ocr_processing_message || ocrMsg || '识别进行中…'}
              {progPct != null ? ` · ${progPct}%` : ''}
            </span>
          </div>
        )}

        <div className="upload-tabs" role="tablist">
          {(
            [
              ['upload', '上传'],
              ['pending', `待处理${hasPending ? ` (${pending.length})` : ''}`],
              ['done', `已识别${classified.length ? ` (${classified.length})` : ''}`],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              className={`upload-tab${tab === id ? ' on' : ''}`}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>

        {msg && <p className="ok-text">{msg}</p>}
        {err && <p className="err">{err}</p>}

        {tab === 'upload' && (
          <section className="upload-section">
            <div className={`status-pill mb-12${ocr?.configured ? ' ok' : ''}`}>
              {ocr?.configured ? '千帆 OCR 已配置' : 'OCR 未配置 / Mock'}
              {ocr?.message ? ` · ${ocr.message}` : ''}
              <span className="hint"> · PDF 有文字层时跳过远程 OCR（更快）</span>
            </div>
            <div
              className={`dropzone dropzone-compact${dragOver ? ' over' : ''}${locked ? ' busy' : ''}`}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  if (!locked) inputRef.current?.click()
                }
              }}
              onClick={() => {
                if (!locked) inputRef.current?.click()
              }}
              onDragEnter={(e) => {
                e.preventDefault()
                e.stopPropagation()
                dragDepth.current += 1
                setDragOver(true)
              }}
              onDragOver={(e) => {
                e.preventDefault()
                e.stopPropagation()
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
              }}
              onDragLeave={(e) => {
                e.preventDefault()
                e.stopPropagation()
                dragDepth.current -= 1
                if (dragDepth.current <= 0) {
                  dragDepth.current = 0
                  setDragOver(false)
                }
              }}
              onDrop={(e) => {
                e.preventDefault()
                e.stopPropagation()
                dragDepth.current = 0
                setDragOver(false)
                if (locked) return
                void onFiles(e.dataTransfer.files)
              }}
            >
              <input
                ref={inputRef}
                type="file"
                multiple
                hidden
                disabled={locked}
                accept={ACCEPT}
                onChange={(e) => void onFiles(e.target.files)}
                onClick={(e) => e.stopPropagation()}
              />
              <div className="dropzone-title">
                {busy === 'upload'
                  ? '正在接收文件…'
                  : dragOver
                    ? '松开即可加入队列'
                    : '拖入 PDF/图片凭证，或点击选择'}
              </div>
              <div className="dropzone-sub">
                多笔业务可一次拖入多个 SO 文件夹内的文件 · 上传后切到「待处理」核对类型
              </div>
            </div>
            <PendingChainPreview
              job={job}
              refreshKey={`${pending.length}-${classified.length}`}
            />
            <ChainPicker job={job} onJob={onJob} />
          </section>
        )}

        {tab === 'pending' && (
          <section className="upload-section">
            {!hasPending ? (
              <p className="preview-empty">暂无待处理文件。请先在「上传」页加入单据。</p>
            ) : (
              <>
                <PendingChainPreview
                  job={job}
                  refreshKey={`${pending.length}-${classified.length}`}
                />
                {pendingTable}
              </>
            )}
          </section>
        )}

        {tab === 'done' && (
          <section className="upload-section">
            {(job.ocr_issues || []).map((x, i) => (
              <p key={i} className="err">
                {String(x)}
              </p>
            ))}
            {!classified.length ? (
              <p className="preview-empty">尚未识别。完成待处理队列后点「开始处理」。</p>
            ) : (
              classifiedTable
            )}
            {job.ledger_path && !job.sample_population && (
              <LedgerMappingPanel job={job} onJob={onJob} />
            )}
          </section>
        )}
      </div>
    </div>
  )
}
