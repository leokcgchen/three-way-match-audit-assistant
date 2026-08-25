import { type DeskProgress, emptyDeskProgress } from '../lib/deskLights'

type Props = {
  progress: DeskProgress | null
  sampleTotal?: number
}

type Chip = {
  key: keyof Required<DeskProgress>
  label: string
  tip: string
  tone: 'muted' | 'warn' | 'err' | 'ok'
}

const CHIPS: Chip[] = [
  {
    key: 'docs_missing',
    label: '缺少凭证资料',
    tip: '还缺凭证或必要单据未齐的样本笔数。',
    tone: 'err',
  },
  {
    key: 'fields_missing',
    label: '缺少关键字段',
    tip: '单据已有，但关键字段未提取/未确认的样本笔数。',
    tone: 'err',
  },
  {
    key: 'match_exception',
    label: '匹配或测试异常',
    tip: '三单/截止测试未通过，且尚未在结论页人工确认的样本笔数。',
    tone: 'err',
  },
  {
    key: 'await_human',
    label: '等待人工判断',
    tip: '单据类型存疑、同页多金额等，需要审计师裁决的样本笔数。',
    tone: 'warn',
  },
  {
    key: 'fail_confirmed',
    label: '异常已确认',
    tip: '测试结论仍是「未通过」，但你已在结论页点过「确认为不通过」。工作流已收口，红灯保留表示仍有未通过结论，不是还没做完。',
    tone: 'ok',
  },
]

/** 顶栏铆钉：单行紧凑，各步骤都能看到。 */
export function MastheadProgress({ progress, sampleTotal }: Props) {
  const p = { ...emptyDeskProgress(), ...(progress || {}) }
  const total = sampleTotal ?? p.sample_total ?? 0
  const pending = Math.max(0, total - (p.done || 0))
  const hasJob = total > 0 || (progress != null && (p.sample_total || 0) > 0)

  if (!hasJob) {
    return (
      <div className="masthead-progress is-empty" aria-label="进度拆分">
        <span className="masthead-progress-kicker">进度</span>
        <span className="masthead-progress-empty">立笔后显示拆分</span>
      </div>
    )
  }

  return (
    <div
      className="masthead-progress"
      aria-label="进度拆分"
      data-tip="顶栏进度拆分（各步骤可见）。已完成=绿灯通过，或异常已人工确认收口。点各胶囊可看单项说明。"
    >
      <span className="masthead-progress-kicker">进度</span>
      <span
        className="masthead-progress-done"
        data-tip="已完成笔数 / 样本总数。待办=尚未收口的样本。"
      >
        <b>{p.done}</b>
        <i>/</i>
        {total}
        {pending > 0 ? <em>待{pending}</em> : null}
      </span>
      <span className="masthead-progress-sep" aria-hidden />
      <div className="masthead-progress-rail" role="list">
        {CHIPS.map((c) => {
          const n = Number(p[c.key] || 0)
          const active = n > 0
          return (
            <span
              key={c.key}
              role="listitem"
              className={`masthead-progress-chip tone-${c.tone}${active ? ' is-on' : ''}`}
              data-tip={c.tip}
            >
              {c.label} <b>{n}</b>
            </span>
          )
        })}
      </div>
    </div>
  )
}
