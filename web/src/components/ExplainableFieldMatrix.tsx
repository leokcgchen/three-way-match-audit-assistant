import type { ComparisonPlan, ExplainableComparisonRow } from '../types'

type Props = {
  plan: ComparisonPlan
  onExplain: (row: ExplainableComparisonRow) => void
  onSelectEvidence: (evidenceId: string) => void
}

function resultLabel(row: ExplainableComparisonRow, recalculation = false): string {
  if (row.result === 'NOT_APPLICABLE') return '不适用'
  if (row.result === 'PASS') {
    if (recalculation) return '复算一致'
    return row.relation_type === 'SEMANTIC_EQUIVALENT' ? '实质一致' : '一致'
  }
  if (row.result === 'CONFLICT' || row.result === 'FAIL') return '不一致'
  return '待补证或人工判断'
}

function resultClass(result: string): string {
  if (result === 'PASS') return 'is-ok'
  if (result === 'CONFLICT' || result === 'FAIL') return 'is-error'
  if (result === 'NOT_APPLICABLE') return 'is-neutral'
  return 'is-warning'
}

export function ExplainableFieldMatrix({ plan, onExplain, onSelectEvidence }: Props) {
  const consistency = plan.domains.consistency || []
  const recalculation = plan.domains.recalculation || []
  const mismatchCount = [...consistency, ...recalculation].filter((row) =>
    ['CONFLICT', 'FAIL'].includes(row.result),
  ).length

  return <section className="resolution-section" aria-labelledby="explainable-matrix-title">
    <header className="resolution-section-head">
      <div>
        <h3 id="explainable-matrix-title">一致性与复算</h3>
        <p>只比较本应一致或可复算的经济字段；编号、日期和单据专有信息不参与一致性计数。</p>
      </div>
      <span className={`resolution-count${mismatchCount ? ' is-error' : ''}`}>不一致 {mismatchCount} 项</span>
    </header>

    <table className="resolution-table" aria-label="跨单据一致性字段">
      <thead><tr><th scope="col">经济字段</th><th scope="col">跨单据取值</th><th scope="col">判断</th><th scope="col">依据</th></tr></thead>
      <tbody>{consistency.map((row) => <tr key={row.row_id}>
        <th scope="row">{row.label}</th>
        <td><div className="resolution-values">{(row.values || []).map((value) =>
          <button key={value.evidence_id} type="button" className="evidence-link" onClick={() => onSelectEvidence(value.evidence_id)}>
            <span>{String(value.value ?? '—')}</span><small>{value.document_role || '单据'}</small>
          </button>,
        )}</div></td>
        <td><span className={`resolution-badge ${resultClass(row.result)}`}>{resultLabel(row)}</span></td>
        <td><button type="button" className="btn compact" onClick={() => onExplain(row)}>为什么</button></td>
      </tr>)}</tbody>
    </table>

    {recalculation.length > 0 && <div className="resolution-recalc">
      <h4>数量与金额复算</h4>
      <table className="resolution-table" aria-label="数量与金额复算">
        <thead><tr><th scope="col">复算项目</th><th scope="col">计算过程</th><th scope="col">判断</th><th scope="col">依据</th></tr></thead>
        <tbody>{recalculation.map((row) => <tr key={row.row_id}>
          <th scope="row">{row.label}</th><td className="mono">{row.calculation || '—'}</td>
          <td><span className={`resolution-badge ${resultClass(row.result)}`}>{resultLabel(row, true)}</span></td>
          <td><button type="button" className="btn compact" onClick={() => onExplain(row)}>为什么</button></td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>
}
