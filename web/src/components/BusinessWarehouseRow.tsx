import { useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import type { ChainInfo } from '../api'

export const ACCEPTED_EVIDENCE = '.pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff'

type Props = {
  row: ChainInfo
  active: boolean
  busy?: boolean
  uploading?: boolean
  uploadError?: string
  onOpen: (row: ChainInfo) => void
  onUpload: (row: ChainInfo, files: File[]) => void | Promise<void>
}

function lightClass(light?: string): string {
  if (light === 'green') return 'is-green'
  if (light === 'red') return 'is-red'
  if (light === 'yellow') return 'is-yellow'
  return 'is-wait'
}

function hasFiles(event: DragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer.types || []).includes('Files')
}

export function BusinessWarehouseRow({
  row,
  active,
  busy = false,
  uploading = false,
  uploadError = '',
  onOpen,
  onUpload,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)
  const [dragging, setDragging] = useState(false)
  const [dragCount, setDragCount] = useState(0)
  const present = (row.present_labels || []).join('、') || '尚未从识别内容判定'
  const missDocs = (row.missing_doc_labels || []).join('、')
  const uncDocs = (row.uncertain_doc_labels || []).join('、')
  const missFields =
    row.reason === 'fields_gap' || row.reason === 'amount_ambiguity'
      ? (row.missing_labels || []).join('、')
      : ''
  const diff = (row.diff_lines || [])[0]
  const disabled = busy || uploading

  const uploadFiles = (files: File[]) => {
    if (disabled || files.length === 0) return
    void onUpload(row, files)
  }

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    uploadFiles(Array.from(event.target.files || []))
    event.target.value = ''
  }

  const handleDragEnter = (event: DragEvent<HTMLLIElement>) => {
    if (disabled || !hasFiles(event)) return
    event.preventDefault()
    dragDepth.current += 1
    setDragCount(event.dataTransfer.files?.length || 0)
    setDragging(true)
  }

  const handleDragOver = (event: DragEvent<HTMLLIElement>) => {
    if (disabled || !hasFiles(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }

  const handleDragLeave = (event: DragEvent<HTMLLIElement>) => {
    if (!hasFiles(event)) return
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) {
      setDragging(false)
      setDragCount(0)
    }
  }

  const handleDrop = (event: DragEvent<HTMLLIElement>) => {
    if (disabled || !hasFiles(event)) return
    event.preventDefault()
    dragDepth.current = 0
    setDragging(false)
    setDragCount(0)
    uploadFiles(Array.from(event.dataTransfer.files || []))
  }

  return (
    <li
      className={`desk-sample-row desk-sample-row-stack business-warehouse-row${active ? ' is-on' : ''}${dragging ? ' is-dragging' : ''}`}
      aria-busy={uploading}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <button
        type="button"
        className="business-warehouse-main"
        disabled={busy}
        aria-label={`打开业务 ${row.chain_id}`}
        onClick={() => onOpen(row)}
      >
        <span className={`desk-sample-light ${lightClass(row.light)}`} aria-hidden />
        <span className="desk-sample-body">
          <span className="desk-sample-id-line">
            <span className="desk-sample-id">{row.chain_id}</span>
            {row.reason === 'fail_closed' ? <span className="desk-sample-ack">已人工确认</span> : null}
          </span>
          <span className="desk-sample-meta">已识别：{present}</span>
          <span className={missDocs ? 'desk-sample-miss' : 'desk-sample-meta'}>
            缺单据：{missDocs || '无'}
          </span>
          {uncDocs ? <span className="desk-sample-warn">类型存疑：{uncDocs}</span> : null}
          {missFields ? <span className="desk-sample-miss">缺字段：{missFields}</span> : null}
          {row.reason === 'amount_ambiguity' ? (
            <span className="desk-sample-miss">多金额待确认</span>
          ) : null}
          {row.reason === 'test_fail' || row.reason === 'fail_closed' ? (
            <span className="desk-sample-miss">{diff || '测试未通过'}</span>
          ) : null}
          {uploadError ? <span role="alert" className="business-warehouse-error">{uploadError}</span> : null}
        </span>
      </button>

      <div className="business-warehouse-upload">
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          accept={ACCEPTED_EVIDENCE}
          aria-label={`为业务 ${row.chain_id} 选择凭证`}
          onChange={handleChange}
        />
        <button
          type="button"
          className="btn business-upload-button"
          disabled={disabled}
          aria-label={
            uploading ? `正在为业务 ${row.chain_id} 上传凭证` : `为业务 ${row.chain_id} 上传凭证`
          }
          onClick={() => inputRef.current?.click()}
        >
          {uploading ? '上传中…' : '请上传'}
        </button>
        <span className="business-upload-hint">或拖到本行</span>
      </div>

      {dragging ? (
        <span className="business-drop-cue" role="status">
          将 {dragCount || 1} 个文件关联到业务 {row.chain_id}
        </span>
      ) : null}
    </li>
  )
}
