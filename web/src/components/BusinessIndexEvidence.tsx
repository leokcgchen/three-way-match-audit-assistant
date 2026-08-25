import type { ClassifiedDoc } from '../types'

const SOURCE_LABELS: Record<string, string> = {
  filename: '文件名',
  manual: '人工指定',
}

const TYPE_LABELS: Record<string, string> = {
  business_id: '业务编号',
  order_number: '订单号',
}

type Props = {
  document: ClassifiedDoc
}

function sourceLabel(source: string): string {
  if (SOURCE_LABELS[source]) return SOURCE_LABELS[source]
  if (source.startsWith('ocr_field:')) return `OCR 字段 ${source.slice('ocr_field:'.length)}`
  return source || '未记录'
}

export function BusinessIndexEvidence({ document }: Props) {
  if (!document.sample_business_id) return null
  const evidence = document.business_index_evidence || []

  return (
    <div className="business-index-evidence">
      <span className="business-index-assigned">归入业务 {document.sample_business_id}</span>
      {evidence.length > 0 ? (
        <details>
          <summary>查看归类依据</summary>
          <ul>
            {evidence.map((item, index) => {
              const type = TYPE_LABELS[String(item.type || '')] || item.type || '索引'
              const source = sourceLabel(String(item.source || ''))
              return (
                <li key={`${item.type}-${item.detected}-${index}`}>
                  <strong>{source}中的{type}</strong>
                  <span>{item.detected || item.matched || '未记录'}</span>
                  <span>规范化后精确匹配</span>
                </li>
              )
            })}
          </ul>
        </details>
      ) : null}
    </div>
  )
}
