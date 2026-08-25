import type { ChainInfo } from '../api'
import { BusinessWarehouseRow } from './BusinessWarehouseRow'

type Props = {
  rows: ChainInfo[]
  busy?: boolean
  uploadingId?: string | null
  completeSetBusyId?: string | null
  uploadErrorById?: Record<string, string>
  onOpen: (row: ChainInfo) => void
  onUpload: (row: ChainInfo, files: File[]) => void | Promise<void>
  onCompleteSetChange?: (row: ChainInfo, next: boolean) => void | Promise<void>
}

export function BusinessUploadQueue({
  rows,
  busy = false,
  uploadingId = null,
  completeSetBusyId = null,
  uploadErrorById = {},
  onOpen,
  onUpload,
  onCompleteSetChange,
}: Props) {
  const pendingRows = rows.filter(
    (row) =>
      row.doc_count === 0 ||
      row.reason === 'missing_docs' ||
      row.reason === 'wait_docs' ||
      Boolean(row.missing_doc_types?.length) ||
      Boolean(row.missing_doc_labels?.length),
  )

  return (
    <section className="business-upload-queue">
      <h4>待处理业务（待上传或待补充凭证）</h4>
      <p className="hint">按业务上传时，系统会自动把所选文件绑定到该笔业务。</p>
      {pendingRows.length ? (
        <ul className="desk-sample-list" aria-label="待上传凭证业务">
          {pendingRows.map((row) => (
            <BusinessWarehouseRow
              key={row.chain_id}
              row={row}
              active={false}
              busy={busy}
              uploading={uploadingId === row.chain_id}
              completeSetBusy={completeSetBusyId === row.chain_id}
              uploadError={uploadErrorById[row.chain_id] || ''}
              mode="upload"
              onOpen={onOpen}
              onUpload={onUpload}
              onCompleteSetChange={onCompleteSetChange}
            />
          ))}
        </ul>
      ) : (
        <p className="preview-empty">当前没有待上传或待补充凭证的业务。</p>
      )}
    </section>
  )
}
