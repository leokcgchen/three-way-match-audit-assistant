import { useState } from 'react'
import type { MouseEvent } from 'react'
import type { PacketFile, PacketUnit } from '../types'

type Props = {
  files: PacketFile[]
  units: PacketUnit[]
  selectedUnitIds: string[]
  thumbnails: Record<string, string>
  locked?: boolean
  blankPageKeys?: Set<string>
  onSelectionChange: (ids: string[]) => void
  onPageFocus: (sourceFile: string, page: number, unitId: string) => void
  onSplit: (unitId: string, page: number) => void
  onMerge: (unitId: string) => void
  onDropPage: (sourceFile: string, page: number) => void
  onRestoreUnit: (unitId: string) => void
  onOpenOriginal: (sourceFile: string) => void
}

const TYPE_LABELS: Record<string, string> = {
  contract: '合同',
  order: '订单',
  delivery: '发货单',
  receipt: '签收/验收',
  invoice: '发票',
  payment: '回款',
  unresolved: '未识别',
  other: '未识别',
}

function typeLabel(unit: PacketUnit): string {
  return TYPE_LABELS[unit.doc_type] || TYPE_LABELS[unit.card_type || ''] || unit.doc_type || '未识别'
}

function pageRange(pages: number[]): string {
  if (pages.length === 1) return `第${pages[0]}页`
  return `第${pages[0]}–${pages[pages.length - 1]}页`
}

