import { beforeEach, describe, expect, it } from 'vitest'

import { consumeFieldTraceTarget, storeFieldTraceTarget } from './fieldTraceNavigation'

describe('field trace navigation', () => {
  beforeEach(() => sessionStorage.clear())

  it('stores a source locator and consumes it once for the matching job and chain', () => {
    const target = {
      jobId: 'job-1',
      chainId: 'S-001',
      fileName: 'receipt.pdf',
      fieldKey: 'acceptanceDate',
    }

    storeFieldTraceTarget(target)

    expect(consumeFieldTraceTarget('job-1', 'S-001')).toEqual(target)
    expect(consumeFieldTraceTarget('job-1', 'S-001')).toBeNull()
  })

  it('does not consume a locator belonging to another business chain', () => {
    const target = {
      jobId: 'job-1',
      chainId: 'S-001',
      fileName: 'invoice.pdf',
      fieldKey: 'postingDate',
    }
    storeFieldTraceTarget(target)

    expect(consumeFieldTraceTarget('job-1', 'S-002')).toBeNull()
    expect(consumeFieldTraceTarget('job-1', 'S-001')).toEqual(target)
  })
})
