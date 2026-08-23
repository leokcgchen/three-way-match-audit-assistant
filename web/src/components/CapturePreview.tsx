import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react'
import { api } from '../api'

type TextBlock = {
  id: string
  text: string
  bbox: number[]
  source?: string
}

type Props = {
  jobId: string
  fileName: string
  fieldKey: string
  fieldLabel?: string
  onApply: (text: string) => void
  onExit: () => void
}

type DragBox = { x0: number; y0: number; x1: number; y1: number }

function normFromPointer(
  el: HTMLElement,
  clientX: number,
  clientY: number,
): { x: number; y: number } {
  const r = el.getBoundingClientRect()
  const x = Math.min(1, Math.max(0, (clientX - r.left) / Math.max(r.width, 1)))
  const y = Math.min(1, Math.max(0, (clientY - r.top) / Math.max(r.height, 1)))
  return { x, y }
}

export function CapturePreview({
  jobId,
  fileName,
  fieldKey,
  fieldLabel,
  onApply,
  onExit,
}: Props) {
  const [page, setPage] = useState(0)
  const [pageCount, setPageCount] = useState(1)
  const [imgUrl, setImgUrl] = useState<string | null>(null)
  const [blocks, setBlocks] = useState<TextBlock[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [candidate, setCandidate] = useState('')
  const [note, setNote] = useState('')
  const [drag, setDrag] = useState<DragBox | null>(null)
  const dragging = useRef(false)
  const start = useRef<{ x: number; y: number } | null>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const objectUrlRef = useRef<string | null>(null)

  const loadPage = useCallback(
    async (pageIndex: number) => {
      setLoading(true)
      setErr('')
      setNote('')
      try {
        const { blob, meta } = await api.previewPage(jobId, fileName, pageIndex)
        if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
        const url = URL.createObjectURL(blob)
        objectUrlRef.current = url
        setImgUrl(url)
        setPage(meta.page_index)
        setPageCount(Math.max(1, meta.page_count))
        const tb = await api.textBlocks(jobId, fileName, meta.page_index)
        setBlocks((tb.blocks || []) as TextBlock[])
        setNote(`本页可点选块 ${(tb.blocks || []).length} · 也可拖框取字`)
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e))
        setImgUrl(null)
        setBlocks([])
      } finally {
        setLoading(false)
      }
    },
    [jobId, fileName],
  )

  useEffect(() => {
    void loadPage(0)
    return () => {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
    }
  }, [loadPage])

  const applyCandidate = (text: string, msg?: string) => {
    const t = String(text || '').trim()
    if (!t) {
      setNote(msg || '未识别到文字')
      return
    }
    setCandidate(t)
    setNote(msg || '已取到文字，可填入字段或继续框选')
  }

  const onBlockClick = (b: TextBlock, e: MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    applyCandidate(b.text, `点选 · ${b.source || 'block'}`)
  }

  const finishDrag = async (box: DragBox) => {
    const w = Math.abs(box.x1 - box.x0)
    const h = Math.abs(box.y1 - box.y0)
    if (w < 0.008 && h < 0.008) {
      setDrag(null)
      return
    }
    setLoading(true)
    setErr('')
    try {
      const out = await api.captureText(jobId, fileName, {
        page_index: page,
        x0: Math.min(box.x0, box.x1),
        y0: Math.min(box.y0, box.y1),
        x1: Math.max(box.x0, box.x1),
        y1: Math.max(box.y0, box.y1),
        field: fieldKey,
      })
      applyCandidate(out.text || '', out.message)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setDrag(null)
    }
  }

  return (
    <div className="capture-preview">
      <div className="toolbar between">
        <div className="hint">
          取证中 · {fieldLabel || fieldKey}
          {loading ? ' · 加载…' : ''}
        </div>
        <div className="toolbar">
          <button
            type="button"
            className="btn compact"
            disabled={page <= 0 || loading}
            onClick={() => void loadPage(page - 1)}
          >
            上一页
          </button>
          <span className="hint">
            {page + 1}/{pageCount}
          </span>
          <button
            type="button"
            className="btn compact"
            disabled={page + 1 >= pageCount || loading}
            onClick={() => void loadPage(page + 1)}
          >
            下一页
          </button>
          <button type="button" className="btn compact" onClick={onExit}>
            退出取证
          </button>
        </div>
      </div>
      {note && <p className="hint">{note}</p>}
      {err && <p className="err">{err}</p>}

      <div
        ref={stageRef}
        className="capture-stage"
        onPointerDown={(e) => {
          if (!stageRef.current || e.button !== 0) return
          // 点在 block 上由 block 处理
          if ((e.target as HTMLElement).closest('.capture-block')) return
          const p = normFromPointer(stageRef.current, e.clientX, e.clientY)
          dragging.current = true
          start.current = p
          setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y })
          stageRef.current.setPointerCapture(e.pointerId)
        }}
        onPointerMove={(e) => {
          if (!dragging.current || !start.current || !stageRef.current) return
          const p = normFromPointer(stageRef.current, e.clientX, e.clientY)
          setDrag({ x0: start.current.x, y0: start.current.y, x1: p.x, y1: p.y })
        }}
        onPointerUp={(e) => {
          if (!dragging.current || !start.current || !stageRef.current) return
          dragging.current = false
          const p = normFromPointer(stageRef.current, e.clientX, e.clientY)
          const box = { x0: start.current.x, y0: start.current.y, x1: p.x, y1: p.y }
          start.current = null
          void finishDrag(box)
          try {
            stageRef.current.releasePointerCapture(e.pointerId)
          } catch {
            /* ignore */
          }
        }}
      >
        {imgUrl ? (
          <img src={imgUrl} alt="capture page" className="capture-img" draggable={false} />
        ) : (
          <p className="preview-empty">无法加载页图</p>
        )}
        {blocks.map((b) => {
          const [x0, y0, x1, y1] = b.bbox || [0, 0, 0, 0]
          return (
            <button
              key={b.id}
              type="button"
              className="capture-block"
              title={b.text}
              style={{
                left: `${x0 * 100}%`,
                top: `${y0 * 100}%`,
                width: `${Math.max((x1 - x0) * 100, 0.4)}%`,
                height: `${Math.max((y1 - y0) * 100, 0.4)}%`,
              }}
              onClick={(e) => onBlockClick(b, e)}
              onPointerDown={(e) => e.stopPropagation()}
            />
          )
        })}
        {drag && (
          <div
            className="capture-drag"
            style={{
              left: `${Math.min(drag.x0, drag.x1) * 100}%`,
              top: `${Math.min(drag.y0, drag.y1) * 100}%`,
              width: `${Math.abs(drag.x1 - drag.x0) * 100}%`,
              height: `${Math.abs(drag.y1 - drag.y0) * 100}%`,
            }}
          />
        )}
      </div>

      <div className="capture-result mt-8">
        <label className="hint">取到的文字</label>
        <textarea
          className="field-input"
          rows={2}
          value={candidate}
          onChange={(e) => setCandidate(e.target.value)}
          placeholder="点选文本块或拖框后在此预览，可微调"
        />
        <div className="toolbar mt-8">
          <span className="tip-anchor" data-tip="把框选或点选的文字写入当前字段，记得再点保存本单。">
            <button
              type="button"
              className="btn primary"
              disabled={!candidate.trim()}
              onClick={() => {
                onApply(candidate.trim())
                onExit()
              }}
            >
            填入「{fieldLabel || fieldKey}」
          </button>
          </span>
          <button type="button" className="btn" onClick={() => setCandidate('')}>
            清空
          </button>
        </div>
      </div>
    </div>
  )
}
