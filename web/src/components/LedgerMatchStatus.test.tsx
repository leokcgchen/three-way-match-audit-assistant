import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { ClassifiedDoc } from '../types'
import { LedgerMatchStatus } from './LedgerMatchStatus'

describe('LedgerMatchStatus', () => {
  it('shows the canonical business id for a successful ledger match', () => {
    const document = {
      file_name: 'YW-2025-3962_发票.pdf',
      doc_type: 'invoice',
      ledger_evaluated: true,
      ledger_match_ok: true,
      ledger_matched_biz_id: 'YW-2025-3962',
      ledger_posting_date: '2026-01-02',
    } satisfies ClassifiedDoc

    render(<LedgerMatchStatus document={document} />)

    expect(screen.getByText('已匹配序时账 · YW-2025-3962')).toBeInTheDocument()
    expect(screen.getByText('入账日期 2026-01-02')).toBeInTheDocument()
  })

  it('explains the document index, ledger index and failed query', async () => {
    const user = userEvent.setup()
    const document = {
      file_name: 'YW-2025-9999_发票.pdf',
      doc_type: 'invoice',
      ledger_evaluated: true,
      ledger_match_ok: false,
      sample_business_id: 'YW-2025-9999',
      business_index_source: 'filename',
      ledger_query_biz_id: 'YW-2025-9999',
      ledger_index_column: 'business_id',
      ledger_match_reason: {
        code: 'NOT_FOUND',
        message: '序时账业务主键列中未找到与凭证业务编号相同的值。',
        document_index: 'YW-2025-9999',
        document_index_source: 'filename',
        ledger_index_column: 'business_id',
        query_value: 'YW-2025-9999',
      },
    } satisfies ClassifiedDoc

    render(<LedgerMatchStatus document={document} />)
    await user.click(screen.getByText('未匹配：业务编号 YW-2025-9999'))

    expect(screen.getByText('文件名')).toBeInTheDocument()
    expect(screen.getByText('business_id')).toBeInTheDocument()
    expect(screen.getAllByText('YW-2025-9999').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('序时账业务主键列中未找到与凭证业务编号相同的值。')).toBeInTheDocument()
  })

  it('states that no sample business id was obtained', () => {
    const document = {
      file_name: '扫描件.pdf',
      doc_type: 'receipt',
      ledger_evaluated: true,
      ledger_match_ok: false,
      ledger_match_reason: {
        code: 'MISSING_DOCUMENT_INDEX',
        message: '凭证未取得抽样清单业务编号，未执行序时账查询。',
      },
    } satisfies ClassifiedDoc

    render(<LedgerMatchStatus document={document} />)

    expect(screen.getByText('无法关联：未取得抽样业务编号')).toBeInTheDocument()
    expect(screen.queryByText('账未匹配')).not.toBeInTheDocument()
  })
})
