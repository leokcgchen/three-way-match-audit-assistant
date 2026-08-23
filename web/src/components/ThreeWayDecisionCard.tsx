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

/** 仅结论页：说明本笔三单为何通过/待复核（不是工作台常驻控件）。 */
export function ThreeWayDecisionCard({ view }: Props) {
  const tone = decisionTone(view.decision)
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
    </aside>
  )
}
