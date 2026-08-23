import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { JOURNEY_STEPS, journeyLine, skipStepLabel } from '../lib/userJourney'
import type { Job, WorkpaperGoal, WorkflowPlan } from '../types'

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
  const [preview, setPreview] = useState<WorkflowPlan | null>(null)
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

  useEffect(() => {
    let cancelled = false
    api
      .previewPlan(selected)
      .then((p) => {
        if (!cancelled) setPreview(p)
      })
      .catch(() => {
        if (!cancelled) setPreview(null)
      })
    return () => {
      cancelled = true
    }
  }, [selected])

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
            只选底稿目标和项目参数。抽样清单、凭证、审阅都在工作台完成。
          </div>
        </div>
        <div className="toolbar">
          <span
            className="tip-anchor"
            data-tip={
              selected.length === 0
                ? '请先勾选至少一个底稿目标。'
                : '确认目标后进入工作台，在那里上传抽样清单。'
            }
          >
            <button className="btn primary" disabled={busy || selected.length === 0} onClick={apply}>
              确认目标并进入工作台
            </button>
          </span>
        </div>
      </div>
      <div className="panel-body">
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

        {preview && selected.length > 0 && (
          <div className="plan-box">
            <div>{journeyLine(selected)}</div>
            <div className="mt-8">
              <strong>工作台主路径：</strong> {JOURNEY_STEPS.join(' → ')}
            </div>
            <div className="mt-8">
              <strong>导出 sheet：</strong>{' '}
              {(preview.workbook_sheets || []).join('、') || '—'}
            </div>
            {preview.skipped_steps?.length > 0 && (
              <div className="mt-8 hint">
                本目标不强制：
                {preview.skipped_steps
                  .filter((s) => s === 'contract_terms' || s === 'amount_test')
                  .map(skipStepLabel)
                  .join('、') || '无'}
              </div>
            )}
          </div>
        )}
        {err && <p className="err">{err}</p>}
      </div>
    </div>
  )
}
