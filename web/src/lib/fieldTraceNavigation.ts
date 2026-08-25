export type FieldTraceTarget = {
  jobId: string
  chainId: string
  fileName: string
  fieldKey: string
}

const STORAGE_KEY = 'gospd.fieldTraceTarget'

export function storeFieldTraceTarget(target: FieldTraceTarget): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(target))
    sessionStorage.setItem('gospd.fieldViewMode', 'matrix')
  } catch {
    /* Navigation still works when session storage is unavailable. */
  }
}

export function consumeFieldTraceTarget(jobId: string, chainId: string): FieldTraceTarget | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<FieldTraceTarget>
    if (
      value.jobId !== jobId ||
      value.chainId !== chainId ||
      !value.fileName ||
      !value.fieldKey
    ) return null
    sessionStorage.removeItem(STORAGE_KEY)
    return value as FieldTraceTarget
  } catch {
    return null
  }
}
