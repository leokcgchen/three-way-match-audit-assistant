import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { FieldCatalog, FieldPlan, Job } from '../types'

const TYPE_LABELS: Record<string, string> = {
  contract: '合同',
  order: '订单',
  delivery: '发货单',
  receipt: '签收/验收',
  invoice: '发票',
  payment: '回款',
  other: '其他',
}

type Props = {
  job: Job
  onJob: (j: Job) => void
  locked?: boolean
}

export function FieldPlanPanel({ job, onJob, locked = false }: Props) {
  const [catalog, setCatalog] = useState<FieldCatalog | null>(null)
  const [tab, setTab] = useState('contract')
  const [customDraft, setCustomDraft] = useState('')
  const [globalDraft, setGlobalDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const plan = job.field_plan
  const pending = job.pending_files || []
  const classified = job.classified || []
  const presentTypes = useMemo(() => {
    const s = new Set<string>()
    for (const p of pending) {
      if (p.doc_type) s.add(p.doc_type)
    }
    for (const d of classified) {
      if (d.doc_type) s.add(d.doc_type)
    }
    return [...s]
  }, [pending, classified])

  useEffect(() => {
    api.fieldCatalog().then(setCatalog).catch(() => setCatalog(null))
  }, [])

  useEffect(() => {
    if (presentTypes.length && !presentTypes.includes(tab)) {
      setTab(presentTypes[0])
    }
  }, [presentTypes, tab])

  const savePlan = async (next: FieldPlan, confirm = false) => {
    setBusy(true)
    setErr('')
    try {
      onJob(await api.putFieldPlan(job.job_id, next, { confirm }))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!plan) {
    return (
      <div className="field-plan-box mb-14">
        <p className="hint">字段清单未初始化。</p>
        <span className="tip-anchor" data-tip="生成本次 OCR 要抽取的字段列表。">
        <button
          type="button"
          className="btn"
          disabled={locked || busy}
          onClick={() =>
            void savePlan(
              {
                confirmed: false,
                global_extra: [],
                by_type: {},
              },
              false,
            )
          }
        >
          初始化字段清单
        </button>
        </span>
        {err && <p className="err mt-8">{err}</p>}
      </div>
    )
  }

  const slot = plan.by_type?.[tab]
  const catSlot = catalog?.by_type?.[tab]
  const selected = new Set(slot?.selected_optional || [])
  const custom = slot?.custom || []
  const globalExtra = plan.global_extra || []

  const toggleOptional = (key: string) => {
    if (!plan || locked || busy) return
    const byType = { ...(plan.by_type || {}) }
    const cur = {
      system_required: [...(byType[tab]?.system_required || [])],
      selected_optional: [...(byType[tab]?.selected_optional || [])],
      custom: [...(byType[tab]?.custom || [])],
    }
    if (cur.selected_optional.includes(key)) {
      cur.selected_optional = cur.selected_optional.filter((k) => k !== key)
    } else {
      cur.selected_optional = [...cur.selected_optional, key]
    }
    byType[tab] = cur
    void savePlan({ ...plan, by_type: byType, confirmed: false })
  }

  const addCustom = () => {
    const key = customDraft.trim()
    if (!key || !plan) return
    const byType = { ...(plan.by_type || {}) }
    const cur = {
      system_required: [...(byType[tab]?.system_required || [])],
      selected_optional: [...(byType[tab]?.selected_optional || [])],
      custom: [...(byType[tab]?.custom || [])],
    }
    if (!cur.custom.includes(key) && !cur.system_required.includes(key)) {
      cur.custom = [...cur.custom, key]
    }
    byType[tab] = cur
    setCustomDraft('')
    void savePlan({ ...plan, by_type: byType, confirmed: false })
  }

  const addGlobal = () => {
    const key = globalDraft.trim()
    if (!key || !plan) return
    const ge = [...(plan.global_extra || [])]
    if (!ge.includes(key)) ge.push(key)
    setGlobalDraft('')
    void savePlan({ ...plan, global_extra: ge, confirmed: false })
  }

  const tabs = presentTypes.length ? presentTypes : Object.keys(TYPE_LABELS)

  return (
    <div className="field-plan-box mb-14">
      <div className="toolbar between">
        <div>
          <h4 className="section-title" style={{ margin: 0 }}>
            加抽字段（可选）
          </h4>
          <div className="hint">
            必填已由底稿目标自动确定。这里只加额外要抽的键，一般不用改。
            {plan?.confirmed ? (
              <span className="badge ok ml-8">已生效</span>
            ) : (
              <span className="badge pending ml-8">未保存</span>
            )}
          </div>
        </div>
        <span className="tip-anchor" data-tip="保存加抽字段。主路径识别不依赖这一步。">
        <button
          type="button"
          className="btn primary"
          disabled={locked || busy || !plan}
          onClick={() => plan && void savePlan(plan, true)}
        >
          {busy ? '保存中…' : '保存加抽字段'}
        </button>
        </span>
      </div>

      <div className="field-plan-tabs">
        {tabs.map((t) => (
          <button
            key={t}
            type="button"
            className={`btn compact${tab === t ? ' primary' : ''}`}
            onClick={() => setTab(t)}
          >
            {TYPE_LABELS[t] || t}
          </button>
        ))}
      </div>

      <div className="hint mt-8 mb-8">系统必用（不可取消）</div>
      <div className="chip-row">
        {(catSlot?.system_required || slot?.system_required || []).map((f) => {
          const key = typeof f === 'string' ? f : f.key
          const label = typeof f === 'string' ? key : f.label
          return (
            <span key={key} className="chip locked" title={key}>
              {label}
            </span>
          )
        })}
      </div>

      <div className="hint mt-12 mb-8">本类型可选</div>
      <div className="chip-row">
        {(catSlot?.optional || []).map((f) => (
          <label key={f.key} className={`chip check${selected.has(f.key) ? ' on' : ''}`}>
            <input
              type="checkbox"
              checked={selected.has(f.key)}
              disabled={locked || busy}
              onChange={() => toggleOptional(f.key)}
            />
            {f.label}
          </label>
        ))}
        {custom.map((k) => (
          <span key={k} className="chip on" title="自定义">
            {k}
          </span>
        ))}
      </div>
      <div className="toolbar mt-8">
        <input
          className="field-input"
          placeholder="为本类型追加自定义字段名（如 warrantyMonths）"
          value={customDraft}
          disabled={locked || busy}
          onChange={(e) => setCustomDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              addCustom()
            }
          }}
        />
        <span className="tip-anchor" data-tip="只给当前单据类型多抽这个字段。">
        <button
          type="button"
          className="btn"
          disabled={locked || busy || !customDraft.trim()}
          onClick={addCustom}
        >
          追加到本类型
        </button>
        </span>
      </div>

      <div className="hint mt-12 mb-8">全局附加字段</div>
      <div className="chip-row">
        {globalExtra.length ? (
          globalExtra.map((k) => (
            <span key={k} className="chip on">
              {k}
            </span>
          ))
        ) : (
          <span className="hint">（无）</span>
        )}
      </div>
      <div className="toolbar mt-8">
        <input
          className="field-input"
          placeholder="全局附加字段名"
          value={globalDraft}
          disabled={locked || busy}
          onChange={(e) => setGlobalDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              addGlobal()
            }
          }}
        />
        <span className="tip-anchor" data-tip="所有单据类型都会多抽这个字段。">
        <button
          type="button"
          className="btn"
          disabled={locked || busy || !globalDraft.trim()}
          onClick={addGlobal}
        >
          追加全局
        </button>
        </span>
      </div>
      {err && <p className="err mt-8">{err}</p>}
    </div>
  )
}
