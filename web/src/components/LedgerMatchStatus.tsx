import type { ClassifiedDoc } from '../types'

const SOURCE_LABELS: Record<string, string> = {
  manual: '人工指定',
  filename: '文件名',
  ocr_business_field: 'OCR 业务编号字段',
  sample_key_field: '与抽样清单一致的单据字段',
  legacy_document_key: '历史单据索引字段',
}

type Props = {
  document: ClassifiedDoc
}

export function LedgerMatchStatus({ document }: Props) {
  if (!document.ledger_evaluated) {
    return <span className="badge ok">已识别</span>
  }

  if (document.ledger_match_ok) {
    return (
      <div className="ledger-match-success" role="status">
        <span className="badge ok">
          已匹配序时账 · {document.ledger_matched_biz_id || document.ledger_query_biz_id || '业务编号待核'}
        </span>
        {document.ledger_posting_date && (
          <span className="ledger-match-date">入账日期 {document.ledger_posting_date}</span>
        )}
      </div>
    )
  }

  const reason = document.ledger_match_reason
  const queryValue = reason?.query_value || document.ledger_query_biz_id || document.sample_business_id
  const source = reason?.document_index_source || document.business_index_source
  const indexColumn = reason?.ledger_index_column || document.ledger_index_column
  const missingIndex = reason?.code === 'MISSING_DOCUMENT_INDEX' || !queryValue
  const summary = missingIndex
    ? '无法关联：未取得抽样业务编号'
    : `未匹配：业务编号 ${queryValue}`

  return (
    <details className="ledger-match-status">
      <summary>
        <span className="badge warn">{summary}</span>
      </summary>
      <dl className="ledger-match-explanation">
        <div>
          <dt>凭证索引</dt>
          <dd>{reason?.document_index || document.sample_business_id || '未取得'}</dd>
        </div>
        <div>
          <dt>索引来源</dt>
          <dd>{SOURCE_LABELS[String(source || '')] || source || '未识别'}</dd>
        </div>
        <div>
          <dt>序时账索引列</dt>
          <dd>{indexColumn || '未映射'}</dd>
        </div>
        <div>
          <dt>实际查询值</dt>
          <dd>{queryValue || '未执行查询'}</dd>
        </div>
      </dl>
      <p className="ledger-match-reason">
        {reason?.message || document.ledger_match_message || '当前凭证未能与序时账建立业务关联。'}
      </p>
    </details>
  )
}
