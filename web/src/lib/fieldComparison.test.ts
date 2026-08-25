import { describe, expect, it } from 'vitest'

import type { ClassifiedDoc, Job } from '../types'
import { buildFieldComparison, countUnverifiedMismatches, requiredRowsFromDocs } from './fieldComparison'

function makeJob(docs: ClassifiedDoc[]): Job {
  return {
    job_id: 'job-1',
    title: '日期规则测试',
    goal_ids: ['gospd01030'],
    plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
    classified: docs,
    fields_confirmed: true,
    active_step: 'field_confirm',
    active_chain_id: 'SO25-0281',
  }
}

function docsWithDates(overrides: Partial<Record<'contract' | 'order' | 'receipt' | 'invoice', Record<string, unknown>>> = {}): ClassifiedDoc[] {
  return [
    { file_name: 'contract-SO25-0281.pdf', doc_type: 'contract', fields: { documentNo: 'HT-1', contractNo: 'HT-1', documentDate: '2025-12-01', buyerName: '甲公司', supplierName: '乙公司', totalAmount: '100', ...overrides.contract } },
    { file_name: 'order-SO25-0281.pdf', doc_type: 'order', fields: { documentNo: 'PO-1', contractNo: 'HT-1', orderNo: 'PO-1', documentDate: '2025-12-02', buyerName: '甲公司', supplierName: '乙公司', quantity: '1', totalAmount: '100', ...overrides.order } },
    { file_name: 'receipt-SO25-0281.pdf', doc_type: 'receipt', fields: { documentNo: 'RC-1', orderNo: 'PO-1', acceptanceDate: '2025-12-03', documentDate: '2025-12-03', ...overrides.receipt } },
    { file_name: 'invoice-SO25-0281.pdf', doc_type: 'invoice', fields: { documentNo: 'INV-1', invoiceNo: 'INV-1', documentDate: '2025-12-04', postingDate: '2025-12-04', buyerName: '甲公司', supplierName: '乙公司', quantity: '1', totalAmount: '100', amount: '90', taxAmount: '10', ...overrides.invoice } },
  ]
}

