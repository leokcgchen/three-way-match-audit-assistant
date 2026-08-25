import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { Job, WorkpaperGoal } from '../types'

type Props = {
  job: Job
  onJob: (job: Job) => void
}

const CAL_MODES: Array<{ id: string; label: string }> = [
  { id: 'natural_month', label: '自然月（默认）' },
  { id: 'fiscal_445', label: '4-4-5 财年周' },
  { id: 'period_end_only', label: '仅报告期末边界' },
]

export function GoalsPage({ job, onJob }: Props) {
  const bodyRef = useRef<HTMLDivElement>(null)
  const [goals, setGoals] = useState<WorkpaperGoal[]>([])
  const [selected, setSelected] = useState<string[]>(job.goal_ids || [])
  const [periodEnd, setPeriodEnd] = useState(String(job.period_end || ''))
  const [entityName, setEntityName] = useState(String(job.entity_name || ''))
  const [calendarMode, setCalendarMode] = useState(
    String(job.calendar_mode || 'natural_month'),
  )
  const [fiscalYearStart, setFiscalYearStart] = useState(
    String(job.fiscal_year_start || ''),
  )
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.listGoals().then((r) => setGoals(r.goals)).catch((e) => setErr(String(e.message || e)))
  }, [])

  useEffect(() => {
    setSelected(job.goal_ids || [])
    setPeriodEnd(String(job.period_end || ''))
    setEntityName(String(job.entity_name || ''))
    setCalendarMode(String(job.calendar_mode || 'natural_month'))
    setFiscalYearStart(String(job.fiscal_year_start || ''))
  }, [
    job.job_id,
    job.goal_ids,
    job.period_end,
    job.entity_name,
    job.calendar_mode,
    job.fiscal_year_start,
  ])

  useLayoutEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = 0
  }, [job.job_id])

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  const needsCutoff = useMemo(
    () => selected.includes('gospd01030') || selected.includes('gospd01010'),
    [selected],
  )

  const apply = async () => {
    setBusy(true)
    setErr('')
    try {
      const next = await api.setGoals(job.job_id, selected, {
        period_end: periodEnd.trim() || undefined,
        entity_name: entityName.trim() || undefined,
        calendar_mode: calendarMode.trim() || 'natural_month',
        fiscal_year_start:
          calendarMode === 'fiscal_445'
            ? fiscalYearStart.trim() || undefined
            : undefined,
      })
      onJob(next)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel panel-fill">
      <div className="panel-head">
        <div>
          <h3>选择底稿目标</h3>
          <div className="hint">
            只选底稿目标并填写项目参数。确认后进入独立的抽样清单上传页面。
          </div>
        </div>
        <div className="toolbar">
          <span
            className="tip-anchor"
            data-tip={
              selected.length === 0
                ? '请先勾选至少一个底稿目标。'
                : '确认目标后进入抽样清单上传页面。'
            }
          >
            <button className="btn primary" disabled={busy || selected.length === 0} onClick={apply}>
              确认目标，并进入抽样清单上传
            </button>
          </span>
        </div>
      </div>
      <div className="panel-body" ref={bodyRef}>
        <div className="goal-section-head">
          <strong>底稿目标选项</strong>
          <span>可多选，请勾选本次需要填写和导出的全部底稿。</span>
        </div>
        <div className="goal-grid">
          {goals.map((g) => {
            const on = selected.includes(g.goal_id)
            return (
              <button
                key={g.goal_id}
                type="button"
                className={`goal-card${on ? ' on' : ''}`}
                onClick={() => toggle(g.goal_id)}
              >
                <div className="goal-mark">{on ? '已选' : '可选'}</div>
                <strong>{g.label}</strong>
                <span>{g.description}</span>
              </button>
            )
          })}
        </div>

        <div className="plan-box mt-8">
          <strong>项目参数</strong>
          <div className="toolbar mt-8" style={{ flexWrap: 'wrap', gap: 12 }}>
            <label className="hint">
              被审计单位
              <input
                className="rel-reason"
                style={{ display: 'block', minWidth: 220, marginTop: 4 }}
                placeholder="写入底稿表头"
                value={entityName}
                onChange={(e) => setEntityName(e.target.value)}
              />
            </label>
            <label className="hint">
              期间截止日{needsCutoff ? '（截止底稿必填）' : ''}
              <input
                type="date"
                className="rel-reason"
                style={{ display: 'block', minWidth: 180, marginTop: 4 }}
                value={periodEnd}
                onChange={(e) => setPeriodEnd(e.target.value)}
              />
            </label>
            <label className="hint">
              会计日历
              <select
                className="rel-reason"
                style={{ display: 'block', minWidth: 180, marginTop: 4 }}
                value={calendarMode}
                onChange={(e) => setCalendarMode(e.target.value)}
              >
                {CAL_MODES.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
            {calendarMode === 'fiscal_445' && (
              <label className="hint">
                财年起点（4-4-5）
                <input
                  type="date"
                  className="rel-reason"
                  style={{ display: 'block', minWidth: 180, marginTop: 4 }}
                  value={fiscalYearStart}
                  onChange={(e) => setFiscalYearStart(e.target.value)}
                />
              </label>
            )}
          </div>
          <div className="hint mt-8">
            截止测试要用期间截止日；不填则该测试记为未测。改日历或期末会作废三单与结论，需重跑。
          </div>
        </div>

        {err && <p className="err">{err}</p>}
      </div>
    </div>
  )
}
