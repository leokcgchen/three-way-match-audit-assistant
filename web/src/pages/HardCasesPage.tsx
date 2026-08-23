import { useMemo, useState } from 'react'
import {
  HARD_CASE_CATALOG,
  hardCaseStatusLabel,
  type HardCase,
} from '../lib/hardCaseCatalog'

function StatusBadge({ status }: { status: HardCase['status'] }) {
  const cls =
    status === 'landed' ? 'ok' : status === 'partial' ? 'pending' : 'pending'
  return <span className={`badge ${cls}`}>{hardCaseStatusLabel(status)}</span>
}

export function HardCasesPage() {
  const [pick, setPick] = useState(0)
  const cases = HARD_CASE_CATALOG
  const cur = cases[pick] || cases[0]

  const intro = useMemo(
    () =>
      '这里收录识别与审阅链路里已经动手解决过的「实务疑难」。方便演示时讲清：痛点是什么、我们怎么拆、现在能做到哪一步、刻意不做什么。新攻克一类就追加；已有条目若做法或边界有变，同步改对应内容。',
    [],
  )

  if (!cur) {
    return (
      <div className="panel panel-fill">
        <div className="panel-body">
          <p className="hint">暂无条目。在 hardCaseCatalog.ts 追加即可。</p>
        </div>
      </div>
    )
  }

  return (
    <div className="panel panel-fill">
      <div className="panel-head">
        <div>
          <h3>识难录</h3>
          <div className="hint">{intro}</div>
        </div>
        <div className="hint">条目 {cases.length}</div>
      </div>
      <div className="panel-body prompt-lab hard-cases">
        <div className="prompt-list">
          {cases.map((c, i) => (
            <button
              key={c.id}
              type="button"
              className={`doc-item${i === pick ? ' active' : ''}`}
              onClick={() => setPick(i)}
            >
              {c.title}
              <small>
                <StatusBadge status={c.status} />
                <span className="hint"> · {c.updated}</span>
              </small>
            </button>
          ))}
        </div>
        <div className="prompt-detail hard-case-detail">
          <div className="hard-case-hero">
            <span className="eyebrow">实务疑难</span>
            <h4 className="section-title">{cur.title}</h4>
            <p className="hard-case-sub">{cur.subtitle}</p>
            <div className="hard-case-meta">
              <StatusBadge status={cur.status} />
              <span className="hint">更新 {cur.updated}</span>
              <span className="hint">id · {cur.id}</span>
            </div>
          </div>

          <section className="hard-case-block">
            <h5>一句话痛点</h5>
            <p>{cur.inPractice}</p>
          </section>

          <section className="hard-case-block">
            <h5>为什么难</h5>
            <ul>
              {cur.whyHard.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </section>

          <section className="hard-case-block">
            <h5>我们的思路</h5>
            <ol className="hard-case-steps">
              {cur.approach.map((x, i) => (
                <li key={x}>
                  <span className="hard-case-step-idx">{String(i + 1).padStart(2, '0')}</span>
                  <span>{x}</span>
                </li>
              ))}
            </ol>
          </section>

          <section className="hard-case-block accent">
            <h5>目前能做到</h5>
            <ul>
              {cur.canDo.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </section>

          <section className="hard-case-block muted">
            <h5>刻意边界</h5>
            <ul>
              {cur.boundaries.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </section>

          <section className="hard-case-block">
            <h5>演示时怎么讲 / 怎么点</h5>
            <ul>
              {cur.howToShow.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </div>
  )
}
