import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Job } from '../types'

const { listAmountAmbiguities, decideAmountAmbiguity, scanAmountAmbiguities, aiReviewAmountAmbiguity } = vi.hoisted(() => ({
  listAmountAmbiguities: vi.fn(),
  decideAmountAmbiguity: vi.fn(),
  scanAmountAmbiguities: vi.fn(),
  aiReviewAmountAmbiguity: vi.fn(),
}))

vi.mock('../api', () => ({
  api: { listAmountAmbiguities, decideAmountAmbiguity, scanAmountAmbiguities, aiReviewAmountAmbiguity },
}))

import { AmountAmbiguityPanel } from './AmountAmbiguityPanel'

const job = (chainId: string): Job => ({
  job_id: 'job-1',
  title: '金额链',
  goal_ids: [],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [],
  fields_confirmed: false,
  active_step: 'field_confirm',
  active_chain_id: chainId,
})

const ambiguity = (id: string) => ({
  ambiguity_id: id, file_name: 'a.pdf', field_key: 'totalAmount',
  field_name: '价税合计', status: 'NEEDS_REVIEW', trigger_reasons: ['MULTIPLE_CANDIDATES'],
  candidates: [{ candidate_id: 'C1', label: '价税合计', value: 100 }],
})

describe('AmountAmbiguityPanel', () => {
  it('immediately reports no open cards when switching chains, before the new request returns', async () => {
    let resolveSecond: ((value: { items: []; count: number }) => void) | undefined
    listAmountAmbiguities
      .mockResolvedValueOnce({
        items: [{
          ambiguity_id: 'amb-1', file_name: 'a.pdf', field_key: 'totalAmount',
          field_name: '价税合计', status: 'NEEDS_REVIEW', trigger_reasons: ['MULTIPLE_CANDIDATES'], candidates: [],
        }],
        count: 1,
      })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve }))
    const onOpenCount = vi.fn()
    const view = render(<AmountAmbiguityPanel job={job('SO25-0281')} onJob={vi.fn()} onOpenCount={onOpenCount} />)

    await waitFor(() => expect(onOpenCount).toHaveBeenLastCalledWith(1))
    view.rerender(<AmountAmbiguityPanel job={job('SO25-0282')} onJob={vi.fn()} onOpenCount={onOpenCount} />)

    expect(onOpenCount).toHaveBeenLastCalledWith(0)
    resolveSecond?.({ items: [], count: 0 })
  })

  it('shows the file, field, candidates, trigger, and inspect/adopt entries for a genuine ambiguity', async () => {
    listAmountAmbiguities.mockReset()
    listAmountAmbiguities.mockResolvedValue({
      items: [{
        ambiguity_id: 'amb-real', file_name: 'SO25-0296_invoice.pdf', field_key: 'amount',
        field_name: '不含税金额', status: 'NEEDS_REVIEW', trigger_reasons: ['ROLE_COLLISION'],
        candidates: [{ candidate_id: 'C1', label: '折后不含税金额', value: 64660.8, raw_value: '64,660.80' }],
      }],
      count: 1,
    })
    const onFocusFile = vi.fn()
    const user = userEvent.setup()
    render(<AmountAmbiguityPanel job={job('SO25-0296')} onJob={vi.fn()} onFocusFile={onFocusFile} />)

    expect(await screen.findByText(/SO25-0296_invoice\.pdf · 不含税金额/)).toBeInTheDocument()
    expect(screen.getByText(/触发：ROLE_COLLISION/)).toBeInTheDocument()
    expect(screen.getByText(/折后不含税金额 · 64660\.8/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看原件' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '采用' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看原件' }))
    expect(onFocusFile).toHaveBeenCalledWith('SO25-0296_invoice.pdf')
  })

  it('does not apply an old-chain candidate decision after the active chain changes', async () => {
    let resolveDecision: ((value: { job: Job }) => void) | undefined
    listAmountAmbiguities.mockReset()
    listAmountAmbiguities.mockResolvedValue({ items: [ambiguity('amb-decide')], count: 1 })
    decideAmountAmbiguity.mockImplementationOnce(() => new Promise((resolve) => { resolveDecision = resolve }))
    const onJob = vi.fn()
    const user = userEvent.setup()
    const view = render(<AmountAmbiguityPanel job={job('SO25-0281')} onJob={onJob} />)
    await user.click(await screen.findByRole('button', { name: '采用' }))
    view.rerender(<AmountAmbiguityPanel job={job('SO25-0282')} onJob={onJob} />)

    resolveDecision?.({ job: job('SO25-0281') })
    await Promise.resolve()
    await Promise.resolve()
    expect(onJob).not.toHaveBeenCalled()
  })

  it('does not apply an old-chain rescan after the active chain changes', async () => {
    let resolveScan: ((value: { job: Job; items: ReturnType<typeof ambiguity>[] }) => void) | undefined
    listAmountAmbiguities.mockReset()
    listAmountAmbiguities.mockResolvedValue({ items: [ambiguity('amb-scan')], count: 1 })
    scanAmountAmbiguities.mockImplementationOnce(() => new Promise((resolve) => { resolveScan = resolve }))
    const onJob = vi.fn()
    const user = userEvent.setup()
    const view = render(<AmountAmbiguityPanel job={job('SO25-0281')} onJob={onJob} />)
    await user.click(await screen.findByRole('button', { name: '重扫增强' }))
    view.rerender(<AmountAmbiguityPanel job={job('SO25-0282')} onJob={onJob} />)

    resolveScan?.({ job: job('SO25-0281'), items: [ambiguity('amb-scan')] })
    await Promise.resolve()
    await Promise.resolve()
    expect(onJob).not.toHaveBeenCalled()
  })

  it('does not apply an old-chain AI review after the active chain changes', async () => {
    let resolveReview: ((value: { job: Job }) => void) | undefined
    listAmountAmbiguities.mockReset()
    listAmountAmbiguities.mockResolvedValue({ items: [ambiguity('amb-ai')], count: 1 })
    aiReviewAmountAmbiguity.mockImplementationOnce(() => new Promise((resolve) => { resolveReview = resolve }))
    const onJob = vi.fn()
    const user = userEvent.setup()
    const view = render(<AmountAmbiguityPanel job={job('SO25-0281')} onJob={onJob} />)
    await user.click(await screen.findByRole('button', { name: '视觉复核' }))
    view.rerender(<AmountAmbiguityPanel job={job('SO25-0282')} onJob={onJob} />)

    resolveReview?.({ job: job('SO25-0281') })
    await Promise.resolve()
    await Promise.resolve()
    expect(onJob).not.toHaveBeenCalled()
  })

  it('clears an old busy decision immediately when switching to a new chain with the same card id', async () => {
    let resolveDecision: ((value: { job: Job }) => void) | undefined
    listAmountAmbiguities.mockReset()
    listAmountAmbiguities
      .mockResolvedValueOnce({ items: [ambiguity('shared-id')], count: 1 })
      .mockResolvedValueOnce({ items: [ambiguity('shared-id')], count: 1 })
    decideAmountAmbiguity.mockImplementationOnce(() => new Promise((resolve) => { resolveDecision = resolve }))
    const user = userEvent.setup()
    const view = render(<AmountAmbiguityPanel job={job('SO25-0281')} onJob={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: '采用' }))
    view.rerender(<AmountAmbiguityPanel job={job('SO25-0282')} onJob={vi.fn()} />)
    await screen.findByText(/待确认 1/)
    resolveDecision?.({ job: job('SO25-0281') })
    await Promise.resolve()
    await Promise.resolve()

    expect(screen.getByRole('button', { name: '重扫增强' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '采用' })).toBeEnabled()
  })
})