export function PacketContactSheet({
  files,
  units,
  selectedUnitIds,
  thumbnails,
  locked = false,
  blankPageKeys = new Set<string>(),
  onSelectionChange,
  onPageFocus,
  onSplit,
  onMerge,
  onDropPage,
  onRestoreUnit,
  onOpenOriginal,
}: Props) {
  const [anchor, setAnchor] = useState<{ sourceFile: string; page: number } | null>(null)
  const [focusedPage, setFocusedPage] = useState<{ sourceFile: string; page: number } | null>(null)

  const selectPage = (event: MouseEvent, unit: PacketUnit, page: number) => {
    const orderedIds = units
      .filter((item) => !item.dropped && item.source_file === unit.source_file)
      .sort((a, b) => a.page_start - b.page_start)
      .map((item) => item.unit_id)
    let next: string[]
    if (event.shiftKey && anchor?.sourceFile === unit.source_file) {
      const start = Math.min(anchor.page, page)
      const end = Math.max(anchor.page, page)
      const rangeIds = units
        .filter(
          (item) =>
            !item.dropped &&
            item.source_file === unit.source_file &&
            item.pages.some((value) => value >= start && value <= end),
        )
        .sort((a, b) => a.page_start - b.page_start)
        .map((item) => item.unit_id)
      next = orderedIds.filter((id) => selectedUnitIds.includes(id) || rangeIds.includes(id))
    } else if (event.ctrlKey || event.metaKey) {
      next = selectedUnitIds.includes(unit.unit_id)
        ? selectedUnitIds.filter((id) => id !== unit.unit_id)
        : [...selectedUnitIds, unit.unit_id]
      setAnchor({ sourceFile: unit.source_file, page })
    } else {
      next = [unit.unit_id]
      setAnchor({ sourceFile: unit.source_file, page })
    }
    setFocusedPage({ sourceFile: unit.source_file, page })
    onSelectionChange(next)
    onPageFocus(unit.source_file, page, unit.unit_id)
  }

  return (
    <div className="packet-contact-sheet" aria-label="多文件拆包联络表">
      {files.map((file, fileIndex) => {
        const fileUnits = units
          .filter((unit) => unit.source_file === file.file_name)
          .sort((a, b) => a.page_start - b.page_start)
        return (
          <section
            key={file.file_name}
            id={`packet-contact-file-${fileIndex}`}
            className="packet-contact-file"
            role="region"
            aria-label={`${file.file_name}，${file.page_count || 0} 页`}
          >
            <header className="packet-contact-file-head">
              <div>
                <strong>{file.file_name}</strong>
                <span>{file.page_count || '?'} 页</span>
              </div>
              <button
                type="button"
                className="btn compact"
                aria-label={`打开 ${file.file_name} 原件`}
                onClick={() => onOpenOriginal(file.file_name)}
              >
                打开原件
              </button>
            </header>

            <div className="packet-contact-units">
              {fileUnits.map((unit) => {
                const selected = selectedUnitIds.includes(unit.unit_id)
                return (
                  <article
                    key={unit.unit_id}
                    className={`packet-contact-unit${selected ? ' is-selected' : ''}${unit.needs_review ? ' is-review' : ''}${unit.dropped ? ' is-dropped' : ''}`}
                    aria-label={`${typeLabel(unit)}，${pageRange(unit.pages)}`}
                    style={{ gridColumn: `span ${Math.min(Math.max(unit.pages.length, 1), 3)}` }}
                  >
                    <div className="packet-contact-boundary">
                      <strong>{typeLabel(unit)} · {pageRange(unit.pages)}</strong>
                      <span>
                        {unit.dropped
                          ? '已去掉'
                          : unit.boundary_confirmed
                            ? '边界已确认'
                            : unit.needs_review
                              ? '异常待复核'
                              : '待确认'}
                      </span>
                    </div>
                    <div className="packet-contact-pages">
                      {unit.pages.map((page) => {
                        const key = `${unit.source_file}:${page}`
                        const thumbnail = thumbnails[key]
                        const focused = focusedPage?.sourceFile === unit.source_file && focusedPage.page === page
                        return (
                          <div key={page} className={`packet-contact-page${focused ? ' is-focused' : ''}`}>
                            <button
                              type="button"
                              className="packet-contact-page-select"
                              aria-label={`选择 ${unit.source_file} 第 ${page} 页`}
                              aria-pressed={selected}
                              disabled={locked}
                              onClick={(event) => selectPage(event, unit, page)}
                            >
                              {thumbnail ? (
                                <img src={thumbnail} alt={`${unit.source_file} 第 ${page} 页缩略图`} />
                              ) : (
                                <span className="packet-contact-placeholder">
                                  <b>第 {page} 页</b>
                                  <span>缩略图不可用</span>
                                </span>
                              )}
                              <span className="packet-contact-page-label">
                                第 {page} 页
                                {blankPageKeys.has(key) ? ' · 疑似空白' : ''}
                              </span>
                            </button>
                            {selected && focused ? (
                              <div className="packet-contact-actions" aria-label={`第 ${page} 页操作`}>
                                <button
                                  type="button"
                                  className="btn compact"
                                  disabled={locked || page === unit.pages[0]}
                                  aria-label={`从第 ${page} 页拆开`}
                                  onClick={() => onSplit(unit.unit_id, page)}
                                >
                                  从本页拆开
                                </button>
                                <button
                                  type="button"
                                  className="btn compact"
                                  disabled={locked}
                                  aria-label="将当前单据并入上一张"
                                  onClick={() => onMerge(unit.unit_id)}
                                >
                                  并入上一张
                                </button>
                                <button
                                  type="button"
                                  className="btn compact"
                                  disabled={locked}
                                  aria-label={`去掉 ${unit.source_file} 第 ${page} 页`}
                                  onClick={() => onDropPage(unit.source_file, page)}
                                >
                                  去掉这页
                                </button>
                              </div>
                            ) : null}
                          </div>
                        )
                      })}
                    </div>
                    {unit.dropped ? (
                      <button
                        type="button"
                        className="btn compact packet-contact-restore"
                        disabled={locked}
                        aria-label={`恢复已去掉单据 ${pageRange(unit.pages)}`}
                        onClick={() => onRestoreUnit(unit.unit_id)}
                      >
                        恢复这张
                      </button>
                    ) : null}
                  </article>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}
