import { useEffect, useMemo, useState } from 'react'
import { api, type ChainInfo } from '../api'
import type { ConclusionFinding, ConclusionTrace, Job } from '../types'
import { ChainPicker } from '../components/ChainPicker'
import { ThreeWayDecisionCard } from '../components/ThreeWayDecisionCard'
import { CutoffEvidenceTable, ThreeWayEvidenceTable } from '../components/ConclusionEvidenceTable'
import { activeSample, isGospdJob } from '../lib/chainDocs'
import { useActiveChainFiles } from '../lib/useActiveChainFiles'
import { storeFieldTraceTarget, type FieldTraceTarget } from '../lib/fieldTraceNavigation'
import { resultOverallStatus } from '../lib/testStatus'
import { listChainsCached } from '../lib/chainsCache'
import { conclusionTraceCached } from '../lib/conclusionTraceCache'
import { pickThreeWayDecision, expandThreeWayShorthand } from '../lib/threeWayDecision'

type Props = { job: Job; onJob: (j: Job) => void; onGo: (step: string) => void }

const HOST_CN: Record<string, string> = {
  contract: '合同',
  order: '订单',
  delivery: '发货单',
  receipt: '签收/验收',
  invoice: '发票',
  payment: '回款',
}

const CMP_FIELD_CN: Record<string, string> = {
  supplier_name: '客户名称',
  supplier: '客户名称',
  total_amount: '价税合计',
  amount: '价税合计',
  quantity: '数量（订单 / 签收验收 / 发票开票）',
  qty: '数量（订单 / 签收验收 / 发票开票）',
}

const MODULE_META: Record<string, { title: string; hint: string; order: number }> = {
  three_way: {
    title: '三单匹配',
    hint: '核是否同一笔业务，再比对购方名称、价税合计，以及数量三角色：订单数量、签收/验收数量、发票开票数量。放行看硬规则。日期不要求同一天。',
    order: 1,
  },
  cutoff: {
    title: '截止性',
    hint: '用签收/控制权日与序时账入账日判断期间，不与开票日求同。',
    order: 2,
  },
}

function cmpFieldName(raw: unknown): string {
  const key = String(raw || '字段')
  return CMP_FIELD_CN[key] || CMP_FIELD_CN[key.toLowerCase()] || key
}

function cmpLine(c: Record<string, unknown>): string {
  const field = cmpFieldName(c.field_name || c.field)
  const isQty = ['quantity', 'qty'].includes(String(c.field_name || c.field || '').toLowerCase())
  const bits = [
    c.order_value != null && c.order_value !== ''
      ? `${isQty ? '订单数量' : '订单'} ${c.order_value}`
      : '',
    c.receipt_value != null && c.receipt_value !== ''
      ? `${isQty ? '签收/验收数量' : '签收/验收'} ${c.receipt_value}`
      : '',
    c.invoice_value != null && c.invoice_value !== ''
      ? `${isQty ? '发票开票数量' : '发票'} ${c.invoice_value}`
      : '',
  ].filter(Boolean)
  const explain = expandThreeWayShorthand(String(c.auditor_explain || c.message || c.diff_description || ''))
  if (bits.length >= 2) return `${field}：${bits.join('  vs  ')}${explain ? ` — ${explain}` : ''}`
  if (explain) return `${field}：${explain}`
  return field
}

function fieldLine(f: { doc_type?: string; field_label?: string; value?: unknown }): string {
  const doc = HOST_CN[String(f.doc_type || '')] || f.doc_type || '单据'
  return `${doc} · ${f.field_label || '字段'} = ${f.value ?? '空'}`
}

function moduleOf(f: ConclusionFinding): string {
  if (f.module) return f.module
  if (f.step === 'cutoff') return 'cutoff'
  if (f.step.startsWith('three')) return 'three_way'
  return f.step
}

function groupFindings(findings: ConclusionFinding[]) {
  const buckets = new Map<string, ConclusionFinding[]>()
  for (const f of findings) {
    const key = moduleOf(f)
    const arr = buckets.get(key) || []
    arr.push(f)
    buckets.set(key, arr)
  }
  return [...buckets.entries()]
    .sort((a, b) => (MODULE_META[a[0]]?.order ?? 99) - (MODULE_META[b[0]]?.order ?? 99))
    .map(([key, items]) => ({
      key,
      title: MODULE_META[key]?.title || items[0]?.step_label || key,
      hint: MODULE_META[key]?.hint || '',
      items,
    }))
}

