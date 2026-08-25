import type { ClassifiedDoc } from '../types'

export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  contract: '销售合同',
  order: '销售订单',
  delivery: '发货单',
  receipt: '签收/验收',
  invoice: '发票',
  payment: '回款',
  other: '其他',
}

export function documentTypeLabel(
  doc: Pick<ClassifiedDoc, 'doc_type' | 'custom_doc_type_name'>,
): string {
  if (doc.doc_type === 'other') {
    return doc.custom_doc_type_name?.trim() || '其他（待确认）'
  }
  return DOCUMENT_TYPE_LABELS[doc.doc_type] || doc.doc_type || '未分类'
}
