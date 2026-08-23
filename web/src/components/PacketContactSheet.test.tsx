import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { PacketFile, PacketUnit } from '../types'
import { PacketContactSheet } from './PacketContactSheet'

function unit(
  unitId: string,
  sourceFile: string,
  pages: number[],
  overrides: Partial<PacketUnit> = {},
): PacketUnit {
  return {
    unit_id: unitId,
    source_file: sourceFile,
    page_start: pages[0],
    page_end: pages[pages.length - 1],
    pages,
    doc_type: 'contract',
    chain_id: 'SO25-0281',
    business_ids: ['SO25-0281'],
    boundary_confirmed: false,
    ...overrides,
  }
}

const files: PacketFile[] = [
  { file_name: '合同包.pdf', page_count: 3, kind: 'packet_single_chain' },
  { file_name: '验收包.pdf', page_count: 1, kind: 'packet_single_chain' },
]

function renderSheet(
  overrides: Partial<React.ComponentProps<typeof PacketContactSheet>> = {},
) {
  const props: React.ComponentProps<typeof PacketContactSheet> = {
    files,
    units: [
      unit('u1', '合同包.pdf', [1]),
      unit('u2', '合同包.pdf', [2]),
      unit('u3', '合同包.pdf', [3]),
      unit('u4', '验收包.pdf', [1], { doc_type: 'receipt' }),
    ],
    selectedUnitIds: [],
    thumbnails: {},
    onSelectionChange: vi.fn(),
    onPageFocus: vi.fn(),
    onSplit: vi.fn(),
    onMerge: vi.fn(),
    onDropPage: vi.fn(),
    onRestoreUnit: vi.fn(),
    onOpenOriginal: vi.fn(),
    ...overrides,
  }
  return { ...render(<PacketContactSheet {...props} />), props }
}

describe('PacketContactSheet', () => {
  it('renders multiple source files with explicit document boundaries and page labels', () => {
    renderSheet()

    expect(screen.getByRole('region', { name: '合同包.pdf，3 页' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '验收包.pdf，1 页' })).toBeInTheDocument()
    expect(screen.getAllByText('合同 · 第1页').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '选择 合同包.pdf 第 3 页' })).toBeInTheDocument()
  })

  it('supports single, ctrl, and shift range selection across units in one file', async () => {
    const user = userEvent.setup()
    const onSelectionChange = vi.fn()
    const { rerender, props } = renderSheet({ onSelectionChange })

    await user.click(screen.getByRole('button', { name: '选择 合同包.pdf 第 1 页' }))
    expect(onSelectionChange).toHaveBeenLastCalledWith(['u1'])

    rerender(<PacketContactSheet {...props} selectedUnitIds={['u1']} />)
    fireEvent.click(screen.getByRole('button', { name: '选择 合同包.pdf 第 2 页' }), {
      ctrlKey: true,
    })
    expect(onSelectionChange).toHaveBeenLastCalledWith(['u1', 'u2'])

    fireEvent.click(screen.getByRole('button', { name: '选择 合同包.pdf 第 3 页' }), {
      shiftKey: true,
    })
    expect(onSelectionChange).toHaveBeenLastCalledWith(['u1', 'u2', 'u3'])
  })

  it('exposes split, merge, drop, restore, and original-file actions without nesting buttons', async () => {
    const user = userEvent.setup()
    const callbacks = {
      onSplit: vi.fn(),
      onMerge: vi.fn(),
      onDropPage: vi.fn(),
      onRestoreUnit: vi.fn(),
      onOpenOriginal: vi.fn(),
    }
    const value = unit('u12', '合同包.pdf', [1, 2])
    renderSheet({
      units: [value, unit('gone', '合同包.pdf', [3], { dropped: true })],
      selectedUnitIds: ['u12'],
      ...callbacks,
    })

    await user.click(screen.getByRole('button', { name: '选择 合同包.pdf 第 2 页' }))
    await user.click(screen.getByRole('button', { name: '从第 2 页拆开' }))
    expect(callbacks.onSplit).toHaveBeenCalledWith('u12', 2)

    await user.click(screen.getByRole('button', { name: '将当前单据并入上一张' }))
    expect(callbacks.onMerge).toHaveBeenCalledWith('u12')

    await user.click(screen.getByRole('button', { name: '去掉 合同包.pdf 第 2 页' }))
    expect(callbacks.onDropPage).toHaveBeenCalledWith('合同包.pdf', 2)

    await user.click(screen.getByRole('button', { name: '恢复已去掉单据 第3页' }))
    expect(callbacks.onRestoreUnit).toHaveBeenCalledWith('gone')

    await user.click(screen.getAllByRole('button', { name: '打开 合同包.pdf 原件' })[0])
    expect(callbacks.onOpenOriginal).toHaveBeenCalledWith('合同包.pdf')
  })
})