function FailCard({ finding, job, chainFileNames, onTrace }: {
  finding: ConclusionFinding
  job: Job
  chainFileNames?: string[] | null
  onTrace: (target: FieldTraceTarget) => void
}) {
  const cmps = (finding.comparisons || []).filter((c) => {
    const st = String(c.status || '').toUpperCase()
    return st.includes('FAIL') || st.includes('WARN') || st.includes('不')
  })
  const periodRows = Object.entries(finding.period || {}).filter(
    ([, v]) => v != null && String(v).trim() !== '',
  )
  const showFields =
    cmps.length === 0 && periodRows.length === 0 && (finding.fields_used || []).length > 0
  const showDecision =
    finding.module === 'three_way' || String(finding.step || '').startsWith('three')
  const isCutoff = finding.module === 'cutoff' || finding.step === 'cutoff'
  return (
    <article className={`fail-card${isCutoff ? ' fail-card-cutoff' : ''}`}>
      {!isCutoff ? (
        <header className="fail-card-head">
          <strong>{finding.step_label || finding.title}</strong>
          <span className={`badge ${finding.blocking ? 'danger' : 'warn'}`}>{finding.status}</span>
        </header>
      ) : null}
      {showDecision && (finding.decision || finding.hold_reason_code) ? (
        <>
          <ThreeWayDecisionCard
            view={{
              decision: finding.decision,
              decision_reasons: finding.decision_reasons,
              hold_reason_code: finding.hold_reason_code,
              quantity_roles: finding.quantity_roles,
              slot_reasons: finding.slot_reasons,
              erp_review: finding.erp_review,
              fulfillment: finding.fulfillment,
              status: finding.status,
            }}
          />
          <ThreeWayEvidenceTable job={job} chainFileNames={chainFileNames} onTrace={onTrace} />
        </>
      ) : null}
      {isCutoff ? (
        <CutoffEvidenceTable finding={finding} jobId={job.job_id} chainId={job.active_chain_id || ''} onTrace={onTrace} />
      ) : (
        <section className="conclusion-language-reason">
          <h4>结论说明</h4>
          <p>{expandThreeWayShorthand(finding.summary || finding.title)}</p>
        </section>
      )}
      {cmps.length > 0 && (
        <section>
          <h4>什么数据没对上</h4>
          <ul>
            {cmps.map((c, i) => (
              <li key={i}>{cmpLine(c)}</li>
            ))}
          </ul>
        </section>
      )}
      {periodRows.length > 0 && finding.module !== 'cutoff' && finding.step !== 'cutoff' && (
        <section>
          <h4>期间判断</h4>
          <ul>
            {periodRows.map(([k, v]) => (
              <li key={k}>
                {k} = {String(v)}
              </li>
            ))}
          </ul>
        </section>
      )}
      {showFields && (
        <section>
          <h4>什么数据没对上</h4>
          <ul>
            {(finding.fields_used || []).map((x, i) => (
              <li key={i}>{fieldLine(x)}</li>
            ))}
          </ul>
        </section>
      )}
      {finding.method ? (
        <section>
          <h4>测试逻辑</h4>
          <p>{expandThreeWayShorthand(finding.method)}</p>
        </section>
      ) : null}
    </article>
  )
}

