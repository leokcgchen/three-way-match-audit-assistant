import { useEffect, useState } from 'react'

type TipState = {
  text: string
  x: number
  y: number
  place: 'below' | 'above'
  align: 'left' | 'center' | 'right'
}

function tipFromTarget(target: EventTarget | null): HTMLElement | null {
  if (!(target instanceof Element)) return null
  return target.closest('[data-tip]') as HTMLElement | null
}

/**
 * 全局悬停说明：任意元素写 data-tip="…" 即可。
 * 用 fixed 层，避免被 overflow:hidden 的分栏裁掉。
 */
export function TipHost() {
  const [tip, setTip] = useState<TipState | null>(null)

  useEffect(() => {
    let timer = 0
    let current: HTMLElement | null = null

    const hide = () => {
      window.clearTimeout(timer)
      current = null
      setTip(null)
    }

    const place = (el: HTMLElement) => {
      const text = (el.getAttribute('data-tip') || '').replace(/\s+/g, ' ').trim()
      if (!text) {
        hide()
        return
      }
      const r = el.getBoundingClientRect()
      const vw = window.innerWidth
      const vh = window.innerHeight
      // 长文案按换行估算高度，避免贴底时整段被裁
      const approxLines = Math.min(12, Math.max(1, Math.ceil(text.length / 28)))
      const tipH = Math.min(vh * 0.5, 16 + approxLines * 18)
      const tipW = Math.min(36 * 16, vw - 24)
      const spaceBelow = vh - r.bottom - 12
      const spaceAbove = r.top - 12
      const above = spaceBelow < tipH && spaceAbove > spaceBelow
      const mid = r.left + r.width / 2
      let align: TipState['align'] = 'center'
      let x = mid
      if (r.left > vw * 0.58 || mid + tipW / 2 > vw - 12) {
        align = 'right'
        x = Math.min(vw - 12, r.right)
      } else if (r.right < vw * 0.42 || mid - tipW / 2 < 12) {
        align = 'left'
        x = Math.max(12, r.left)
      }
      let y = above ? r.top - 8 : r.bottom + 8
      if (above) {
        y = Math.max(8 + tipH, y)
      } else {
        y = Math.min(vh - 8, y)
      }
      setTip({
        text,
        x,
        y,
        place: above ? 'above' : 'below',
        align,
      })
    }

    const schedule = (el: HTMLElement | null) => {
      window.clearTimeout(timer)
      if (!el) {
        hide()
        return
      }
      current = el
      timer = window.setTimeout(() => {
        if (current === el) place(el)
      }, 160)
    }

    const onMove = (e: MouseEvent) => {
      schedule(tipFromTarget(e.target))
    }
    const onFocus = (e: FocusEvent) => {
      const el = tipFromTarget(e.target)
      if (el) {
        window.clearTimeout(timer)
        current = el
        place(el)
      }
    }
    const onBlur = (e: FocusEvent) => {
      if (!e.relatedTarget || !tipFromTarget(e.relatedTarget)) hide()
    }

    document.addEventListener('mouseover', onMove)
    document.addEventListener('focusin', onFocus)
    document.addEventListener('focusout', onBlur)
    window.addEventListener('scroll', hide, true)
    window.addEventListener('resize', hide)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('mouseover', onMove)
      document.removeEventListener('focusin', onFocus)
      document.removeEventListener('focusout', onBlur)
      window.removeEventListener('scroll', hide, true)
      window.removeEventListener('resize', hide)
    }
  }, [])

  if (!tip) return null
  return (
    <div
      className={`ui-tip${tip.place === 'above' ? ' is-above' : ''}${
        tip.align === 'left' ? ' is-left' : tip.align === 'right' ? ' is-right' : ''
      }`}
      style={{ left: tip.x, top: tip.y }}
      role="tooltip"
    >
      {tip.text}
    </div>
  )
}
