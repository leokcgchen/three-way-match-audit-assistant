import { useEffect, useState } from 'react'
import { api, type ExportReadiness, type WorkbookPreview } from '../api'
import type { Job } from '../types'
import { ChainPicker } from '../components/ChainPicker'
import { DESK_LIGHT_LEGEND_INLINE, DESK_LIGHT_LEGEND_TIP } from '../lib/deskLights'

type Props = { job: Job; onJob: (j: Job) => void; onGo: (step: string) => void }

export function WorkbookPage({ job, onJob, onGo }: Props) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [preview, setPreview] = useState<WorkbookPreview | null>(null)
  const [previewErr, setPreviewErr] = useState('')
  const [sheet, setSheet] = useState('')
  const [readiness, setReadiness] = useState<ExportReadiness | null>(null)
  const [readinessErr, setReadinessErr] = useState('')
  const workbookEntries =
    job.workbook_paths && job.workbook_paths.length
      ? job.workbook_paths
      : job.workbook_path
        ? [
            {
              format: '',
              label: '审阅底稿',
              path: job.workbook_path,
              file_name: job.workbook_path.split(/[/\\]/).pop() || 'workbook.xlsx',
            },
          ]
        : []
  const [activeFormat, setActiveFormat] = useState(workbookEntries[0]?.format || '')

  const loadPreview = async (jobId: string, sh?: string, format?: string) => {
    try {
      const p = await api.workbookPreview(jobId, sh, format || undefined)
      setPreview(p)
      setSheet(p.sheet || '')
      setPreviewErr('')
    } catch (e) {
      setPreview(null)
      setPreviewErr(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    if (workbookEntries.length) {
      const fmt = workbookEntries[0]?.format || ''
      setActiveFormat(fmt)
      void loadPreview(job.job_id, undefined, fmt || undefined)
    } else {
      setPreview(null)
      setPreviewErr('')
      setActiveFormat('')
    }
  }, [job.job_id, job.workbook_path, JSON.stringify(job.workbook_paths || [])])

  const loadReadiness = async () => {
    try {
      setReadiness(await api.exportReadiness(job.job_id))
      setReadinessErr('')
    } catch (e) {
      setReadiness(null)
      setReadinessErr(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    void loadReadiness()
  }, [job.job_id, job.updated_at, job.active_chain_id, job.conclusion_confirmed])

  const exportWb = async () => {
    setBusy(true)
    setErr('')
    setMsg('')
    setPreviewErr('')
    try {
      const next = await api.exportWorkbook(job.job_id)
      onJob(next)
      await loadReadiness()
      const n = (next.workbook_paths || []).length || (next.workbook_path ? 1 : 0)
      setMsg(n > 1 ? `已生成 ${n} 份底稿（按勾选目标各一份，互不偏重）。` : '底稿已生成，可预览或下载。')
      const firstFormat = next.workbook_paths?.[0]?.format
      const link = document.createElement('a')
      link.href = api.workbookDownloadUrl(job.job_id, firstFormat || undefined)
      link.download = ''
      link.click()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      await loadReadiness()
    } finally {
      setBusy(false)
    }
  }

  const handlePrimary = () => {
    if (!readiness) return
    if (readiness.ready) {
      void exportWb()
      return
    }
    const target = readiness.stages.find((stage) => stage.blocking && stage.action)?.action?.step
    onGo(target || 'event_review')
  }

  return (
    <div className="panel panel-fill">
      <div className="panel-head">
        <div>
          <h3>导出底稿</h3>
          <div className="hint">
            按所选底稿目标导出；多选官方模板时各生成一份。下方「导出前检查」与后端导出门禁同源。
          </div>
        </div>
      </div>
      <div className="panel-body">
        <section className={`export-readiness ${readiness?.ready ? 'ready' : 'blocked'}`} aria-live="polite">
          <div className="export-readiness-head">
            <div>
              <span className="eyebrow">导出前检查</span>
              <h4>{readiness ? readiness.summary : '正在检查导出条件…'}</h4>
              <p>这里是生成底稿的唯一门禁清单；每一项都能直接进入对应处理页面。</p>
            </div>
            <button
              className="btn primary export-primary-cta"
              type="button"
              disabled={!readiness || busy}
              onClick={handlePrimary}
            >
              {!readiness
                ? '正在检查…'
                : busy
                  ? '正在生成…'
                  : readiness.ready
                    ? '生成并下载底稿'
                    : `处理 ${readiness.blocked_count} 个阻断项`}
            </button>
          </div>
          {readinessErr && <p className="err">无法读取导出条件：{readinessErr}</p>}
          {readiness?.lights && (
            <div
              className="desk-light-bar export-light-bar"
              aria-label="样本红黄绿"
              data-tip={DESK_LIGHT_LEGEND_TIP}
            >
              <span className="desk-light-chip is-green">绿 {readiness.lights.green}</span>
              <span className="desk-light-chip is-yellow">黄 {readiness.lights.yellow}</span>
              <span className="desk-light-chip is-red">红 {readiness.lights.red}</span>
              <span className="desk-light-chip is-wait">尚未判断 {readiness.lights.wait}</span>
              <span className="desk-light-legend hint">{DESK_LIGHT_LEGEND_INLINE}</span>
            </div>
          )}
          {readiness?.lights?.progress && (
            <p className="hint export-progress-line">
              已完成 {readiness.lights.progress.done ?? 0} · 缺少凭证资料{' '}
              {readiness.lights.progress.docs_missing ?? 0} · 缺少关键字段{' '}
              {readiness.lights.progress.fields_missing ?? 0} · 匹配或测试异常{' '}
              {readiness.lights.progress.match_exception ?? 0} · 等待人工判断{' '}
              {readiness.lights.progress.await_human ?? 0}
            </p>
          )}
          {!!readiness?.lights?.issues?.length && (
            <ul className="export-issue-list">
              {readiness.lights.issues.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          {readiness && (
            <div className="export-readiness-list">
              {readiness.stages.map((stage) => (
                <article
                  key={stage.id}
                  className={`export-stage ${stage.blocking ? 'attention' : 'complete'}`}
                >
                  <div className="export-stage-mark" aria-hidden>
                    {stage.blocking ? '!' : '✓'}
                  </div>
                  <div className="export-stage-copy">
                    <strong>{stage.label}</strong>
                    <p>{stage.reason}</p>
                    {!!stage.affected_groups?.length && (
                      <small>涉及业务组：{stage.affected_groups.join('、')}</small>
                    )}
                  </div>
                  {stage.action && (
                    <button
                      type="button"
                      className="btn"
                      onClick={() => onGo(stage.action!.step)}
                    >
                      {stage.action.label}
                    </button>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
        {err && <p className="err">{err}</p>}
        {msg && <p className="ok-text">{msg}</p>}

        <ChainPicker job={job} onJob={onJob} />

        <details className="plan-box">
          <summary>
            <strong>本次目标与计划明细</strong>
          </summary>
          <div className="mt-8">
            <strong>本次目标：</strong>
            {(job.plan?.goals || []).map((g) => g.label).join('、') || '未选'}
          </div>
          <div className="mt-8">
            <strong>计划 sheet：</strong>
            {(job.plan?.workbook_sheets || []).join('、') || '—'}
          </div>
          <p className="hint mt-8">门禁以上方「导出前检查」为准（按业务组全量统计，不看单笔顶层镜像）。</p>
        </details>

        {workbookEntries.length > 0 && (
          <div className="plan-box mt-12">
            <strong>已生成文件（{workbookEntries.length}）：</strong>
            <ul className="checklist mt-8">
              {workbookEntries.map((e) => (
                <li key={e.path || e.format || e.file_name}>
                  <span>
                    {e.label}
                    <code className="hint" style={{ marginLeft: 8 }}>
                      {e.file_name}
                    </code>
                  </span>
                  <span className="toolbar" style={{ gap: 6 }}>
                    <button
                      className="btn"
                      style={{ minHeight: 28, padding: '0.15rem 0.5rem' }}
                      onClick={() => {
                        setActiveFormat(e.format || '')
                        void loadPreview(job.job_id, undefined, e.format || undefined)
                      }}
                    >
                      预览
                    </button>
                    <a
                      className="btn a-as-btn"
                      style={{ minHeight: 28, padding: '0.15rem 0.5rem' }}
                      href={api.workbookDownloadUrl(job.job_id, e.format || undefined)}
                    >
                      下载
                    </a>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {previewErr && <p className="err mt-12">预览失败：{previewErr}</p>}

        {preview && (
          <div className="workbook-preview mt-16">
            <div className="toolbar between mb-12">
              <h4 className="section-title" style={{ margin: 0 }}>
                底稿预览
                {preview.sheet ? ` · ${preview.sheet}` : ''}
              </h4>
              <select
                className="field-select"
                style={{ maxWidth: 280, marginBottom: 0 }}
                value={sheet}
                onChange={(e) => {
                  const s = e.target.value
                  setSheet(s)
                  void loadPreview(job.job_id, s, activeFormat || undefined)
                }}
              >
                {(preview.sheets || []).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    {(preview.columns || []).map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(preview.rows || []).slice(0, 80).map((row, i) => (
                    <tr key={i}>
                      {(preview.columns || []).map((c) => (
                        <td key={c}>{row[c] ?? ''}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {preview.note && <p className="hint mt-8">{preview.note}</p>}
          </div>
        )}
      </div>
    </div>
  )
}
