import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { SampleScopeException } from '../types'
import { SampleScopeExceptionDialog } from './SampleScopeExceptionDialog'

const outside: SampleScopeException = {
  exception_id: 'scope-1',
  file_name: 'SO25-9999_合同.pdf',
  scope_status: 'OUT_OF_SAMPLE',
  detected_business_ids: ['SO25-9999'],
  reason: '识别到的业务号不在当前抽样清单中，不能进入审阅业务列表。',
  recommended_action: 'delete',
}

describe('SampleScopeExceptionDialog', () => {
  it('asks the auditor to review and recommends deletion', () => {
    render(
      <SampleScopeExceptionDialog
        exceptions={[outside]}
        onDelete={vi.fn()}
        onDismiss={vi.fn()}
      />,
    )

    expect(screen.getByRole('dialog', { name: '发现非抽样清单材料' })).toBeInTheDocument()
    expect(screen.getByText('SO25-9999')).toBeInTheDocument()
    expect(screen.getByText(/不会新增业务/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除该文件（推荐）' })).toBeInTheDocument()
  })

  it('keeps the file in the exception area when the auditor dismisses', async () => {
    const onDelete = vi.fn()
    const onDismiss = vi.fn()
    const user = userEvent.setup()
    render(
      <SampleScopeExceptionDialog
        exceptions={[outside]}
        onDelete={onDelete}
        onDismiss={onDismiss}
      />,
    )

    await user.click(screen.getByRole('button', { name: '暂不删除，留在异常区' }))

    expect(onDismiss).toHaveBeenCalledOnce()
    expect(onDelete).not.toHaveBeenCalled()
  })

  it('requires an explicit click before deleting', async () => {
    const onDelete = vi.fn()
    const user = userEvent.setup()
    render(
      <SampleScopeExceptionDialog
        exceptions={[outside]}
        onDelete={onDelete}
        onDismiss={vi.fn()}
      />,
    )

    expect(onDelete).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '删除该文件（推荐）' }))
    expect(onDelete).toHaveBeenCalledWith(outside)
  })

  it('keeps keyboard focus inside the review dialog', async () => {
    const user = userEvent.setup()
    render(
      <SampleScopeExceptionDialog
        exceptions={[outside]}
        onDelete={vi.fn()}
        onDismiss={vi.fn()}
      />,
    )
    const recommended = screen.getByRole('button', { name: '删除该文件（推荐）' })
    const keep = screen.getByRole('button', { name: '暂不删除，留在异常区' })

    expect(recommended).toHaveFocus()
    await user.tab({ shift: true })
    expect(keep).toHaveFocus()
    await user.tab()
    expect(recommended).toHaveFocus()
  })
})
