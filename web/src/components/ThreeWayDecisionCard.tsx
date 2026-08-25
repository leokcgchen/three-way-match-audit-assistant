import {
  decisionLabel,
  decisionTone,
  expandThreeWayShorthand,
  holdReasonLabel,
  quantityRolesLine,
  type ThreeWayDecisionView,
} from '../lib/threeWayDecision'

type Props = {
  view: ThreeWayDecisionView
}

const FULFILLMENT_FLAG_LABEL: Record<string, string> = {
  PARTIAL_FULFILLMENT: '累计签收尚未达到订单数量',
  PARTIAL_INVOICE: '累计开票尚未达到累计签收数量',
  PARTIAL_RECEIPT_AMT: '累计签收金额尚未达到订单金额',
  PARTIAL_INVOICE_AMT: '累计开票金额尚未达到订单金额',
  SET_CLAIMED_INCOMPLETE: '已声明齐套但资料仍不完整',
  OVER_RECEIPT: '累计签收数量超过订单数量',
  OVER_INVOICE_QTY: '累计开票数量超过累计签收数量',
  OVER_RECEIPT_AMT: '累计签收金额超过订单金额',
  OVER_INVOICE_AMT: '累计开票金额超过订单金额',
  DUPLICATE_SOURCE_LINE: '发现重复来源行，未重复累计',
  AMBIGUOUS_LINK: '存在多个同等级订单行，须人工确认绑定',
  UNBOUND: '存在无法绑定到订单行的凭证行',
  HEADER_REFERENCE_CONFLICT: '人工业务组内的单据编号存在冲突',
}

const ROLE_LABEL: Record<string, string> = {
  order: '订单',
  receipt: '签收/验收',
  invoice: '发票',
}

function fulfillmentTone(light?: string): 'ok' | 'warn' | 'err' | null {
  if (light === 'GREEN') return 'ok'
  if (light === 'YELLOW') return 'warn'
  if (light === 'RED') return 'err'
  return null
}

function fulfillmentLightLabel(light?: string, completeSet?: boolean): string {
  if (light === 'GREEN') return '绿灯 · 累计一致'
  if (light === 'YELLOW') return completeSet ? '黄灯 · 待人工确认' : '黄灯 · 资料仍在补充'
  if (light === 'RED') return completeSet ? '红灯 · 齐套后异常' : '红灯 · 累计异常'
  return '状态待确认'
}

function quantityTotal(
  rows: NonNullable<NonNullable<ThreeWayDecisionView['fulfillment']>['rows']>,
  key: 'ordered_qty' | 'received_qty' | 'invoiced_qty',
): string {
  const total = rows.reduce((sum, row) => sum + Number(row[key] || 0), 0)
  return Number.isFinite(total) ? String(total) : '—'
}

/** 仅结论页：说明本笔三单为何通过/待复核（不是工作台常驻控件）。 */
export function ThreeWayDecisionCard({ view }: Props) {
  const fulfillment = view.fulfillment
  const rows = fulfillment?.rows || []
  const allocations = fulfillment?.allocations || []
  const roleFiles = fulfillment?.role_files || {}
  const tone = fulfillmentTone(fulfillment?.light) || decisionTone(view.decision)
  const hold = holdReasonLabel(view.hold_reason_code)
  const qty = quantityRolesLine(view.quantity_roles)
  const reasons = (view.decision_reasons || []).filter(Boolean)
  const erpNote =
    view.erp_review?.status === 'UNAVAILABLE'
      ? view.erp_review.note || '未接公司 ERP；纸面结论不冒充已过账。'
      : ''

  return (
    <aside className={`tw-decision tw-decision-${tone}`}>
      <div className="tw-decision-head">
        <span className="tw-decision-label">三单结论</span>
        <strong className="tw-decision-value">{decisionLabel(view.decision)}</strong>
        {hold ? <span className="badge warn">{hold}</span> : null}
      </div>
      {reasons.length > 0 && (
        <ul className="tw-decision-reasons">
          {reasons.slice(0, 6).map((r, i) => (
            <li key={i}>{expandThreeWayShorthand(r)}</li>
          ))}
        </ul>
      )}
      {qty ? <p className="tw-decision-qty">数量三角色 · {qty}</p> : null}
      {erpNote ? <p className="tw-decision-erp hint">{erpNote}</p> : null}
      {fulfillment && rows.length > 0 ? (
        <section className="tw-fulfillment" aria-label="履约累计">
          <div className="tw-fulfillment-head">
            <strong>履约累计</strong>
            <span
              className={`badge ${fulfillment.light === 'RED' ? 'danger' : fulfillment.light === 'GREEN' ? 'ok' : 'warn'}`}
              role="status"
            >
              {fulfillmentLightLabel(fulfillment.light, fulfillment.complete_set)}
            </span>
          </div>
          <p className="tw-fulfillment-counts">
            订单 {(roleFiles.order || []).length} · 签收/验收 {(roleFiles.receipt || []).length} · 发票 {(roleFiles.invoice || []).length}
          </p>
          <p className="tw-fulfillment-summary">
            {fulfillment.summary || `订单 ${quantityTotal(rows, 'ordered_qty')} · 累计签收 ${quantityTotal(rows, 'received_qty')} · 累计开票 ${quantityTotal(rows, 'invoiced_qty')}`}
          </p>
          {(fulfillment.flags || []).length > 0 ? (
            <ul className="tw-fulfillment-flags">
              {[...new Set(fulfillment.flags)].map((flag) => (
                <li key={flag}>{FULFILLMENT_FLAG_LABEL[flag] || flag}</li>
              ))}
            </ul>
          ) : null}
          <details className="tw-fulfillment-details">
            <summary>查看文件与逐行分配明细</summary>
            <div className="tw-role-files">
              {(['order', 'receipt', 'invoice'] as const).map((role) => (
                <section key={role}>
                  <strong>{ROLE_LABEL[role]}</strong>
                  {(roleFiles[role] || []).length ? (
                    <ul>{(roleFiles[role] || []).map((file) => <li key={file}>{file}</li>)}</ul>
                  ) : <p>无</p>}
                </section>
              ))}
            </div>
            <div className="tw-allocation-table-wrap">
              <table className="data-table tw-allocation-table">
                <thead>
                  <tr>
                    <th>来源文件</th>
                    <th>角色</th>
                    <th>来源行</th>
                    <th>绑定订单行</th>
                    <th>数量</th>
                    <th>绑定依据 / 状态</th>
                  </tr>
                </thead>
                <tbody>
                  {allocations.length ? allocations.map((allocation, index) => (
                    <tr key={`${allocation.source_file || 'file'}:${allocation.source_line_id || index}:${index}`}>
                      <td>{allocation.source_file || '—'}</td>
                      <td>{ROLE_LABEL[allocation.source_role || ''] || allocation.source_role || '—'}</td>
                      <td>{allocation.source_line_id || '—'}</td>
                      <td>{allocation.order_line_id || '待人工绑定'}</td>
                      <td>{allocation.qty ?? '—'}</td>
                      <td>{[...(allocation.basis || []), allocation.rejected_reason || allocation.bind_status || ''].filter(Boolean).join('；') || '—'}</td>
                    </tr>
                  )) : (
                    <tr><td colSpan={6}>暂无逐行分配记录</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </details>
        </section>
      ) : null}
    </aside>
  )
}
