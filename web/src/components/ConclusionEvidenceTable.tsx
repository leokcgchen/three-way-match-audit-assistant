import type { ConclusionFinding, Job } from '../types'
import { buildFieldComparison, resolveDocForCell, type CompareRow } from '../lib/fieldComparison'
import type { FieldTraceTarget } from '../lib/fieldTraceNavigation'
import { expandThreeWayShorthand } from '../lib/threeWayDecision'

type TraceHandler = (target: FieldTraceTarget) => void
type ThreeWayProps = { job: Job; chainFileNames?: string[] | null; onTrace: TraceHandler }
type CutoffProps = { finding: ConclusionFinding; jobId: string; chainId: string; onTrace: TraceHandler }
const THREE_WAY_COLUMN_ORDER = ['order', 'receipt', 'invoice']
const ECONOMIC_FIELD_ROWS = [
  { keys: ['buyerName', 'supplierName'], label: '客户名称' },
  { keys: ['totalAmount', 'amount'], label: '价税合计' },
  { keys: ['quantity'], label: '数量' },
] as const
const SOURCE_FIELD_KEYS = new Set([
  'documentNo', 'contractNo', 'orderNo', 'invoiceNo',
  'documentDate', 'postingDate', 'deliveryDate', 'acceptanceDate',
])

function evidenceState(row: CompareRow): { tone: 'ok' | 'warn' | 'fail' | 'neutral'; label: string } {
  const status = String(row.threeWayStatus || '').toUpperCase()
  if (/FAIL|未通过|异常/.test(status)) return { tone: 'fail', label: '不一致' }
  if (/WARN/.test(status)) return { tone: 'warn', label: '容差内' }
  if (/PASS|通过/.test(status)) {
    return row.hasGap ? { tone: 'warn', label: '部分未测' } : { tone: 'ok', label: '一致' }
  }
  return { tone: 'neutral', label: '未测' }
}

