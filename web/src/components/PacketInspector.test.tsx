import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { PacketUnit } from '../types'
import { PacketInspector } from './PacketInspector'

const selected: PacketUnit = {
  unit_id: 'u1',
  source_file: '验收包.pdf',
  page_start: 1,
  page_end: 2,
  pages: [1, 2],
  suggested_doc_type: 'delivery',
  doc_type: 'receipt',
  doc_type_source: 'ai',
  chain_id: 'SO25-0281',
  business_ids: ['SO25-0281'],
  boundary_confirmed: false,
}

function renderInspector(overrides: Partial<React.ComponentProps<typeof PacketInspector>> = {}) {
  const onChange = vi.fn()
  const onConfirmSelected = vi.fn()
  render(
    <PacketInspector
      selectedUnits={[selected]}
      businessIds={['SO25-0281', 'SO25-0282']}
      onChange={onChange}
      onConfirmSelected={onConfirmSelected}
      {...overrides}
    />,
  )
  return { onChange, onConfirmSelected }
}

describe('PacketInspector', () => {
  it('shows AI suggestion separately and marks a type override as human', async () => {
    const user = userEvent.setup()
    const { onChange } = renderInspector()

    expect(screen.getByText('AI 建议：发货单')).toBeInTheDocument()
    expect(screen.getByLabelText('当前单据类型')).toHaveValue('receipt')

    await user.selectOptions(screen.getByLabelText('当前单据类型'), 'invoice')

    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({
        unit_id: 'u1',
        doc_type: 'invoice',
        host_type: 'invoice',
        doc_type_source: 'human',
      }),
    ])
  })

  it('adds a second authoritative business link without replacing the first', async () => {
    const user = userEvent.setup()
    const { onChange } = renderInspector()

    await user.click(screen.getByRole('checkbox', { name: '关联业务 SO25-0282' }))

    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({
        business_ids: ['SO25-0281', 'SO25-0282'],
        chain_id: 'SO25-0281',
        business_binding_source: 'human',
      }),
    ])
  })

  it('filters only known sample businesses and never creates an unknown one', async () => {
    const user = userEvent.setup()
    renderInspector()

    await user.type(screen.getByLabelText('搜索业务'), 'SO25-9999')

    expect(screen.getByText('抽样清单中没有匹配业务')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: '关联业务 SO25-9999' })).not.toBeInTheDocument()
  })

  it('batch-confirms the selected unit boundaries', async () => {
    const user = userEvent.setup()
    const { onConfirmSelected } = renderInspector()

    await user.click(screen.getByRole('button', { name: '确认所选 1 张单据' }))

    expect(onConfirmSelected).toHaveBeenCalledWith(['u1'])
  })
})
