import { describe, expect, it } from 'vitest'
import styles from '../styles.css?raw'

describe('PacketUnpackPage layout', () => {
  it('owns a bounded vertical scroll region inside the overflow-hidden app shell', () => {
    const rule = styles.match(/\.packet-review-workbench\s*\{([^}]*)\}/)?.[1] || ''

    expect(rule).toMatch(/flex:\s*1 1 auto/)
    expect(rule).toMatch(/min-height:\s*0/)
    expect(rule).toMatch(/overflow-y:\s*auto/)
  })

  it('keeps the large preview scrollable above the fixed gate', () => {
    const main = styles.match(/\.packet-review-main\s*\{([^}]*)\}/)?.[1] || ''
    const preview = styles.match(/\.packet-full-preview\s*\{([^}]*)\}/)?.[1] || ''
    const workbench = styles.match(/\.packet-review-workbench\s*\{([^}]*)\}/)?.[1] || ''

    expect(main).toMatch(/overflow-y:\s*auto/)
    expect(preview).toMatch(/max-height:/)
    expect(preview).toMatch(/overflow:\s*auto/)
    expect(workbench).toMatch(/padding-bottom:/)
  })
})