export function ThreeWayEvidenceTable({ job, chainFileNames, onTrace }: ThreeWayProps) {
  const { columns, rows } = buildFieldComparison(job, chainFileNames)
  const threeWayColumns = THREE_WAY_COLUMN_ORDER
    .map((id) => columns.find((column) => column.id === id))
    .filter((column): column is NonNullable<typeof column> => Boolean(column))
  const economicRows = ECONOMIC_FIELD_ROWS.flatMap((config) => {
    const candidates = config.keys
      .map((key) => rows.find((row) => row.fieldKey === key))
      .filter((row): row is CompareRow => Boolean(row))
    const row = candidates.find((candidate) => candidate.threeWayStatus) || candidates[0]
    if (!row) return []
    const hasEvidence = [row.ledger, ...threeWayColumns.map((column) => row.cells[column.id])].some(Boolean)
    return hasEvidence ? [{ row, displayLabel: config.label }] : []
  })
  const sourceRows = rows.filter((row) =>
    SOURCE_FIELD_KEYS.has(row.fieldKey)
    && [row.ledger, ...threeWayColumns.map((column) => row.cells[column.id])].some(Boolean),
  )
  if (!economicRows.length && !sourceRows.length) return null

  return (
    <section className="conclusion-evidence" aria-label="三单勾稽证据">
      <div className="conclusion-evidence-head">
        <div>
          <h4>三单经济字段勾稽</h4>
          <p>只展示正式三单规则使用的客户名称、价税合计和数量。点击数值可回到核对字段及原件。</p>
        </div>
        <div className="conclusion-evidence-legend" aria-label="一致性图例">
          <span className="evidence-state is-ok">一致</span>
          <span className="evidence-state is-warn">部分未测 / 容差内</span>
          <span className="evidence-state is-fail">不一致</span>
        </div>
      </div>
      <div className="conclusion-evidence-scroll">
        <table className="conclusion-evidence-table" aria-label="三单字段横向对照">
          <thead>
            <tr>
              <th>经济字段</th><th>抽样清单</th>
              {threeWayColumns.map((column) => <th key={column.id}>{column.label}</th>)}
              <th>一致性判断</th>
            </tr>
          </thead>
          <tbody>
            {economicRows.map(({ row, displayLabel }) => {
              const state = evidenceState(row)
              return (
                <tr key={row.fieldKey} className={`evidence-row is-${state.tone}`}>
                  <th scope="row">{displayLabel}</th><td>{row.ledger || '—'}</td>
                  {threeWayColumns.map((column) => {
                    const value = row.cells[column.id] || ''
                    const sourceDoc = resolveDocForCell(columns, column.id)
                    return (
                      <td key={column.id}>
                        {value && sourceDoc ? (
                          <button type="button" className="evidence-value-link"
                            aria-label={`${column.label} ${displayLabel} ${value}，查看核对字段及原件位置`}
                            onClick={() => onTrace({ jobId: job.job_id, chainId: job.active_chain_id || '', fileName: sourceDoc.file_name, fieldKey: row.fieldKey })}>
                            {value}
                          </button>
                        ) : value || '—'}
                      </td>
                    )
                  })}
                  <td><span className={`evidence-state is-${state.tone}`}>{state.label}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {sourceRows.length ? (
        <details className="conclusion-source-index">
          <summary>查看来源标识与日期（不参与三单一致性）</summary>
          <p>编号只用于串联业务和定位原件；日期只在单据时序或截止性测试中判断，不要求跨单据相等。</p>
          <div className="conclusion-evidence-scroll">
            <table className="conclusion-evidence-table conclusion-source-table" aria-label="来源标识与日期">
              <thead>
                <tr>
                  <th>信息字段</th><th>抽样清单</th>
                  {threeWayColumns.map((column) => <th key={column.id}>{column.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {sourceRows.map((row) => (
                  <tr key={row.fieldKey}>
                    <th scope="row">{row.label}</th><td>{row.ledger || '—'}</td>
                    {threeWayColumns.map((column) => {
                      const value = row.cells[column.id] || ''
                      const sourceDoc = resolveDocForCell(columns, column.id)
                      return (
                        <td key={column.id}>
                          {value && sourceDoc ? (
                            <button type="button" className="evidence-value-link"
                              aria-label={`${column.label} ${row.label} ${value}，查看核对字段及原件位置`}
                              onClick={() => onTrace({ jobId: job.job_id, chainId: job.active_chain_id || '', fileName: sourceDoc.file_name, fieldKey: row.fieldKey })}>
                              {value}
                            </button>
                          ) : value || '—'}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </section>
  )
}

function dateValue(raw: unknown): number | null {
  const match = String(raw ?? '').trim().replace(/\//g, '-').match(/^(\d{4})-(\d{1,2})-(\d{1,2})/)
  if (!match) return null
  const stamp = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return Number.isFinite(stamp) ? stamp : null
}

function periodTone(label: string, value: unknown, cutoff: number | null): { tone: string; state: string } {
  if (/偏差/.test(label)) {
    const delta = Number(value)
    return delta === 0 ? { tone: 'ok', state: '无偏差' } : { tone: 'fail', state: `${delta > 0 ? '+' : ''}${delta} 天` }
  }
  if (/报告期|截止/.test(label)) return { tone: 'neutral', state: '判断基准' }
  const date = dateValue(value)
  if (date != null && cutoff != null) return date <= cutoff ? { tone: 'ok', state: '期内' } : { tone: 'fail', state: '期后' }
  return { tone: 'warn', state: '需复核' }
}

type CutoffNarrativeRow = {
  label: string
  value: unknown
}

function cutoffNarrative(
  finding: ConclusionFinding,
  rows: CutoffNarrativeRow[],
  cutoffEntry: [string, unknown] | undefined,
): string {
  const status = String(finding.status || '').toUpperCase()
  const resultLabel = status.includes('PASS') ? '通过' : status.includes('FAIL') ? '不通过' : '待复核'
  const controlRow = rows.find((row) => /控制权|签收|验收/.test(row.label))
  const postingRow = rows.find((row) => /入账|过账/.test(row.label))
  const deviationRow = rows.find((row) => /偏差/.test(row.label))
  const periodEnd = cutoffEntry?.[1]
  const cutoffStamp = dateValue(periodEnd)
  const controlStamp = dateValue(controlRow?.value)
  const postingStamp = dateValue(postingRow?.value)

  if (cutoffStamp == null || controlStamp == null || postingStamp == null) {
    return expandThreeWayShorthand(finding.summary || finding.title)
  }

  const controlPeriod = controlStamp <= cutoffStamp ? '期内' : '期后'
  const postingPeriod = postingStamp <= cutoffStamp ? '期内' : '期后'
  const signedDays = Math.round((postingStamp - controlStamp) / 86_400_000)
  const suppliedDeviation = Number(deviationRow?.value)
  const deviationDays = Number.isFinite(suppliedDeviation)
    ? Math.abs(suppliedDeviation)
    : Math.abs(signedDays)
  const direction = signedDays < 0 ? '提前' : signedDays > 0 ? '延后' : '同日'
  const periodRelation = controlPeriod === postingPeriod ? '同一期间' : '跨期末'
  const timingPhrase = direction === '同日'
    ? '入账日与控制权转移日为同一天'
    : `入账相对控制权转移日${direction} ${deviationDays} 天`
  const conclusion = resultLabel === '不通过'
    ? '因此收入未记入正确会计期间，相关应收账款期间需要复核。'
    : resultLabel === '通过'
      ? '因此收入归属期间判断通过。'
      : '因此需要审计师结合原始单据进一步复核收入归属期间。'

  return (
    `本笔截止性测试${resultLabel}。判断基准为报告期末日 ${String(periodEnd)}，` +
    `以签收/控制权转移日与入账日判断收入归属期间：` +
    `控制权转移日 ${String(controlRow?.value)} 位于${controlPeriod}，` +
    `入账日 ${String(postingRow?.value)} 位于${postingPeriod}，` +
    `属于${periodRelation}${direction === '同日' ? '确认' : `${direction}确认`}，${timingPhrase}。${conclusion}`
  )
}

export function CutoffEvidenceTable({ finding, jobId, chainId, onTrace }: CutoffProps) {
  const periodRows = Object.entries(finding.period || {}).filter(([, value]) => value != null && String(value).trim())
  const cutoffEntry = periodRows.find(([label]) => /报告期|截止/.test(label))
  const cutoff = cutoffEntry ? dateValue(cutoffEntry[1]) : null
  const fieldRows = (finding.fields_used || []).filter((field) => field.value != null && String(field.value).trim())
  const rows = [
    ...fieldRows.map((field) => ({ label: field.field_label || '期间字段', value: field.value, fileName: field.file_name || '', fieldKey: field.field_key || '', docType: field.doc_type || '' })),
    ...periodRows.filter(([, value]) => !fieldRows.some((field) => String(field.value) === String(value)))
      .map(([label, value]) => ({ label, value, fileName: '', fieldKey: '', docType: '' })),
  ]
  if (!rows.length) return null

  const normalizedStatus = String(finding.status || '').toUpperCase()
  const decisionTone = normalizedStatus.includes('PASS')
    ? 'ok'
    : normalizedStatus.includes('FAIL')
      ? 'err'
      : 'warn'
  const decisionLabel = decisionTone === 'ok' ? '通过' : decisionTone === 'err' ? '不通过' : '待复核'
  const decisionReason = cutoffNarrative(finding, rows, cutoffEntry)

  return (
    <>
      <aside
        className={`tw-decision tw-decision-${decisionTone} cutoff-decision`}
        aria-label={`截止性结论：${decisionLabel}`}
      >
        <div className="tw-decision-head">
          <span className="tw-decision-label">截止性结论</span>
          <strong className="tw-decision-value">{decisionLabel}</strong>
        </div>
        {decisionReason ? <ul className="tw-decision-reasons"><li>{decisionReason}</li></ul> : null}
      </aside>
      <section className="conclusion-evidence cutoff-evidence" aria-labelledby={`cutoff-evidence-${finding.finding_id}`}>
        <div className="conclusion-evidence-head"><div>
          <h4 id={`cutoff-evidence-${finding.finding_id}`}>期间判断证据</h4>
          <p>用控制权转移日、报告期截止日与入账日判断归属期间。</p>
        </div></div>
        <div className="conclusion-evidence-scroll">
          <table className="conclusion-evidence-table cutoff-evidence-table" aria-label="截止性期间判断证据">
            <thead><tr><th>判断字段</th><th>来源</th><th>日期 / 数值</th><th>期间标注</th></tr></thead>
            <tbody>{rows.map((row, index) => {
              const status = periodTone(row.label, row.value, cutoff)
              const traceable = Boolean(row.fileName && row.fieldKey)
              return (
                <tr key={`${row.label}:${index}`} className={`evidence-row is-${status.tone}`}>
                  <th scope="row">{row.label}</th>
                  <td>{row.docType ? (row.docType === 'receipt' ? '签收/验收单' : row.docType === 'invoice' ? '发票/序时账' : row.docType) : '项目参数 / 测试计算'}</td>
                  <td>{traceable ? (
                    <button type="button" className="evidence-value-link"
                      aria-label={`${row.label} ${String(row.value)}，查看核对字段及原件位置`}
                      onClick={() => onTrace({ jobId, chainId, fileName: row.fileName, fieldKey: row.fieldKey })}>
                      {String(row.value)}
                    </button>
                  ) : String(row.value)}</td>
                  <td><span className={`evidence-state is-${status.tone}`}>{status.state}</span></td>
                </tr>
              )
            })}</tbody>
          </table>
        </div>
      </section>
    </>
  )
}
