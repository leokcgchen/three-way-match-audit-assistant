import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { mergeUnitWithPrevious, splitUnitAtPage } from '../lib/documentIntake'
import type { Job, PacketUnit } from '../types'

const TYPE_LABELS: Record<string, string> = {
  contract: '合同',
  order: '订单',
  delivery: '发货单',
  receipt: '签收/验收',
  invoice: '发票',
  payment: '回款',
  unresolved: '未识别',
  other: '未识别',
  bank_receipt: '电子回单',
  payment_request: '付款申请',
  delivery_note: '发货单',
  delivery_acceptance: '发货验收单',
  receipt_acceptance: '签收验收单',
  acceptance_record: '验收单',
  delivery_receipt: '送货单',
}

function unitTypeLabel(u: PacketUnit): string {
  return TYPE_LABELS[u.card_type || ''] || TYPE_LABELS[u.doc_type] || u.doc_type
}

const TYPE_OPTIONS = [
  'bank_receipt',
  'contract',
  'order',
  'delivery_note',
  'delivery_acceptance',
  'receipt_acceptance',
  'invoice',
  'payment',
  'unresolved',
]

const TYPE_TO_HOST: Record<string, string> = {
  bank_receipt: 'payment',
  payment_request: 'payment',
  contract: 'contract',
  order: 'order',
  delivery: 'delivery',
  delivery_note: 'delivery',
  delivery_acceptance: 'delivery',
  receipt: 'receipt',
  receipt_acceptance: 'receipt',
  acceptance_record: 'receipt',
  invoice: 'invoice',
  payment: 'payment',
  unresolved: 'unresolved',
}

type Props = {
  job: Job
  onJob: (j: Job) => void
  ocrBusy?: boolean
  onProcess?: (force?: boolean) => Promise<void>
}

function uid(): string {
  return `du_${Math.random().toString(16).slice(2, 10)}`
}

function pageRangeLabel(pages: number[]): string {
  if (!pages.length) return ''
  if (pages.length === 1) return `第${pages[0]}页`
  return `第${pages[0]}–${pages[pages.length - 1]}页`
}

