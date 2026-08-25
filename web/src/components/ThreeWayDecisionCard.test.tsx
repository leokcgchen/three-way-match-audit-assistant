import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { pickThreeWayDecision } from '../lib/threeWayDecision'
import { ThreeWayDecisionCard } from './ThreeWayDecisionCard'

describe('ThreeWayDecisionCard', () => {
  it('shows accumulated one-to-many fulfillment evidence and translated exceptions', async () => {
    const user = userEvent.setup()
    const view = pickThreeWayDecision({
      three_way: {
        decision: 'HOLD_REVIEW',
        fulfillment: {
          light: 'RED',
          complete_set: true,
          flags: ['PARTIAL_INVOICE', 'SET_CLAIMED_INCOMPLETE'],
          role_files: {
            order: ['order.pdf'],
            receipt: ['r1.pdf', 'r2.pdf', 'r3.pdf'],
            invoice: ['i1.pdf', 'i2.pdf'],
          },
          rows: [{
            order_line_id: 'order.pdf:10',
            ordered_qty: '100',
            received_qty: '100',
            invoiced_qty: '100',
            light: 'RED',
            flags: ['PARTIAL_INVOICE', 'SET_CLAIMED_INCOMPLETE'],
          }],
          allocations: [{
            source_file: 'r1.pdf',
            source_line_id: '10',
            source_role: 'receipt',
            order_line_id: 'order.pdf:10',
            qty: '30',
            bind_status: 'UNIQUE',
            basis: ['订单行号精确一致'],
          }],
        },
      },
    })

    expect(view).not.toBeNull()
    render(<ThreeWayDecisionCard view={view!} />)

    expect(screen.getByText('订单 1 · 签收/验收 3 · 发票 2')).toBeInTheDocument()
    expect(screen.getByText('订单 100 · 累计签收 100 · 累计开票 100')).toBeInTheDocument()
    expect(screen.getByText('红灯 · 齐套后异常')).toBeInTheDocument()
    expect(screen.getByText('已声明齐套但资料仍不完整')).toBeInTheDocument()

    await user.click(screen.getByText('查看文件与逐行分配明细'))
    expect(screen.getAllByText('r1.pdf')).toHaveLength(2)
    expect(screen.getByRole('columnheader', { name: '绑定订单行' })).toBeInTheDocument()
  })

  it('keeps the legacy decision card unchanged when fulfillment is absent', () => {
    render(<ThreeWayDecisionCard view={{ decision: 'AUTO_PASS' }} />)

    expect(screen.getByText('通过')).toBeInTheDocument()
    expect(screen.queryByText('履约累计')).not.toBeInTheDocument()
  })
})
