import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { Job, SampleScopeException } from '../types'
import type { ChainInfo } from '../api'
import { LedgerMappingPanel } from '../components/LedgerMappingPanel'
import { BusinessUploadQueue } from '../components/BusinessUploadQueue'
import { SampleScopeExceptionDialog } from '../components/SampleScopeExceptionDialog'
import { LedgerMatchStatus } from '../components/LedgerMatchStatus'
import { BusinessIndexEvidence } from '../components/BusinessIndexEvidence'
import { invalidateChainsCache } from '../lib/chainsCache'
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
  initialTab?: TabId
  onProcess?: (force?: boolean, fileNames?: string[]) => Promise<void>
  onGo?: (step: string) => void
}

export function UploadPage({ job, onJob, ocrBusy = false, ocrMsg = '', initialTab = 'upload', onProcess, onGo }: Props) {
  const [ocr, setOcr] = useState<{ configured: boolean; message?: string } | null>(null)
  const [busy, setBusy] = useState<'idle' | 'upload'>('idle')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [tab, setTab] = useState<TabId>(initialTab)
  const [rows, setRows] = useState<ChainInfo[]>([])
  const [uploadingId, setUploadingId] = useState<string | null>(null)
  const [completeSetBusyId, setCompleteSetBusyId] = useState<string | null>(null)
  const [uploadErrorById, setUploadErrorById] = useState<Record<string, string>>({})
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [scopeDialogOpen, setScopeDialogOpen] = useState(false)
  const [mixedPacketMode, setMixedPacketMode] = useState(false)
  const [deletingScopeId, setDeletingScopeId] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const mixedInputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)
  const seenScopeExceptions = useRef<{ jobId: string; ids: Set<string> }>({ jobId: '', ids: new Set() })

  const pending = job.pending_files || []
  const classified = job.classified || []
  const scopeExceptions = useMemo(() => job.scope_exceptions || [], [job.scope_exceptions])
  const hasPending = pending.length > 0
  const processablePending = pending.filter((item) => item.mixed_packet_declared !== true)
  const uncertainPending = pending.filter(
    (item) => item.mixed_packet_declared !== true && (item.type_uncertain === true || item.light_confident === false),
  )
  const declaredPacketPending = pending.filter(
    (item) => !item.from_packet && item.mixed_packet_declared === true,
  )
  const hasProcessablePending = processablePending.length > 0
  const hasRun = Boolean(job.ocr_has_run)
  const requiresPacketReview = packetNeedsReview(job)
  const locked = busy !== 'idle' || ocrBusy
  const prog = job.ocr_progress
  const progPct =
    prog && prog.total > 0 ? Math.min(100, Math.round((prog.done / prog.total) * 100)) : null

  useEffect(() => {
    api.ocrStatus().then(setOcr).catch(() => setOcr({ configured: false, message: '状态未知' }))
  }, [])

  useEffect(() => {
    api.listChains(job.job_id).then((result) => setRows(result.chains || [])).catch(() => setRows([]))
  }, [job.job_id, job.updated_at, pending.length, classified.length])

  useEffect(() => setTab(initialTab), [initialTab, job.job_id])

  useEffect(() => {
    if (ocrMsg) setMsg(ocrMsg)
  }, [ocrMsg])

  useEffect(() => {
    if (seenScopeExceptions.current.jobId !== job.job_id) {
      seenScopeExceptions.current = { jobId: job.job_id, ids: new Set() }
    }
    const currentIds = new Set(scopeExceptions.map((exception) => exception.exception_id))
    for (const seenId of seenScopeExceptions.current.ids) {
      if (!currentIds.has(seenId)) seenScopeExceptions.current.ids.delete(seenId)
    }
    const unseen = scopeExceptions.filter(
      (exception) => !seenScopeExceptions.current.ids.has(exception.exception_id),
    )
    if (!unseen.length) return
    unseen.forEach((exception) => seenScopeExceptions.current.ids.add(exception.exception_id))
    setScopeDialogOpen(true)
  }, [job.job_id, scopeExceptions])

  useEffect(() => {
    if (initialTab !== 'upload') return
  }, [initialTab, job.job_id])

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
      const next = await api.upload(job.job_id, docs, { process: false, mixedPacket: false })
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
    if (!force && !hasProcessablePending && !classified.length) {
      setErr('请先拖入或选择凭证')
      return
    }
    if (force && selectedFiles.length === 0) {
      setErr('请先勾选需要重新识别的文件')
      return
    }
    setErr('')
    setMsg('')
    try {
      if (onProcess) {
        await onProcess(
          force,
          force ? selectedFiles : processablePending.map((item) => item.file_name),
        )
      } else {
        let next = job
        if (force || hasProcessablePending || classified.length) {
          next = await api.process(job.job_id, {
            force,
            fileNames: force
              ? selectedFiles
              : processablePending.map((item) => item.file_name),
          })
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

  const uploadForBusiness = async (row: ChainInfo | null, files: File[]) => {
    if (!files.length || locked || uploadingId) return
    const targetId = row?.chain_id || '__mixed_packet__'
    setUploadingId(targetId)
    setUploadErrorById((current) => ({ ...current, [targetId]: '' }))
    try {
      const businessHints = row
        ? Object.fromEntries(files.map((file) => [file.name, [row.chain_id]]))
        : undefined
      const next = await api.upload(job.job_id, files, {
        process: false,
        businessHints,
        mixedPacket: row === null,
      })
      onJob(next)
      setMsg(row ? `已为业务 ${row.chain_id} 上传 ${files.length} 个文件。` : `已上传混装资料包 ${files.length} 个文件。`)
      if (packetNeedsReview(next)) onGo?.('packet_unpack')
      else setTab('pending')
    } catch (error) {
      setUploadErrorById((current) => ({ ...current, [targetId]: error instanceof Error ? error.message : String(error) }))
    } finally {
      setUploadingId(null)
    }
  }

  const changeCompleteSet = async (row: ChainInfo, next: boolean) => {
    if (locked || completeSetBusyId) return
    const previous = Boolean(row.complete_set)
    setCompleteSetBusyId(row.chain_id)
    setUploadErrorById((current) => ({ ...current, [row.chain_id]: '' }))
    setRows((current) => current.map((item) => (
      item.chain_id === row.chain_id ? { ...item, complete_set: next } : item
    )))
    try {
      const updated = await api.setChainCompleteSet(job.job_id, row.chain_id, next)
      invalidateChainsCache(job.job_id)
      onJob(updated)
      setMsg(next ? `业务 ${row.chain_id} 已标记为本笔齐套。` : `业务 ${row.chain_id} 已取消齐套标记。`)
    } catch (error) {
      setRows((current) => current.map((item) => (
        item.chain_id === row.chain_id ? { ...item, complete_set: previous } : item
      )))
      setUploadErrorById((current) => ({
        ...current,
        [row.chain_id]: error instanceof Error ? error.message : String(error),
      }))
    } finally {
      setCompleteSetBusyId(null)
    }
  }

  const toggleSelected = (fileName: string) => {
    setSelectedFiles((current) => current.includes(fileName) ? current.filter((name) => name !== fileName) : [...current, fileName])
  }

  const deleteScopeException = async (exception: SampleScopeException) => {
    if (deletingScopeId) return
    setDeletingScopeId(exception.exception_id)
    setErr('')
    try {
      const next = await api.deleteScopeException(job.job_id, exception.exception_id)
      onJob(next)
      setMsg(`已删除非抽样清单材料：${exception.file_name}`)
      if (!(next.scope_exceptions || []).length) setScopeDialogOpen(false)
    } catch (error) {
      setErr(error instanceof Error ? error.message : String(error))
    } finally {
      setDeletingScopeId(null)
    }
  }

  const pendingTable = (
    <table className="data-table mb-14">
      <thead>
        <tr>
          <th>选择</th>
          <th>文件</th>
          <th>类型</th>
          <th>大小</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        {pending.map((p) => (
          <tr key={p.file_name}>
            <td><input type="checkbox" aria-label={`选择 ${p.file_name}`} checked={selectedFiles.includes(p.file_name)} onChange={() => toggleSelected(p.file_name)} /></td>
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
          <th>选择</th>
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
            <td><input type="checkbox" aria-label={`选择 ${d.file_name}`} checked={selectedFiles.includes(d.file_name)} onChange={() => toggleSelected(d.file_name)} /></td>
            <td>
              <div>{d.file_name}</div>
              <BusinessIndexEvidence document={d} />
            </td>
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
              ) : (
                <LedgerMatchStatus document={d} />
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
          <div className="hint">只收 PDF/图片。普通多页单据可直接识别；一个文件含多笔业务时请人工开启混装资料包。</div>
        </div>
        <div className="toolbar">
          <label className="packet-mode-toggle">
            <input
              type="checkbox"
              checked={mixedPacketMode}
              disabled={locked || uploadingId !== null}
              onChange={(event) => setMixedPacketMode(event.target.checked)}
            />
            <span>存在混装资料包</span>
          </label>
          {mixedPacketMode && (
            <>
              <input ref={mixedInputRef} type="file" multiple hidden accept={ACCEPT} aria-label="选择混装资料包" onChange={(event) => { const files = Array.from(event.target.files || []); event.target.value = ''; void uploadForBusiness(null, files) }} />
              <button className="btn" disabled={locked || uploadingId !== null} onClick={() => mixedInputRef.current?.click()}>
                {uploadingId === '__mixed_packet__' ? '上传中…' : '上传混装资料包'}
              </button>
            </>
          )}
          {(!requiresPacketReview || hasProcessablePending) ? (
            <span
              className="tip-anchor"
              data-tip="按底稿目标抽字段并识别；可切到其它页等待。"
            >
              <button
                className="btn primary"
                disabled={locked || (hasProcessablePending ? false : hasRun ? selectedFiles.length === 0 : true)}
                onClick={() => void startProcess(!hasProcessablePending && hasRun)}
              >
                {ocrBusy
                  ? '处理中…'
                  : hasProcessablePending
                    ? `开始处理（${processablePending.length}）`
                    : hasRun
                      ? '重新识别'
                      : '开始处理'}
              </button>
            </span>
          ) : null}
        </div>
      </div>

      <div className="panel-body upload-body">
        {scopeExceptions.length > 0 && (
          <section className="scope-exception-panel" aria-labelledby="scope-exception-heading">
            <div>
              <p className="scope-exception-kicker">需审计师处理</p>
              <h4 id="scope-exception-heading">异常区（{scopeExceptions.length}）</h4>
              <p>
                以下材料未进入抽样业务。系统不会用它们新增业务，也不会带入后续审阅或底稿。
              </p>
            </div>
            <ul>
              {scopeExceptions.map((exception) => (
                <li key={exception.exception_id}>
                  <strong>{exception.file_name}</strong>
                  <span>
                    {exception.detected_business_ids?.length
                      ? `识别业务号：${exception.detected_business_ids.join('、')}`
                      : '未识别到可归属的抽样业务号'}
                  </span>
                </li>
              ))}
            </ul>
            <button type="button" className="btn danger" onClick={() => setScopeDialogOpen(true)}>
              查看并处理异常
            </button>
          </section>
        )}
        {requiresPacketReview && (
          <div className="packet-upload-banner">
            <span>
              {hasProcessablePending
                ? `另有 ${declaredPacketPending.length} 个人工标记的混装资料包待拆包，不影响其余 ${processablePending.length} 份普通凭证先识别。`
                : `有 ${declaredPacketPending.length} 个人工标记的混装资料包，识别前须先拆包分笔。`}
            </span>
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
        {uncertainPending.length > 0 && (
          <div className="type-uncertain-banner" role="status">
            <strong>文件类型不确定，疑似内部存在杂乱的文件类型</strong>
            <span>识别后请到“核对字段”逐页查看，并为当前文件确认一个固定名称。</span>
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
            <BusinessUploadQueue
              rows={rows}
              busy={locked}
              uploadingId={uploadingId}
              completeSetBusyId={completeSetBusyId}
              uploadErrorById={uploadErrorById}
              onOpen={(row) => void api.setActiveChain(job.job_id, row.chain_id).then(onJob).catch(() => undefined)}
              onUpload={uploadForBusiness}
              onCompleteSetChange={changeCompleteSet}
            />
          </section>
        )}

        {tab === 'pending' && (
          <section className="upload-section">
            {!hasPending ? (
              <p className="preview-empty">暂无待处理文件。请先在「上传」页加入单据。</p>
            ) : (
              <>
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
      {scopeDialogOpen && (
        <SampleScopeExceptionDialog
          exceptions={scopeExceptions}
          deletingId={deletingScopeId}
          onDelete={deleteScopeException}
          onDismiss={() => setScopeDialogOpen(false)}
        />
      )}
    </div>
  )
}
