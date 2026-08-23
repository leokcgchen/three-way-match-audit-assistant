import { useEffect, useState } from 'react'
import { api } from '../api'

type Props = {
  jobId: string
  fileName: string
  highlightField?: string | null
  /** 草稿字段值（未保存也可定位） */
  highlightValue?: string | null
  /** 1-based page number from audit evidence. */
  page?: number
}

/**
 * 原件预览：默认用服务端渲染页图（避免 iframe 切 PDF 不刷新）；
 * 选中字段时再请求高亮图。fileName 变化必须立刻丢掉上一张图。
 */
export function DocPreview({ jobId, fileName, highlightField, highlightValue, page = 1 }: Props) {
  const [imgUrl, setImgUrl] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null

    // 同步清掉上一份单据的图，避免「列表已切到发票、中间仍显示合同」
    setImgUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return null
    })
    setErr('')
    setNote('')
    setLoading(true)

    const run = async () => {
      try {
        if (highlightField) {
          const url =
            api.highlightUrl(jobId, fileName, highlightField, highlightValue || undefined) +
            `&_=${Date.now()}`
          const res = await fetch(url)
          if (!res.ok) {
            let detail = res.statusText
            try {
              const body = await res.json()
              detail = body.detail || detail
            } catch {
              /* ignore */
            }
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
          }
          const ct = (res.headers.get('Content-Type') || '').toLowerCase()
          if (!ct.includes('image')) {
            throw new Error(`高亮接口返回非图片: ${ct || 'unknown'}`)
          }
          const encodedNote = res.headers.get('X-Highlight-Note')
          if (encodedNote) {
            try {
              setNote(decodeURIComponent(encodedNote))
            } catch {
              setNote(encodedNote)
            }
          }
          const blob = await res.blob()
          if (!blob.size) throw new Error('高亮图为空')
          objectUrl = URL.createObjectURL(blob)
        } else {
          // 无高亮：渲染首页（与取证同源），不依赖浏览器 PDF iframe 缓存
          const pageIndex = Math.max(0, page - 1)
          const { blob, meta } = await api.previewPage(jobId, fileName, pageIndex)
          if (!blob.size) throw new Error('预览图为空')
          objectUrl = URL.createObjectURL(blob)
          setNote(
            meta.page_count > 1
              ? `原件第 ${pageIndex + 1} / ${meta.page_count} 页（点字段可高亮定位）`
              : '原件预览（点字段可高亮定位）',
          )
        }
        if (cancelled) {
          if (objectUrl) URL.revokeObjectURL(objectUrl)
          return
        }
        setImgUrl(objectUrl)
      } catch (e) {
        if (!cancelled) {
          setImgUrl(null)
          setErr(e instanceof Error ? e.message : String(e))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void run()
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [jobId, fileName, highlightField, highlightValue, page])

  return (
    <div key={fileName} className="doc-preview">
      {loading && <p className="hint">正在加载预览…</p>}
      {note && <p className="hint">{note}</p>}
      {err && (
        <p className="err">
          预览失败：{err}
          <a
            className="btn compact a-as-btn"
            style={{ marginLeft: 8 }}
            href={api.fileUrl(jobId, fileName)}
            target="_blank"
            rel="noreferrer"
          >
            打开原件
          </a>
        </p>
      )}
      {imgUrl ? (
        <img src={imgUrl} alt={`preview ${fileName}`} className="preview-frame" />
      ) : (
        !loading && <p className="preview-empty">暂无预览</p>
      )}
    </div>
  )
}
