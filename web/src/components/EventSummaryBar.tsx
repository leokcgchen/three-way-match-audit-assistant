import type { ReviewEventSummary } from '../types'

type Props = {
  summary: ReviewEventSummary
  busy?: boolean
  onPrimary: () => void
}

export function EventSummaryBar({ summary, busy = false, onPrimary }: Props) {
  const primaryLabel = summary.open > 0 ? `处理 ${summary.open} 个异常` : '检查并导出'
  return (
    <section className="event-summary-bar" aria-label="审阅事件汇总">
      <div className="event-summary-metrics">
        <strong>待处理 {summary.open}</strong>
        <span className={summary.blocking > 0 ? 'is-blocking' : ''}>阻断 {summary.blocking}</span>
        <span>缺件 {summary.missing}</span>
        <span>人工判断 {summary.review}</span>
        <span>自动通过 {summary.passed}</span>
      </div>
      <p>
        {summary.open > 0
          ? '正常项已自动收起，只需处理下面的例外。'
          : '当前没有待裁决事项，可以进入最终检查。'}
      </p>
      <button
        type="button"
        className={`btn${summary.open > 0 ? ' primary' : ''} event-summary-primary`}
        disabled={busy}
        onClick={onPrimary}
      >
        {busy ? '处理中…' : primaryLabel}
      </button>
    </section>
  )
}
