import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { PacketContactSheet } from '../components/PacketContactSheet'
import { PacketInspector } from '../components/PacketInspector'
import {
  businessIdsForUnit,
  confirmNormalUnits,
  intakeBlockers,
  mergeUnitWithPrevious,
  reviewSummary,
  splitUnitAtPage,
} from '../lib/documentIntake'
import type { Job, PacketUnit } from '../types'

type Props = {
  job: Job
  onJob: (job: Job) => void
  ocrBusy?: boolean
  onProcess?: (force?: boolean) => Promise<void>
}

type BusyState = 'idle' | 'analyze' | 'confirm'

function unitId(prefix = 'du'): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(16).slice(2, 8)}`
}

export function PacketUnpackPage({ job, onJob, ocrBusy = false }: Props) {
  const [units, setUnits] = useState<PacketUnit[]>(job.packet_units || [])
  const [selectedUnitIds, setSelectedUnitIds] = useState<string[]>([])
  const [focusedFile, setFocusedFile] = useState('')
  const [focusedPage, setFocusedPage] = useState(1)
  const [previewUrl, setPreviewUrl] = useState('')
  const [thumbnails, setThumbnails] = useState<Record<string, string>>({})
  const [fileModes, setFileModes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<BusyState>('idle')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const analyzeInflight = useRef(false)
  const thumbnailUrls = useRef<string[]>([])

  const run = job.packet_run
  const files = useMemo(() => run?.files || [], [run?.files])
  const packetFiles = useMemo(
    () => files.filter((file) => file.kind !== 'standard'),
    [files],
  )
  const shownFiles = packetFiles.length ? packetFiles : files
  const locked = busy !== 'idle' || ocrBusy

  useEffect(() => {
    setUnits(job.packet_units || [])
  }, [job.job_id, run?.run_id, job.packet_units])

  useEffect(() => {
    if (!focusedFile && shownFiles[0]) setFocusedFile(shownFiles[0].file_name)
  }, [focusedFile, shownFiles])

  const analyze = async () => {
    if (locked || analyzeInflight.current) return
    analyzeInflight.current = true
    setBusy('analyze')
    setError('')
    try {
      const next = await api.packetAnalyze(job.job_id, { file_modes: fileModes })
      onJob(next)
      setUnits(next.packet_units || [])
      const count = (next.packet_units || []).filter((unit) => !unit.dropped).length
      setMessage(`AI 已提出 ${count} 张单据建议，请人工批量确认。`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      analyzeInflight.current = false
      setBusy('idle')
    }
  }

  useEffect(() => {
    if (String(run?.status || '') === 'pending_analyze') void analyze()
    // analyze deliberately runs only when the server enters pending_analyze.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, run?.status])

  const pageCountSignature = shownFiles
    .map((file) => {
      const observed = units
        .filter((unit) => unit.source_file === file.file_name)
        .flatMap((unit) => unit.pages)
      return file.page_count || Math.max(0, ...observed)
    })
    .join(',')
  const pageKeys = useMemo(() => {
    const counts = pageCountSignature.split(',').map(Number)
    return shownFiles.flatMap((file, fileIndex) =>
      Array.from({ length: counts[fileIndex] || 0 }, (_, index) => ({
        sourceFile: file.file_name,
        page: index + 1,
      })),
    )
  }, [shownFiles, pageCountSignature])

  useEffect(() => {
    let cancelled = false
    thumbnailUrls.current.forEach((url) => URL.revokeObjectURL(url))
    thumbnailUrls.current = []
    setThumbnails({})
    const queue = [...pageKeys]
    const loadNext = async () => {
      while (!cancelled) {
        const item = queue.shift()
        if (!item) return
        const { sourceFile, page } = item
        try {
          const response = await api.previewPage(job.job_id, sourceFile, page - 1)
          const url = URL.createObjectURL(response.blob)
          if (cancelled) {
            URL.revokeObjectURL(url)
            return
          }
          thumbnailUrls.current.push(url)
          setThumbnails((current) => ({ ...current, [`${sourceFile}:${page}`]: url }))
        } catch {
          // A missing thumbnail must not block reviewing the remaining pages.
        }
      }
    }
    void Promise.allSettled(
      Array.from({ length: Math.min(4, queue.length) }, () => loadNext()),
    )
    return () => {
      cancelled = true
    }
  }, [job.job_id, pageKeys])

  useEffect(
    () => () => thumbnailUrls.current.forEach((url) => URL.revokeObjectURL(url)),
    [],
  )

  useEffect(() => {
    if (!focusedFile) return
    let cancelled = false
    let url = ''
    api.previewPage(job.job_id, focusedFile, focusedPage - 1)
      .then((response) => {
        url = URL.createObjectURL(response.blob)
        if (cancelled) URL.revokeObjectURL(url)
        else setPreviewUrl(url)
      })
      .catch(() => {
        if (!cancelled) setPreviewUrl('')
      })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [job.job_id, focusedFile, focusedPage])

  const businessIds = useMemo(() => {
    const declared = job.sample_population?.business_ids || []
    if (declared.length) return declared
    return Array.from(new Set(units.flatMap(businessIdsForUnit)))
  }, [job.sample_population?.business_ids, units])
  const selectedUnits = units.filter((unit) => selectedUnitIds.includes(unit.unit_id))
  const summary = reviewSummary(units, shownFiles)
  const blockers = intakeBlockers(units, shownFiles)
  const blankPageKeys = useMemo(
    () => new Set(
      (run?.pages || [])
        .filter((page) => page.page_role === 'blank')
        .map((page) => `${page.source_file}:${page.page}`),
    ),
    [run?.pages],
  )

  const replaceChangedUnits = (changed: PacketUnit[]) => {
    const byId = new Map(changed.map((unit) => [unit.unit_id, unit]))
    setUnits((current) => current.map((unit) => byId.get(unit.unit_id) || unit))
  }

  const dropPage = (sourceFile: string, page: number) => {
    setUnits((current) => {
      const owner = current.find(
        (unit) => !unit.dropped && unit.source_file === sourceFile && unit.pages.includes(page),
      )
      if (!owner) return current
      if (owner.pages.length === 1) {
        return current.map((unit) => unit.unit_id === owner.unit_id
          ? { ...unit, dropped: true, drop_reason: 'manual_drop_page', boundary_confirmed: true }
          : unit)
      }
      const kept = owner.pages.filter((value) => value !== page)
      const dropped: PacketUnit = {
        ...owner,
        unit_id: unitId('dropped'),
        pages: [page],
        page_start: page,
        page_end: page,
        dropped: true,
        drop_reason: 'manual_drop_page',
        boundary_confirmed: true,
      }
      return [
        ...current.map((unit) => unit.unit_id === owner.unit_id
          ? { ...unit, pages: kept, page_start: kept[0], page_end: kept[kept.length - 1] }
          : unit),
        dropped,
      ]
    })
  }

  const dropBlankPages = (sourceFile: string) => {
    const pages = Array.from(blankPageKeys)
      .filter((key) => key.startsWith(`${sourceFile}:`))
      .map((key) => Number(key.slice(key.lastIndexOf(':') + 1)))
    pages.forEach((page) => dropPage(sourceFile, page))
  }

  const markFileSingleBusiness = (sourceFile: string) => {
    const hints = Array.from(new Set(
      units.filter((unit) => unit.source_file === sourceFile).flatMap(businessIdsForUnit),
    ))
    if (hints.length !== 1) return
    setUnits((current) => current.map((unit) => unit.source_file === sourceFile
      ? {
          ...unit,
          business_ids: hints,
          chain_id: hints[0],
          business_binding_source: 'human',
        }
      : unit))
  }

  const confirmAndStart = async () => {
    if (locked || blockers.length) return
    setBusy('confirm')
    setError('')
    try {
      const next = await api.packetConfirm(job.job_id, {
        units: units.map((unit) => ({
          unit_id: unit.unit_id,
          source_file: unit.source_file,
          source_path: unit.source_path,
          pages: unit.pages,
          doc_type: unit.doc_type,
          card_type: unit.card_type,
          dropped: unit.dropped,
          chain_id: unit.chain_id,
          business_ids: businessIdsForUnit(unit),
          suggested_doc_type: unit.suggested_doc_type,
          doc_type_source: unit.doc_type_source,
          boundary_confirmed: unit.boundary_confirmed,
          business_binding_source: unit.business_binding_source,
          drop_reason: unit.drop_reason,
          keys: unit.keys,
        })),
        file_modes: fileModes,
        start_ocr: true,
      })
      onJob(next)
      setMessage('人工确认已保存，正在进入字段识别。')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy('idle')
    }
  }

  return (
    <div className="packet-review-workbench">
      <header className="packet-review-toolbar">
        <div>
          <span className="eyebrow">上传凭证 · 人工确认</span>
          <h2>多文件单据联络表</h2>
          <p>AI 先建议边界与类型；审计师在一个视野内确认业务、拆分和合并。</p>
        </div>
        <div className="packet-review-kpis" aria-label="拆包进度">
          <span><b>{summary.fileCount}</b> 个文件</span>
          <span><b>{summary.pageCount}</b> 页</span>
          <span><b>{summary.unitCount}</b> 张单据</span>
          <span className={summary.pendingCount ? 'is-warn' : ''}><b>{summary.pendingCount}</b> 待确认</span>
          <span className={summary.unassignedCount ? 'is-danger' : ''}><b>{summary.unassignedCount}</b> 未归属</span>
          <span className={summary.anomalyCount ? 'is-danger' : ''}><b>{summary.anomalyCount}</b> 异常</span>
        </div>
        <div className="packet-review-toolbar-actions">
          <button className="btn" type="button" disabled={locked} onClick={() => void analyze()}>
            {busy === 'analyze' ? '分析中…' : '重新生成 AI 建议'}
          </button>
          <button
            className="btn primary"
            type="button"
            disabled={locked}
            onClick={() => setUnits((current) => confirmNormalUnits(current))}
          >
            批量确认正常项
          </button>
        </div>
      </header>

      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {message ? <div className="success-banner" role="status">{message}</div> : null}

      <div className="packet-review-layout">
        <nav className="packet-file-nav" aria-label="资料包文件">
          <h3>文件</h3>
          {shownFiles.map((file, index) => {
            const fileUnits = units.filter((unit) => unit.source_file === file.file_name && !unit.dropped)
            const pending = fileUnits.filter((unit) => !unit.boundary_confirmed).length
            return (
              <div key={file.file_name} className={focusedFile === file.file_name ? 'is-active' : ''}>
                <button
                  type="button"
                  className="packet-file-link"
                  onClick={() => {
                    setFocusedFile(file.file_name)
                    document.getElementById(`packet-contact-file-${index}`)?.scrollIntoView({ block: 'start' })
                  }}
                >
                  <strong>{file.file_name}</strong>
                  <span>{file.page_count || '?'} 页 · {pending} 待确认</span>
                </button>
                <div className="packet-file-tools">
                  <button type="button" disabled={locked} onClick={() => {
                    setFileModes((current) => ({ ...current, [file.file_name]: 'single' }))
                    markFileSingleBusiness(file.file_name)
                  }}>整份同一业务</button>
                  <button type="button" disabled={locked} onClick={() => dropBlankPages(file.file_name)}>
                    去除疑似空白
                  </button>
                </div>
              </div>
            )
          })}
        </nav>

        <main className="packet-review-main">
          <PacketContactSheet
            files={shownFiles}
            units={units}
            selectedUnitIds={selectedUnitIds}
            thumbnails={thumbnails}
            locked={locked}
            blankPageKeys={blankPageKeys}
            onSelectionChange={setSelectedUnitIds}
            onPageFocus={(sourceFile, page, selectedId) => {
              setFocusedFile(sourceFile)
              setFocusedPage(page)
              if (!selectedUnitIds.includes(selectedId)) setSelectedUnitIds([selectedId])
            }}
            onSplit={(selectedId, page) => setUnits((current) => splitUnitAtPage(current, selectedId, page))}
            onMerge={(selectedId) => {
              setUnits((current) => mergeUnitWithPrevious(current, selectedId))
              setSelectedUnitIds((current) => current.filter((id) => id !== selectedId))
            }}
            onDropPage={dropPage}
            onRestoreUnit={(selectedId) => setUnits((current) => current.map((unit) => unit.unit_id === selectedId
              ? { ...unit, dropped: false, drop_reason: undefined, boundary_confirmed: false }
              : unit))}
            onOpenOriginal={(sourceFile) => window.open(api.fileUrl(job.job_id, sourceFile), '_blank', 'noopener,noreferrer')}
          />
          {focusedFile ? (
            <details className="packet-full-preview">
              <summary>查看 {focusedFile} 第 {focusedPage} 页大图</summary>
              {previewUrl
                ? <img src={previewUrl} alt={`${focusedFile} 第 ${focusedPage} 页大图`} />
                : <p className="preview-empty">此页暂时无法预览</p>}
            </details>
          ) : null}
        </main>

        <PacketInspector
          selectedUnits={selectedUnits}
          businessIds={businessIds}
          locked={locked}
          onChange={replaceChangedUnits}
          onConfirmSelected={(ids) => setUnits((current) => current.map((unit) => ids.includes(unit.unit_id)
            ? { ...unit, boundary_confirmed: true, needs_review: false }
            : unit))}
        />
      </div>

      <footer className="packet-review-gate">
        <div>
          <strong>{blockers.length ? `还有 ${blockers.length} 个阻断项` : '可以进入字段识别'}</strong>
          <p>
            {blockers.length
              ? Array.from(new Set(blockers.map((item) => item.message))).join('；')
              : '所有非废弃单据均已确认业务归属、边界和类型。'}
          </p>
        </div>
        <button
          type="button"
          className="btn primary"
          disabled={locked || blockers.length > 0}
          onClick={() => void confirmAndStart()}
        >
          {busy === 'confirm' ? '保存并启动中…' : '确认并开始识别'}
        </button>
      </footer>
    </div>
  )
}
