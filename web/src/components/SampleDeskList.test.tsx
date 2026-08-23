import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'

import type { ChainInfo } from '../api'
import { SampleDeskList } from './SampleDeskList'

const openRow: ChainInfo = {
  chain_id: 'SO-OPEN',
  doc_count: 1,
  light: 'red',
  reason: 'missing_docs',
  label: '缺发票',
  missing_doc_types: ['发票'],
  missing_doc_labels: ['发票'],
  auto_passed: false,
}

const passedRow: ChainInfo = {
  chain_id: 'SO-PASSED',
  doc_count: 3,
  light: 'green',
  reason: 'ok',
  label: '已通过',
  auto_passed: true,
}

it('hides auto-passed rows until 查看已通过 is pressed', async () => {
  const user = userEvent.setup()
  render(
    <SampleDeskList
      rows={[openRow, passedRow]}
      onOpen={vi.fn()}
      onUpload={vi.fn()}
    />,
  )
  expect(screen.getByText('SO-OPEN')).toBeInTheDocument()
  expect(screen.queryByText('SO-PASSED')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '查看已通过 1 笔' }))
  expect(screen.getByText('SO-PASSED')).toBeInTheDocument()
})
