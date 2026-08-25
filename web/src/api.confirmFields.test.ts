import { describe, expect, it, vi } from 'vitest'

import { api } from './api'

describe('api.confirmFields', () => {
  it('sends the currently displayed chain in the confirmation request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ job_id: 'job-1' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    await api.confirmFields('job-1', 'SO25-0282')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/workflow/jobs/job-1/hitl/fields/confirm',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ chain_id: 'SO25-0282' }),
      }),
    )
  })

})

describe('api.setChainCompleteSet', () => {
  it('sends the chain-scoped complete-set decision', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ job_id: 'job-1' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    await api.setChainCompleteSet('job-1', 'SO/25-0282', true)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/workflow/jobs/job-1/chains/SO%2F25-0282/complete-set',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ complete_set: true }),
      }),
    )
  })
})
