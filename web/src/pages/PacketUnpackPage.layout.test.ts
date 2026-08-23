import { describe, expect, it } from 'vitest'
import styles from '../styles.css?raw'

describe('PacketUnpackPage layout', () => {
  it('owns a bounded vertical scroll region inside the overflow-hidden app shell', () => {
    const rule = styles.match(/\.packet-review-workbench\s*\{([^}]*)\}/)?.[1] || ''

    expect(rule).toMatch(/flex:\s*1 1 auto/)
    expect(rule).toMatch(/min-height:\s*0/)
    expect(rule).toMatch(/overflow-y:\s*auto/)
  })
})
