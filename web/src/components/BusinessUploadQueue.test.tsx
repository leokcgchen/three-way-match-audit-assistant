import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ChainInfo } from '../api'
import { BusinessUploadQueue } from './BusinessUploadQueue'

describe('BusinessUploadQueue', () => {
  it('renders missing-document businesses with per-business upload actions', () => {
    const rows: ChainInfo[] = [{ chain_id: 'SO-1', doc_count: 0, reason: 'missing_docs', missing_doc_labels: ['发票'] }]
    render(<BusinessUploadQueue rows={rows} onOpen={vi.fn()} onUpload={vi.fn()} />)
    expect(screen.getByRole('heading', { name: '待处理业务（待上传或待补充凭证）' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '为业务 SO-1 上传凭证' })).toBeEnabled()
  })

  it('passes the complete-set decision through for the selected business', async () => {
    const user = userEvent.setup()
    const onCompleteSetChange = vi.fn()
    const rows: ChainInfo[] = [
      { chain_id: 'SO-1', doc_count: 0, reason: 'missing_docs', complete_set: false },
    ]

    render(
      <BusinessUploadQueue
        rows={rows}
        onOpen={vi.fn()}
        onUpload={vi.fn()}
        onCompleteSetChange={onCompleteSetChange}
      />,
    )
    await user.click(screen.getByRole('checkbox', { name: '本笔已齐套：SO-1' }))

    expect(onCompleteSetChange).toHaveBeenCalledWith(rows[0], true)
  })
})
