import { useMemo, useState } from 'react'
import type { PacketUnit } from '../types'
import { businessIdsForUnit } from '../lib/documentIntake'

type Props = {
  selectedUnits: PacketUnit[]
  businessIds: string[]
  locked?: boolean
  onChange: (units: PacketUnit[]) => void
  onConfirmSelected: (unitIds: string[]) => void
}

const TYPE_LABELS: Record<string, string> = {
  contract: '合同',
  order: '订单',
  delivery: '发货单',
  receipt: '签收/验收',
  invoice: '发票',
  payment: '回款',
  unresolved: '未识别',
  other: '未识别',
}

const TYPE_OPTIONS = ['contract', 'order', 'delivery', 'receipt', 'invoice', 'payment', 'unresolved']

export function PacketInspector({
  selectedUnits,
  businessIds,
  locked = false,
  onChange,
  onConfirmSelected,
}: Props) {
  const [query, setQuery] = useState('')
  const filteredBusinessIds = useMemo(() => {
    const token = query.trim().toLocaleLowerCase()
    if (!token) return businessIds
    return businessIds.filter((id) => id.toLocaleLowerCase().includes(token))
  }, [businessIds, query])

  if (!selectedUnits.length) {
    return (
      <aside className="packet-inspector" aria-label="单据属性">
        <h4>单据属性</h4>
        <p className="preview-empty">从联络表选择一张或多张单据。</p>
      </aside>
    )
  }

  const first = selectedUnits[0]
  const activeType = selectedUnits.every((unit) => unit.doc_type === first.doc_type)
    ? first.doc_type
    : ''
  const suggested = first.suggested_doc_type || first.doc_type || 'unresolved'

  const changeType = (docType: string) => {
    onChange(
      selectedUnits.map((unit) => ({
        ...unit,
        card_type: docType,
        doc_type: docType,
        host_type: docType,
        doc_type_source: 'human',
        needs_review: docType === 'unresolved',
      })),
    )
  }

  const toggleBusiness = (businessId: string, checked: boolean) => {
    onChange(
      selectedUnits.map((unit) => {
        const current = businessIdsForUnit(unit)
        const next = checked
          ? [...current, businessId].filter((value, index, all) => all.indexOf(value) === index)
          : current.filter((value) => value !== businessId)
        return {
          ...unit,
          business_ids: next,
          chain_id: next[0] || '未识别业务号',
          business_binding_source: 'human',
        }
      }),
    )
  }

  return (
    <aside className="packet-inspector" aria-label="单据属性">
      <div className="packet-inspector-head">
        <h4>单据属性</h4>
        <span>{selectedUnits.length} 张已选</span>
      </div>

      <div className="packet-inspector-section">
        <span className="packet-ai-suggestion">AI 建议：{TYPE_LABELS[suggested] || suggested}</span>
        <label htmlFor="packet-current-type">当前单据类型</label>
        <select
          id="packet-current-type"
          className="field-select"
          value={activeType}
          disabled={locked}
          onChange={(event) => changeType(event.target.value)}
        >
          {!activeType ? <option value="">多种类型</option> : null}
          {TYPE_OPTIONS.map((value) => (
            <option key={value} value={value}>{TYPE_LABELS[value]}</option>
          ))}
        </select>
        {first.doc_type_source === 'human' ? <span className="badge">人工覆盖</span> : null}
      </div>

      <div className="packet-inspector-section">
        <label htmlFor="packet-business-search">搜索业务</label>
        <input
          id="packet-business-search"
          type="search"
          value={query}
          placeholder="输入业务编号"
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="packet-business-options" role="group" aria-label="业务归属">
          {filteredBusinessIds.map((businessId) => {
            const checked = selectedUnits.every((unit) => businessIdsForUnit(unit).includes(businessId))
            return (
              <label key={businessId} className="packet-business-option">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={locked}
                  aria-label={`关联业务 ${businessId}`}
                  onChange={(event) => toggleBusiness(businessId, event.target.checked)}
                />
                <span>{businessId}</span>
              </label>
            )
          })}
          {!filteredBusinessIds.length ? <p className="preview-empty">抽样清单中没有匹配业务</p> : null}
        </div>
      </div>

      <button
        type="button"
        className="btn packet-confirm-selection"
        disabled={locked}
        aria-label={`确认所选 ${selectedUnits.length} 张单据`}
        onClick={() => onConfirmSelected(selectedUnits.map((unit) => unit.unit_id))}
      >
        确认所选边界
      </button>
    </aside>
  )
}