export function ConclusionPage({ job, onJob, onGo }: Props) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [trace, setTrace] = useState<ConclusionTrace | null>(null)
  const [traceLoading, setTraceLoading] = useState(true)
  const [deskRow, setDeskRow] = useState<ChainInfo | null>(null)
  const sample = isGospdJob(job) ? activeSample(job) : null
  const gate5Ok = Boolean(sample ? sample.conclusion_confirmed : job.conclusion_confirmed)
  const disposition = String(sample?.conclusion_disposition || '')
  const activeChain = job.active_chain_id || ''
  const { chainFileNames } = useActiveChainFiles(job)
  const decisionView = useMemo(
    () =>
      pickThreeWayDecision(
        sample as Record<string, unknown> | null,
        job as unknown as Record<string, unknown>,
      ),
    [sample, job.three_way, job.three_way_match, job.updated_at, activeChain],
  )
  const sampleRev = useMemo(() => {
    const s = isGospdJob(job) ? activeSample(job) : null
    const blob = (s?.evidence ?? job.evidence) as Record<string, unknown> | undefined
    return [
      job.updated_at || '',
      activeChain,
      resultOverallStatus(blob),
      String(blob?.issue_description || blob?.human_readable_summary || ''),
    ].join('|')
  }, [job, activeChain])
  const notReady =
    deskRow?.reason === 'amount_ambiguity' ||
    deskRow?.reason === 'missing_docs' ||
    deskRow?.reason === 'fields_gap' ||
    deskRow?.reason === 'wait_docs'

  useEffect(() => {
    let cancelled = false
    setTraceLoading(true)
    ;(async () => {
      try {
        const [t, chains] = await Promise.all([
          conclusionTraceCached(job, { chainId: activeChain || null }),
          listChainsCached(job),
        ])
        if (!cancelled) {
          setTrace(t)
          const row = (chains.chains || []).find((c) => c.chain_id === (job.active_chain_id || ''))
          setDeskRow(row || null)
          setErr('')
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setTraceLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [job.job_id, sampleRev, job.finding_acknowledgements, activeChain])

  const findings = useMemo(() => {
    const all = trace?.findings || []
    const scoped = activeChain
      ? all.filter((f) => !f.chain_id || f.chain_id === activeChain)
      : all
    const blocking = scoped.filter((f) => f.blocking && !f.acknowledged)
    return blocking.length ? blocking : scoped.filter((f) => f.blocking)
  }, [trace, activeChain])

  const groups = useMemo(() => groupFindings(findings), [findings])

  const traceToField = (target: FieldTraceTarget) => {
    storeFieldTraceTarget(target)
    onGo('field_confirm')
  }

  const confirmFail = async () => {
    setBusy(true)
    setErr('')
    try {
      onJob(await api.confirmConclusion(job.job_id, '确认测试不通过成立', true))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const confirmDocIssue = async () => {
    setBusy(true)
    setErr('')
    try {
      const out = await api.releaseActiveChain(job.job_id, {
        reason: '确认为单据问题',
        ack_unacked: true,
      })
      onJob(out.job)
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
          <h3>{gate5Ok ? (disposition === 'fail' ? '已确认不通过' : '审阅结果') : '测试未通过'}</h3>
          <div className="hint">
            {gate5Ok
              ? disposition === 'fail'
                ? '本笔已按不通过收口。下面是当时对不上的数据。'
                : '本笔已通过或已按单据问题放行。'
              : '三单匹配与截止性分开看。看清对不上的数据和测试逻辑后，确认是测试不通过，还是单据本身有问题。'}
          </div>
        </div>
      </div>
      <div className="panel-body fail-review">
        {err && <p className="err">{err}</p>}
        <ChainPicker job={job} onJob={onJob} compact />
        {traceLoading ? (
          <p className="preview-empty">正在加载本笔结论…</p>
        ) : notReady ? (
          <p className="preview-empty">
            {deskRow?.reason === 'amount_ambiguity'
              ? '本笔还有金额未确认，不能看测试结论。请先到核对页把多金额确认完。'
              : deskRow?.reason === 'missing_docs'
                ? `本笔还缺必需单据（${(deskRow.missing_doc_labels || []).join('、') || '发票等'}），不能当字段已齐，也不能出测试结论。请先补凭证。`
                : deskRow?.reason === 'docs_uncertain'
                  ? `本笔单据类型存疑（${(deskRow.uncertain_doc_labels || []).join('、') || '需核对'}），请先到核对页确认类型后再看结论。`
                  : deskRow?.reason === 'fields_gap'
                    ? `本笔还缺字段（${(deskRow.missing_labels || []).join('、') || '必填项'}），请先到核对页补齐。`
                    : '本笔还没有对应凭证。'}
          </p>
        ) : (
          <>
            {decisionView && !findings.some((f) => f.decision) ? (
              <>
                <ThreeWayDecisionCard view={decisionView} />
                <ThreeWayEvidenceTable job={job} chainFileNames={chainFileNames} onTrace={traceToField} />
              </>
            ) : null}
            {!findings.length && (
              <p className="preview-empty">当前笔没有未通过项。绿灯笔会自动收口，不必停在本页。</p>
            )}
            {groups.map((g) => (
              <section key={g.key} className="fail-module">
                <header className="fail-module-head">
                  <h4>{g.title}</h4>
                  {g.hint ? <p>{g.hint}</p> : null}
                </header>
                {g.items.map((f) => (
                  <FailCard key={f.finding_id} finding={f} job={job} chainFileNames={chainFileNames} onTrace={traceToField} />
                ))}
              </section>
            ))}
            {!gate5Ok && findings.length > 0 && (
              <div className="fail-actions">
                <button className="btn primary" disabled={busy} onClick={() => void confirmFail()}>
                  确认为不通过
                </button>
                <button className="btn" disabled={busy} onClick={() => void confirmDocIssue()}>
                  这是单据问题，放行
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
