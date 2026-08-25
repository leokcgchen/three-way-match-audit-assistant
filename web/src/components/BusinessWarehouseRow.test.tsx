import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ChainInfo } from '../api'
import { BusinessWarehouseRow } from './BusinessWarehouseRow'

const row: ChainInfo = {
  chain_id: 'SO25-0281',
  doc_count: 1,
  light: 'red',
  reason: 'missing_docs',
  label: '缺单据',
  present_labels: ['合同'],
  missing_doc_labels: ['发票', '签收/验收'],
}

function file(name = 'evidence.pdf') {
  return new File(['evidence'], name, { type: 'application/pdf' })
}

function renderRow(
  overrides: Partial<React.ComponentProps<typeof BusinessWarehouseRow>> = {},
) {
  const onOpen = vi.fn()
  const onUpload = vi.fn(async () => undefined)
  const view = render(
    <ul>
      <BusinessWarehouseRow row={row} active={false} onOpen={onOpen} onUpload={onUpload} {...overrides} />
    </ul>,
  )
  return { ...view, onOpen, onUpload }
}

describe('BusinessWarehouseRow', () => {
  it('renders a navigation-only overview row without file controls', () => {
    renderRow({ mode: 'overview' })

    expect(screen.getByRole('button', { name: '打开业务 SO25-0281' })).toBeEnabled()
    expect(screen.queryByLabelText('为业务 SO25-0281 选择凭证')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '为业务 SO25-0281 上传凭证' })).not.toBeInTheDocument()
  })

  it('shows the canonical business id together with its order aliases', () => {
    renderRow({
      mode: 'overview',
      row: {
        ...row,
        display_index: 'YW-2025-3962 & SO-251209-7214',
        order_numbers: ['SO-251209-7214'],
      },
    })

    expect(screen.getByText('YW-2025-3962 & SO-251209-7214')).toBeInTheDocument()
    expect(screen.getByText('业务主键 & 订单别名')).toBeInTheDocument()
  })

  it('opens the hidden picker and reports selected files for the target business', async () => {
    const user = userEvent.setup()
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click')
    const { onUpload } = renderRow()

    await user.click(screen.getByRole('button', { name: '为业务 SO25-0281 上传凭证' }))
    expect(clickSpy).toHaveBeenCalledOnce()

    const input = screen.getByLabelText('为业务 SO25-0281 选择凭证')
    fireEvent.change(input, { target: { files: [file()] } })

    expect(onUpload).toHaveBeenCalledWith(row, [expect.objectContaining({ name: 'evidence.pdf' })])
  })

  it('accepts dropped files and names the current drop target', async () => {
    const { onUpload } = renderRow()
    const target = screen.getByRole('listitem')
    const dropped = file('delivery.pdf')

    fireEvent.dragEnter(target, { dataTransfer: { files: [dropped], types: ['Files'] } })
    expect(screen.getByText('将 1 个文件关联到业务 SO25-0281')).toBeInTheDocument()

    fireEvent.drop(target, { dataTransfer: { files: [dropped], types: ['Files'] } })

    expect(onUpload).toHaveBeenCalledWith(row, [dropped])
    expect(screen.queryByText('将 1 个文件关联到业务 SO25-0281')).not.toBeInTheDocument()
  })

  it('keeps upload and business opening as separate actions', async () => {
    const user = userEvent.setup()
    const { onOpen } = renderRow()

    await user.click(screen.getByRole('button', { name: '为业务 SO25-0281 上传凭证' }))
    expect(onOpen).not.toHaveBeenCalled()

    const main = screen.getByRole('button', { name: '打开业务 SO25-0281' })
    main.focus()
    await user.keyboard('{Enter}')
    expect(onOpen).toHaveBeenCalledWith(row)
  })

  it('shows stable in-row busy and error feedback', () => {
    const { rerender } = renderRow({ uploading: true })

    expect(screen.getByRole('listitem')).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('button', { name: '正在为业务 SO25-0281 上传凭证' })).toBeDisabled()

    rerender(
      <ul>
        <BusinessWarehouseRow
          row={row}
          active={false}
          uploading={false}
          uploadError="网络中断，请重试"
          onOpen={vi.fn()}
          onUpload={vi.fn(async () => undefined)}
        />
      </ul>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('网络中断，请重试')
  })

  it('lets the auditor declare this business complete without opening it', async () => {
    const user = userEvent.setup()
    const onCompleteSetChange = vi.fn(async () => undefined)
    const { onOpen } = renderRow({ onCompleteSetChange })

    const checkbox = screen.getByRole('checkbox', { name: '本笔已齐套：SO25-0281' })
    expect(checkbox).not.toBeChecked()

    await user.click(checkbox)

    expect(onCompleteSetChange).toHaveBeenCalledWith(row, true)
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('does not show the complete-set control in navigation-only overview mode', () => {
    renderRow({ mode: 'overview', onCompleteSetChange: vi.fn() })

    expect(
      screen.queryByRole('checkbox', { name: '本笔已齐套：SO25-0281' }),
    ).not.toBeInTheDocument()
  })
})
