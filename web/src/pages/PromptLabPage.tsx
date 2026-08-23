import { useEffect, useState } from 'react'
import { api } from '../api'
import type { PromptCatalog } from '../types'

export function PromptLabPage() {
  const [data, setData] = useState<PromptCatalog | null>(null)
  const [err, setErr] = useState('')
  const [pick, setPick] = useState(0)

  useEffect(() => {
    api
      .prompts()
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [])

  if (err) {
    return (
      <div className="panel panel-fill">
        <div className="panel-body">
          <p className="err">{err}</p>
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="panel panel-fill">
        <div className="panel-body">加载提示词目录…</div>
      </div>
    )
  }

  const entries = data.entries || []
  const cur = entries[pick] || {}

  return (
    <div className="panel panel-fill">
      <div className="panel-head">
        <div>
          <h3>提示词工程</h3>
          <div className="hint">
            只读查看系统提示词，供调试与讲解，不参与审阅。版本 {String(data.prompt_version || '—')}
            {typeof data.wired_count === 'number' ? ` · 已接线 ${data.wired_count}` : ''}
            {typeof data.design_only_count === 'number' ? ` · 设计中 ${data.design_only_count}` : ''}
          </div>
        </div>
      </div>
      <div className="panel-body prompt-lab">
        <div className="prompt-list">
          {entries.map((e, i) => (
            <button
              key={String(e.task_type || i)}
              type="button"
              className={`doc-item${i === pick ? ' active' : ''}`}
              onClick={() => setPick(i)}
            >
              {String(e.title || e.task_type || `条目 ${i + 1}`)}
              <small>
                {e.wired ? (
                  <span className="badge ok">已接线</span>
                ) : (
                  <span className="badge pending">设计中</span>
                )}
              </small>
            </button>
          ))}
        </div>
        <div className="prompt-detail">
          <h4 className="section-title">{String(cur.title || cur.task_type || '提示词')}</h4>
          {cur.description ? <p className="hint">{String(cur.description)}</p> : null}
          {cur.when ? <p className="hint">何时调用：{String(cur.when)}</p> : null}
          {cur.env_flag ? <p className="hint">开关：{String(cur.env_flag)}</p> : null}
          {cur.affects_final ? <p className="hint">是否改终态：{String(cur.affects_final)}</p> : null}
          {data.system_prompt ? (
            <details open>
              <summary>共用 System 提示词</summary>
              <pre className="prompt-entry">{data.system_prompt}</pre>
            </details>
          ) : null}
          {cur.sample_user ? (
            <details open>
              <summary>样例 User 提示词</summary>
              <pre className="prompt-entry">{String(cur.sample_user)}</pre>
            </details>
          ) : null}
          {(data.principles || []).length > 0 ? (
            <details>
              <summary>原则</summary>
              <ul>
                {(data.principles || []).map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      </div>
    </div>
  )
}
