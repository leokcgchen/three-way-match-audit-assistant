import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

import { MastheadProgress } from './MastheadProgress'

it('uses complete labels for every progress category', () => {
  render(
    <MastheadProgress
      sampleTotal={5}
      progress={{ docs_missing: 1, fields_missing: 1, match_exception: 1, await_human: 1 }}
    />,
  )

  expect(screen.getByText(/缺少凭证资料/)).toBeInTheDocument()
  expect(screen.getByText(/缺少关键字段/)).toBeInTheDocument()
  expect(screen.getByText(/匹配或测试异常/)).toBeInTheDocument()
  expect(screen.getByText(/等待人工判断/)).toBeInTheDocument()
  expect(screen.queryByText(/资料缺|字段缺|待人裁/)).not.toBeInTheDocument()
})