describe('field comparison date and identifier rules', () => {
  it('uses neutral file date wording and includes buyer and seller as references', () => {
    const comparison = buildFieldComparison(makeJob(docsWithDates()))

    expect(comparison.rows.find((row) => row.fieldKey === 'acceptanceDate')?.label).toBe('文件日期')
    expect(comparison.rows.find((row) => row.fieldKey === 'buyerName')?.label).toBe('购方')
    expect(comparison.rows.find((row) => row.fieldKey === 'supplierName')?.label).toBe('卖方')
  })

  it('shows every extracted audit field even when the active required-field plan omits it', () => {
    const comparison = buildFieldComparison(
      makeJob(docsWithDates({
        order: { goodsName: '伺服电机', model: 'SM-130', customerCode: 'KH-1', rule_engine_status: 'UNRESOLVED' },
        receipt: { goodsName: '伺服电机', model: 'SM-130', customerCode: 'KH-WMS-1' },
        invoice: { goodsName: '伺服电机', model: 'SM-130', orderNo: 'PO-1' },
      })),
      undefined,
      undefined,
      [{ key: 'totalAmount', label: '价税合计', source_types: ['contract', 'order', 'invoice'] }],
    )

    expect(comparison.rows.map((row) => row.fieldKey)).toEqual(expect.arrayContaining([
      'totalAmount', 'buyerName', 'supplierName', 'orderNo', 'goodsName', 'model', 'customerCode',
    ]))
    expect(comparison.rows.find((row) => row.fieldKey === 'supplierName')?.match).toBe(true)
    expect(comparison.rows.find((row) => row.fieldKey === 'customerCode')?.manualReviewOnly).toBe(true)
    expect(comparison.rows.map((row) => row.fieldKey)).not.toContain('rule_engine_status')
  })

  it('keeps identifiers and document dates visible but excludes their differing values from mismatch counts', () => {
    const job = makeJob(docsWithDates())
    const comparison = buildFieldComparison(job)

    const manualKeys = ['documentNo', 'contractNo', 'invoiceNo', 'documentDate']
    for (const key of manualKeys) {
      expect(comparison.rows.find((row) => row.fieldKey === key)?.match).toBe(true)
    }
    const mismatchKeys = comparison.rows.filter((row) => !row.match).map((row) => row.fieldKey)
    expect(mismatchKeys).not.toContain('documentNo')
    expect(mismatchKeys).not.toContain('contractNo')
    expect(mismatchKeys).not.toContain('invoiceNo')
    expect(mismatchKeys).not.toContain('documentDate')
    expect(countUnverifiedMismatches(job)).toBe(mismatchKeys.length)
  })

  it('uses the related order number as a consistency key without comparing each document own number', () => {
    const docs = docsWithDates({ invoice: { orderNo: 'WRONG-ORDER' } })
    const comparison = buildFieldComparison(makeJob(docs))

    expect(comparison.rows.find((row) => row.fieldKey === 'orderNo')).toMatchObject({
      match: false,
      manualReviewOnly: false,
    })
    expect(comparison.rows.find((row) => row.fieldKey === 'documentNo')).toMatchObject({
      match: true,
      manualReviewOnly: true,
    })
  })

  it('reports a passing contract-to-invoice chronology without comparing document dates for equality', () => {
    const comparison = buildFieldComparison(makeJob(docsWithDates())) as typeof buildFieldComparison extends (...args: never[]) => infer T ? T & { timing?: { status: string; summary: string } } : never

    expect(comparison.timing).toMatchObject({ status: 'PASS' })
    expect(comparison.timing?.summary).toContain('合同日')
  })

  it('treats contract as optional for an order-receipt-invoice three-way pack', () => {
    const docs = docsWithDates()
      .filter((doc) => doc.doc_type !== 'contract')
      .map((doc) => doc.doc_type === 'order'
        ? { ...doc, fields: { ...doc.fields, contractNo: undefined } }
        : doc)

    const required = requiredRowsFromDocs(docs, ['gospd01030'])
    const comparison = buildFieldComparison(makeJob(docs))

    expect(required.map((row) => row.key)).not.toContain('contractNo')
    expect(comparison.timing).toMatchObject({ status: 'PASS' })
    expect(comparison.timing.summary).not.toContain('合同日')
  })

  it('requires review when a required chronology date is missing', () => {
    const comparison = buildFieldComparison(makeJob(docsWithDates({ receipt: { acceptanceDate: '', documentDate: '' } }))) as typeof buildFieldComparison extends (...args: never[]) => infer T ? T & { timing?: { status: string; summary: string } } : never

    expect(comparison.timing).toMatchObject({ status: 'REVIEW' })
  })

  it('fails an inverted chronology', () => {
    const comparison = buildFieldComparison(makeJob(docsWithDates({ order: { documentDate: '2025-12-04' }, receipt: { acceptanceDate: '2025-12-03' } }))) as typeof buildFieldComparison extends (...args: never[]) => infer T ? T & { timing?: { status: string; summary: string } } : never

    expect(comparison.timing).toMatchObject({ status: 'FAIL' })
  })

  it('counts a chronology failure and checks valid inversions before missing dates', () => {
    const job = makeJob(docsWithDates({
      contract: { documentDate: '2025-12-04' },
      order: { documentDate: '2025-12-03' },
      receipt: { acceptanceDate: '', documentDate: '' },
    }))

    const comparison = buildFieldComparison(job)
    expect(comparison.timing).toMatchObject({ status: 'FAIL' })
    expect(countUnverifiedMismatches(job)).toBe(
      comparison.rows.filter((row) => !row.match).length + 1,
    )
  })

  it('uses unsaved date drafts for chronology immediately', () => {
    const job = makeJob(docsWithDates())
    const comparison = buildFieldComparison(job, undefined, {
      'contract-SO25-0281.pdf': { documentDate: '2025-12-04' },
      'order-SO25-0281.pdf': { documentDate: '2025-12-03' },
    })

    expect(comparison.timing).toMatchObject({ status: 'FAIL' })
  })
})