export function PacketUnpackPage({ job, onJob, ocrBusy = false, onProcess }: Props) {
  const [units, setUnits] = useState<PacketUnit[]>(job.packet_units || [])
  const [busy, setBusy] = useState<'idle' | 'analyze' | 'confirm'>('idle')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [fileName, setFileName] = useState('')
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState('')
  const [previewUrl, setPreviewUrl] = useState('')
  const [thumbs, setThumbs] = useState<Record<string, string>>({})
  const [newChain, setNewChain] = useState('')
  const [fileMode, setFileMode] = useState<Record<string, string>>({})
  const thumbUrlsRef = useRef<string[]>([])
  const analyzeInflightRef = useRef(false)

  const run = job.packet_run
  const files = run?.files || []
  const packetFiles = files.filter((f) => f.kind && f.kind !== 'standard')
  const locked = busy !== 'idle' || ocrBusy

  useEffect(() => {
    setUnits(job.packet_units || [])
  }, [job.job_id, run?.run_id, job.packet_units?.length])

  useEffect(() => {
    const st = String(run?.status || '')
    if (st === 'pending_analyze') {
      void analyze()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, run?.status])

  useEffect(() => {
    const first = packetFiles[0]?.file_name || units[0]?.source_file || ''
    if (!fileName && first) setFileName(first)
  }, [fileName, packetFiles, units])

  const fileMeta =
    packetFiles.find((f) => f.file_name === fileName) || files.find((f) => f.file_name === fileName)
  const pageCount =
    fileMeta?.page_count ||
    Math.max(1, ...units.filter((u) => u.source_file === fileName).flatMap((u) => u.pages), 1)

  useEffect(() => {
    if (!fileName) return
    let cancelled = false
    let created = ''
    api
      .previewPage(job.job_id, fileName, page - 1)
      .then((r) => {
        const url = URL.createObjectURL(r.blob)
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        created = url
        setPreviewUrl(url)
      })
      .catch(() => {
        if (!cancelled) setPreviewUrl('')
      })
    return () => {
      cancelled = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [job.job_id, fileName, page])

  const visibleUnits = units.filter((u) => !u.dropped)
  const fileUnits = visibleUnits.filter((u) => u.source_file === fileName)
  const droppedPageSet = new Set(
    units.filter((u) => u.dropped && u.source_file === fileName).flatMap((u) => u.pages),
  )
  const blankPageSet = useMemo(() => {
    return new Set(
      (run?.pages || [])
        .filter((p) => p.source_file === fileName && p.page_role === 'blank')
        .map((p) => p.page),
    )
  }, [run?.pages, fileName])
  const blankPagesLive = useMemo(() => {
    const dropped = new Set(
      units.filter((u) => u.dropped && u.source_file === fileName).flatMap((u) => u.pages),
    )
    return Array.from(blankPageSet)
      .filter((n) => !dropped.has(n))
      .sort((a, b) => a - b)
  }, [blankPageSet, units, fileName])
  const currentPageIsBlank = blankPageSet.has(page) && !droppedPageSet.has(page)
  const thumbKey = fileUnits.map((u) => `${u.unit_id}:${u.pages[0] || 1}`).join('|')

  useEffect(() => {
    let cancelled = false
    const created: string[] = []
    thumbUrlsRef.current.forEach((u) => URL.revokeObjectURL(u))
    thumbUrlsRef.current = []
    setThumbs({})
    ;(async () => {
      const next: Record<string, string> = {}
      for (const unit of fileUnits) {
        const p0 = unit.pages[0]
        if (!p0) continue
        const key = `${unit.source_file}:${p0}`
        if (next[key]) continue
        try {
          const r = await api.previewPage(job.job_id, unit.source_file, p0 - 1)
          if (cancelled) continue
          const url = URL.createObjectURL(r.blob)
          created.push(url)
          next[key] = url
        } catch {
          /* 无预览时卡片仍可用 */
        }
      }
      if (cancelled) {
        created.forEach((u) => URL.revokeObjectURL(u))
        return
      }
      thumbUrlsRef.current = created
      setThumbs(next)
    })()
    return () => {
      cancelled = true
    }
    // fileUnits identity is thumbKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, fileName, thumbKey])

  useEffect(
    () => () => {
      thumbUrlsRef.current.forEach((u) => URL.revokeObjectURL(u))
    },
    [],
  )

  const chains = useMemo(() => {
    const ids = Array.from(new Set(visibleUnits.map((u) => u.chain_id || '未识别业务号')))
    if (!ids.includes('未识别业务号')) ids.push('未识别业务号')
    return ids
  }, [visibleUnits])

  const selected =
    units.find((u) => u.unit_id === selectedId) ||
    fileUnits.find((u) => u.pages.includes(page)) ||
    units.find((u) => u.dropped && u.source_file === fileName && u.pages.includes(page))
  const sameFileUnits = selected
    ? visibleUnits.filter((u) => u.source_file === selected.source_file)
    : []
  const selectedIdx = selected ? sameFileUnits.findIndex((u) => u.unit_id === selected.unit_id) : -1
  const canMerge = Boolean(selected && !selected.dropped && selectedIdx > 0)
  const canSplit = Boolean(
    selected &&
      !selected.dropped &&
      selected.pages.includes(page) &&
      selected.pages[0] !== page &&
      selected.pages.length > 1,
  )
  const mergeTip = canMerge
    ? '切多了：把当前这张单和同一文件里上一张合成一张（例如合同被拆成两页）。'
    : '先点中间一张单。若它是该文件的第一张，就不能再往上合并。'
  const splitTip = canSplit
    ? `从左边当前第${page}页切开：这一页起算新的一张单。`
    : '先在左边点要切开的那一页（不能是这张单的第一页），再点这里。'

  const analyze = async () => {
    if (locked || analyzeInflightRef.current) return
    analyzeInflightRef.current = true
    setBusy('analyze')
    setErr('')
    try {
      const next = await api.packetAnalyze(job.job_id, { file_modes: fileMode })
      onJob(next)
      setUnits(next.packet_units || [])
      setMsg(`已按页切开 ${(next.packet_units || []).length} 张单，请核对类型和归属笔。`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      analyzeInflightRef.current = false
      setBusy('idle')
    }
  }

  const patchUnit = (id: string, patch: Partial<PacketUnit>) => {
    setUnits((prev) => prev.map((u) => (u.unit_id === id ? { ...u, ...patch } : u)))
  }

  const applyType = (id: string, card: string) => {
    const host = TYPE_TO_HOST[card] || card
    patchUnit(id, {
      card_type: card,
      doc_type: host,
      host_type: host,
      needs_review: card === 'unresolved',
    })
  }

  const dropSelected = () => {
    if (!selected || selected.dropped) return
    const next = sameFileUnits.find((u) => u.unit_id !== selected.unit_id)
    patchUnit(selected.unit_id, { dropped: true })
    if (next) {
      setSelectedId(next.unit_id)
      setPage(next.pages[0])
    } else {
      setSelectedId('')
    }
  }

  /** 只去掉当前页（空白页并进合同/订单时，不能整张删）。 */
  const dropCurrentPage = (pageNo: number = page) => {
    const owner =
      units.find((u) => !u.dropped && u.source_file === fileName && u.pages.includes(pageNo)) ||
      null
    if (!owner) return
    if (owner.pages.length === 1) {
      patchUnit(owner.unit_id, { dropped: true })
      const next = visibleUnits.find(
        (u) => u.unit_id !== owner.unit_id && u.source_file === fileName,
      )
      if (next) {
        setSelectedId(next.unit_id)
        setPage(next.pages[0])
      } else {
        setSelectedId(owner.unit_id)
      }
      return
    }
    const keep = owner.pages.filter((p) => p !== pageNo).sort((a, b) => a - b)
    const nid = uid()
    setUnits((list) => [
      ...list.map((u) =>
        u.unit_id === owner.unit_id
          ? { ...u, pages: keep, page_start: keep[0], page_end: keep[keep.length - 1] }
          : u,
      ),
      {
        ...owner,
        unit_id: nid,
        pages: [pageNo],
        page_start: pageNo,
        page_end: pageNo,
        dropped: true,
        split_reason: 'manual_drop_page',
        needs_review: true,
        review_reasons: ['人工去掉的空白/废页'],
        doc_type: 'unresolved',
        card_type: 'unresolved',
        host_type: 'unresolved',
      },
    ])
    setSelectedId(nid)
    setPage(pageNo)
  }

  const dropAllBlankPages = () => {
    const targets = blankPagesLive.slice()
    if (!targets.length) return
    setUnits((list) => {
      let next = list.map((u) => ({ ...u, pages: [...u.pages] }))
      const extras: PacketUnit[] = []
      for (const pageNo of targets) {
        const idx = next.findIndex(
          (u) => !u.dropped && u.source_file === fileName && u.pages.includes(pageNo),
        )
        if (idx < 0) continue
        const owner = next[idx]
        if (owner.pages.length === 1) {
          next[idx] = { ...owner, dropped: true }
          continue
        }
        const keep = owner.pages.filter((p) => p !== pageNo).sort((a, b) => a - b)
        next[idx] = {
          ...owner,
          pages: keep,
          page_start: keep[0],
          page_end: keep[keep.length - 1],
        }
        extras.push({
          ...owner,
          unit_id: uid(),
          pages: [pageNo],
          page_start: pageNo,
          page_end: pageNo,
          dropped: true,
          split_reason: 'manual_drop_blank',
          needs_review: true,
          review_reasons: ['疑似空白/隔页'],
          doc_type: 'unresolved',
          card_type: 'unresolved',
          host_type: 'unresolved',
        })
      }
      return [...next, ...extras]
    })
    setMsg(`已去掉 ${targets.length} 页疑似空白，确认后不识别；可点灰色页码放回。`)
  }

  const restoreSelected = () => {
    if (!selected?.dropped) return
    patchUnit(selected.unit_id, { dropped: false })
  }

  const dropAction = () => {
    if (!selected || selected.dropped) return
    if (currentPageIsBlank || (selected.pages.includes(page) && blankPageSet.has(page))) {
      dropCurrentPage(page)
      return
    }
    dropSelected()
  }

  const selectedTypeValue = selected
    ? TYPE_OPTIONS.includes(selected.card_type || '')
      ? selected.card_type || 'unresolved'
      : selected.doc_type || 'unresolved'
    : 'unresolved'

  const mergeWithPrev = () => {
    if (!selected || selectedIdx <= 0) return
    const prev = sameFileUnits[selectedIdx - 1]
    setUnits((list) => mergeUnitWithPrevious(list, selected.unit_id))
    setSelectedId(prev.unit_id)
  }

  const splitAtPage = () => {
    if (!canSplit || !selected) return
    setUnits((list) => splitUnitAtPage(list, selected.unit_id, page))
    setSelectedId(`${selected.unit_id}__p${page}`)
  }

  const markSingleChain = () => {
    if (!fileName) return
    const so = units.find((u) => u.source_file === fileName && /^SO/i.test(u.chain_id || ''))?.chain_id
    const chain = so || units.find((u) => u.source_file === fileName)?.chain_id || '未识别业务号'
    setFileMode((m) => ({ ...m, [fileName]: 'single' }))
    setUnits((list) => list.map((u) => (u.source_file === fileName ? { ...u, chain_id: chain } : u)))
  }

  const confirm = async () => {
    if (locked) return
    setBusy('confirm')
    setErr('')
    try {
      const next = await api.packetConfirm(job.job_id, {
        units: units.map((u) => ({
          unit_id: u.unit_id,
          source_file: u.source_file,
          source_path: u.source_path,
          pages: u.pages,
          doc_type: u.doc_type,
          card_type: u.card_type,
          dropped: Boolean(u.dropped),
          chain_id: u.chain_id,
          keys: u.keys,
        })),
        file_modes: fileMode,
        start_ocr: false,
      })
      onJob(next)
      if (onProcess) {
        setMsg('已确认拆包，开始识别…')
        await onProcess(false)
      } else {
        setMsg('已确认拆包。请回「上传凭证」点「开始处理」。')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('idle')
    }
  }

  const warnings = run?.warnings || []
  const confirmTip =
    '按当前切开和归属生成单据并开始识别。后面字段核对、勾稽、测试不变。'

  return (
    <div className="panel panel-fill packet-unpack">
      <div className="panel-head">
        <div>
          <h3>拆包分笔</h3>
          <div className="hint">看切得对不对 → 改类型 → 拖到对应业务笔 → 确认后才识别。</div>
        </div>
        <div className="toolbar">
          <span className="tip-anchor" data-tip="按页再跑一遍切开建议。只出候选，不会直接放行或下审计结论。">
            <button className="btn" disabled={locked} onClick={() => void analyze()}>
              {busy === 'analyze' ? '切开中…' : '重新切开'}
            </button>
          </span>
          <span
            className="tip-anchor"
            data-tip="这个 PDF 都是同一笔业务时用：当前文件里的单据都归到同一个业务号。"
          >
            <button className="btn" disabled={locked || !fileName} onClick={markSingleChain}>
              整份是一笔
            </button>
          </span>
          <span className="tip-anchor" data-tip={confirmTip}>
            <button className="btn primary" disabled={locked || units.length === 0} onClick={() => void confirm()}>
              {busy === 'confirm' ? '提交中…' : '确认拆包并开始识别'}
            </button>
          </span>
        </div>
      </div>
      <div className="panel-body packet-body">
        {err && <p className="err">{err}</p>}
        {msg && <p className="ok-text">{msg}</p>}
        {warnings.length > 0 && <p className="hint">{warnings.join('；')}</p>}
        <p className="packet-howto">
          左边点页看原件。切错了用「并入上一张」或「从本页拆开」。虚线页码是疑似空白/隔页，用「去掉这页」或顶上的「去掉空白页」；不会把整张合同删掉。中间的单可拖到右边对应业务笔。
        </p>
        {blankPagesLive.length > 0 && (
          <div className="packet-blank-banner" role="status">
            <span>
              本文件有 {blankPagesLive.length} 页疑似空白/隔页（第 {blankPagesLive.join('、')} 页）
            </span>
            <span className="tip-anchor" data-tip="只去掉这些空白页，旁边正常单据保留。确认后不识别；可点灰色页码放回。">
              <button type="button" className="btn compact" disabled={locked} onClick={dropAllBlankPages}>
                去掉空白页
              </button>
            </span>
          </div>
        )}
        <div className="packet-grid">
          <section className="packet-col packet-pages" aria-label="原件页">
            <div className="packet-col-head">原件页</div>
            <select
              className="field-select"
              value={fileName}
              onChange={(e) => {
                setFileName(e.target.value)
                setPage(1)
              }}
            >
              {(packetFiles.length ? packetFiles : files).map((f) => (
                <option key={f.file_name} value={f.file_name}>
                  {f.file_name}（{f.page_count || '?'}页 ·{' '}
                  {f.kind === 'packet_multi_chain' ? '可能多笔' : '按一笔看'}）
                </option>
              ))}
            </select>
            <div className="packet-ruler" role="listbox" aria-label="页码">
              {Array.from({ length: pageCount }, (_, i) => i + 1).map((n) => {
                const owner = fileUnits.find((u) => u.pages.includes(n))
                const droppedHere = droppedPageSet.has(n)
                const blankHere = blankPageSet.has(n)
                const tip = droppedHere
                  ? `第${n}页已去掉，点这里可放回。`
                  : blankHere
                    ? `第${n}页疑似空白/隔页，点「去掉这页」或顶上「去掉空白页」。`
                    : owner?.needs_review
                      ? `第${n}页看不清或类型没把握，请对照原件复核。`
                      : undefined
                return (
                  <button
                    key={n}
                    type="button"
                    className={`packet-tick${n === page ? ' is-on' : ''}${owner?.needs_review && !blankHere ? ' is-warn' : ''}${blankHere ? ' is-blank' : ''}${droppedHere ? ' is-dropped' : ''}`}
                    {...(tip ? { 'data-tip': tip } : {})}
                    onClick={() => {
                      setPage(n)
                      if (owner) setSelectedId(owner.unit_id)
                      else if (droppedHere) {
                        const gone = units.find(
                          (u) => u.dropped && u.source_file === fileName && u.pages.includes(n),
                        )
                        if (gone) setSelectedId(gone.unit_id)
                      }
                    }}
                  >
                    {n}
                  </button>
                )
              })}
            </div>
            <div className="packet-preview">
              {previewUrl ? <img src={previewUrl} alt={`第 ${page} 页`} /> : <div className="hint">无预览</div>}
            </div>
          </section>

          <section className="packet-col packet-units" aria-label="切开的单据">
            <div className="packet-col-head">这几页是一张单吗</div>
            <div className="packet-unit-actions">
              <span className="tip-anchor" data-tip={mergeTip}>
                <button className="btn compact" disabled={locked || !canMerge} onClick={mergeWithPrev}>
                  并入上一张
                </button>
              </span>
              <span className="tip-anchor" data-tip={splitTip}>
                <button className="btn compact" disabled={locked || !canSplit} onClick={splitAtPage}>
                  从本页拆开
                </button>
              </span>
              {selected?.dropped ? (
                <span className="tip-anchor" data-tip="把这张空白/无用页放回切开列表。">
                  <button className="btn compact" disabled={locked} onClick={restoreSelected}>
                    放回这张
                  </button>
                </span>
              ) : (
                <span
                  className="tip-anchor"
                  data-tip={
                    !selected
                      ? '先点中间一张单或左边页码，再去掉。'
                      : currentPageIsBlank
                        ? '只去掉左边当前这一页空白/废页，旁边的合同或订单会留下。'
                        : '去掉中间整张单（整段页）。若只想去掉其中一页空白，先点左边虚线页码。'
                  }
                >
                  <button className="btn compact" disabled={locked || !selected} onClick={dropAction}>
                    {currentPageIsBlank ? '去掉这页' : '去掉这张'}
                  </button>
                </span>
              )}
            </div>
            {selected?.dropped && (
              <p className="packet-drop-note">
                {pageRangeLabel(selected.pages)}已去掉，确认后不识别。点左边灰色页码或「放回这张」可恢复。
              </p>
            )}
            {currentPageIsBlank && selected && !selected.dropped && (
              <p className="packet-drop-note">当前第{page}页疑似空白，点「去掉这页」不会删掉同张单里的其它页。</p>
            )}
            <ul className="packet-unit-list">
              {fileUnits.map((u) => {
                const thumb = thumbs[`${u.source_file}:${u.pages[0]}`]
                return (
                  <li key={u.unit_id}>
                    <button
                      type="button"
                      className={`packet-unit-card${u.unit_id === selected?.unit_id ? ' is-on' : ''}`}
                      draggable
                      data-tip="点选后可改类型；按住拖到右边某一笔。"
                      onDragStart={(e) => e.dataTransfer.setData('text/unit-id', u.unit_id)}
                      onClick={() => {
                        setSelectedId(u.unit_id)
                        setPage(u.pages[0])
                      }}
                    >
                      {thumb ? (
                        <img className="packet-unit-thumb" src={thumb} alt="" />
                      ) : (
                        <span className="packet-unit-thumb is-empty" aria-hidden />
                      )}
                      <span className="packet-unit-meta">
                        <span className="packet-unit-type">{unitTypeLabel(u)}</span>
                        <span className="packet-unit-pages">{pageRangeLabel(u.pages)}</span>
                        {u.needs_review && <span className="badge warn">请复核</span>}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
            {selected && (
              <label className="packet-type-edit" data-tip="认错类型就在这里改。未识别的不会进入后面测试。">
                单据类型
                <select
                  className="field-select"
                  value={selectedTypeValue}
                  disabled={locked || selected.dropped}
                  onChange={(e) => applyType(selected.unit_id, e.target.value)}
                >
                  {TYPE_OPTIONS.map((k) => (
                    <option key={k} value={k}>
                      {TYPE_LABELS[k] || k}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </section>

          <section className="packet-col packet-chains" aria-label="业务笔">
            <div className="packet-col-head">属于哪一笔</div>
            <p className="packet-chain-hint">把中间的单据拖进对应业务号；没有号的放在「未识别业务号」。</p>
            <div className="packet-new-chain">
              <input
                value={newChain}
                onChange={(e) => setNewChain(e.target.value)}
                placeholder="业务号，如 SO25-0300"
                data-tip="手打订单号或合同号，把当前选中的单据归进去。"
              />
              <span
                className="tip-anchor"
                data-tip={
                  selected && newChain.trim()
                    ? '把当前选中的单据归到左边填的业务号。'
                    : '先点中间一张单，再填业务号。'
                }
              >
                <button
                  className="btn compact"
                  disabled={!newChain.trim() || !selected}
                  onClick={() => {
                    if (selected) patchUnit(selected.unit_id, { chain_id: newChain.trim() })
                    setNewChain('')
                  }}
                >
                  归到此笔
                </button>
              </span>
            </div>
            {chains.map((cid) => (
              <div
                key={cid}
                className={`packet-chain-bucket${selected?.chain_id === cid ? ' is-on' : ''}`}
                data-tip={`拖到这里，表示这些单据属于「${cid}」。`}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  const id = e.dataTransfer.getData('text/unit-id')
                  if (id) patchUnit(id, { chain_id: cid })
                }}
              >
                <div className="packet-chain-id">{cid}</div>
                <ul>
                  {visibleUnits
                    .filter((u) => (u.chain_id || '未识别业务号') === cid)
                    .map((u) => (
                      <li key={u.unit_id}>
                        <button
                          type="button"
                          className="packet-chain-item"
                          onClick={() => {
                            setFileName(u.source_file)
                            setSelectedId(u.unit_id)
                            setPage(u.pages[0])
                          }}
                        >
                          {unitTypeLabel(u)} · {pageRangeLabel(u.pages)}
                        </button>
                      </li>
                    ))}
                </ul>
              </div>
            ))}
          </section>
        </div>
      </div>
    </div>
  )
}
