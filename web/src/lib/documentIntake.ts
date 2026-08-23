import type { PacketFile, PacketUnit } from '../types'

const UNIDENTIFIED = new Set(['', '未识别业务号', 'unidentified', 'unresolved'])
const UNRESOLVED_TYPES = new Set(['', 'other', 'unresolved'])

export type IntakeBlocker = {
  unitId: string
  sourceFile: string
  code: 'unassigned' | 'boundary_unconfirmed' | 'type_unresolved' | 'needs_review'
  message: string
}

export type ReviewSummary = {
  fileCount: number
  pageCount: number
  unitCount: number
  pendingCount: number
  unassignedCount: number
  anomalyCount: number
}

function unique(values: Array<string | null | undefined>): string[] {
  const out: string[] = []
  for (const raw of values) {
    const value = String(raw || '').trim()
    if (UNIDENTIFIED.has(value.toLocaleLowerCase()) || out.includes(value)) continue
    out.push(value)
  }
  return out
}

export function businessIdsForUnit(unit: PacketUnit): string[] {
  if (unit.business_ids !== undefined) return unique(unit.business_ids)
  return unique([unit.chain_id])
}

export function reviewSummary(units: PacketUnit[], files: PacketFile[]): ReviewSummary {
  const visible = units.filter((unit) => !unit.dropped)
  const declaredPages = files.reduce((total, file) => total + Math.max(0, file.page_count || 0), 0)
  const observedPages = new Set(
    units.flatMap((unit) => unit.pages.map((page) => `${unit.source_file}:${page}`)),
  ).size
  return {
    fileCount: files.length,
    pageCount: declaredPages || observedPages,
    unitCount: visible.length,
    pendingCount: visible.filter((unit) => !unit.boundary_confirmed).length,
    unassignedCount: visible.filter((unit) => businessIdsForUnit(unit).length === 0).length,
    anomalyCount: visible.filter((unit) => Boolean(unit.needs_review)).length,
  }
}

export function confirmNormalUnits(units: PacketUnit[]): PacketUnit[] {
  return units.map((unit) => {
    const docType = String(unit.doc_type || '').toLocaleLowerCase()
    const confirmable =
      !unit.dropped &&
      !unit.needs_review &&
      businessIdsForUnit(unit).length > 0 &&
      !UNRESOLVED_TYPES.has(docType)
    return confirmable ? { ...unit, boundary_confirmed: true } : unit
  })
}

export function splitUnitAtPage(
  units: PacketUnit[],
  unitId: string,
  splitPage: number,
): PacketUnit[] {
  const index = units.findIndex((unit) => unit.unit_id === unitId)
  if (index < 0) return units
  const selected = units[index]
  const pages = [...new Set(selected.pages)].sort((a, b) => a - b)
  const splitIndex = pages.indexOf(splitPage)
  if (splitIndex <= 0) return units
  const leftPages = pages.slice(0, splitIndex)
  const rightPages = pages.slice(splitIndex)
  const left: PacketUnit = {
    ...selected,
    pages: leftPages,
    page_start: leftPages[0],
    page_end: leftPages[leftPages.length - 1],
    split_reason: 'manual_split',
    boundary_confirmed: true,
    needs_review: false,
  }
  const right: PacketUnit = {
    ...selected,
    unit_id: `${selected.unit_id}__p${splitPage}`,
    pages: rightPages,
    page_start: rightPages[0],
    page_end: rightPages[rightPages.length - 1],
    split_reason: 'manual_split',
    boundary_confirmed: true,
    needs_review: false,
  }
  return [...units.slice(0, index), left, right, ...units.slice(index + 1)]
}

export function mergeUnitWithPrevious(units: PacketUnit[], unitId: string): PacketUnit[] {
  const selected = units.find((unit) => unit.unit_id === unitId)
  if (!selected || selected.dropped) return units
  const sameFile = units
    .filter((unit) => !unit.dropped && unit.source_file === selected.source_file)
    .sort((a, b) => a.page_start - b.page_start)
  const selectedIndex = sameFile.findIndex((unit) => unit.unit_id === unitId)
  if (selectedIndex <= 0) return units
  const previous = sameFile[selectedIndex - 1]
  if (previous.page_end + 1 !== selected.page_start) return units
  const pages = [...new Set([...previous.pages, ...selected.pages])].sort((a, b) => a - b)
  const businessIds = unique([
    ...businessIdsForUnit(previous),
    ...businessIdsForUnit(selected),
  ])
  const merged: PacketUnit = {
    ...previous,
    pages,
    page_start: pages[0],
    page_end: pages[pages.length - 1],
    business_ids: businessIds,
    chain_id: businessIds[0] || previous.chain_id,
    split_reason: 'manual_merge',
    boundary_confirmed: true,
    needs_review: false,
  }
  return units
    .filter((unit) => unit.unit_id !== selected.unit_id)
    .map((unit) => (unit.unit_id === previous.unit_id ? merged : unit))
}

export function mergeUnitWithNext(units: PacketUnit[], unitId: string): PacketUnit[] {
  const selected = units.find((unit) => unit.unit_id === unitId)
  if (!selected || selected.dropped) return units
  const sameFile = units
    .filter((unit) => !unit.dropped && unit.source_file === selected.source_file)
    .sort((a, b) => a.page_start - b.page_start)
  const selectedIndex = sameFile.findIndex((unit) => unit.unit_id === unitId)
  if (selectedIndex < 0 || selectedIndex >= sameFile.length - 1) return units
  const next = sameFile[selectedIndex + 1]
  if (selected.page_end + 1 !== next.page_start) return units
  const pages = [...new Set([...selected.pages, ...next.pages])].sort((a, b) => a - b)
  const businessIds = unique([
    ...businessIdsForUnit(selected),
    ...businessIdsForUnit(next),
  ])
  const merged: PacketUnit = {
    ...selected,
    pages,
    page_start: pages[0],
    page_end: pages[pages.length - 1],
    business_ids: businessIds,
    chain_id: businessIds[0] || selected.chain_id,
    split_reason: 'manual_merge',
    boundary_confirmed: true,
    needs_review: false,
  }
  return units
    .filter((unit) => unit.unit_id !== next.unit_id)
    .map((unit) => (unit.unit_id === selected.unit_id ? merged : unit))
}

export type PacketCommand =
  | { type: 'split'; unitId: string; page: number }
  | { type: 'merge_previous'; unitId: string }
  | { type: 'merge_next'; unitId: string }
  | { type: 'drop'; unitId: string; reason?: string }
  | { type: 'restore'; unitId: string }

export function applyPacketCommand(units: PacketUnit[], command: PacketCommand): PacketUnit[] {
  if (command.type === 'split') return splitUnitAtPage(units, command.unitId, command.page)
  if (command.type === 'merge_previous') return mergeUnitWithPrevious(units, command.unitId)
  if (command.type === 'merge_next') return mergeUnitWithNext(units, command.unitId)
  return units.map((unit) => {
    if (unit.unit_id !== command.unitId) return unit
    if (command.type === 'restore') {
      return { ...unit, dropped: false, drop_reason: undefined, boundary_confirmed: false }
    }
    return {
      ...unit,
      dropped: true,
      drop_reason: command.reason || 'manual_drop',
      boundary_confirmed: true,
    }
  })
}

export function intakeBlockers(units: PacketUnit[], files: PacketFile[]): IntakeBlocker[] {
  const multiPageFiles = new Set(
    files.filter((file) => (file.page_count || 0) > 1).map((file) => file.file_name),
  )
  const blockers: IntakeBlocker[] = []
  for (const unit of units) {
    if (unit.dropped) continue
    const common = { unitId: unit.unit_id, sourceFile: unit.source_file }
    if (businessIdsForUnit(unit).length === 0) {
      blockers.push({ ...common, code: 'unassigned', message: '尚未确认业务归属' })
    }
    if (multiPageFiles.has(unit.source_file) && !unit.boundary_confirmed) {
      blockers.push({ ...common, code: 'boundary_unconfirmed', message: '尚未确认拆包边界' })
    }
    if (UNRESOLVED_TYPES.has(String(unit.doc_type || '').toLocaleLowerCase())) {
      blockers.push({ ...common, code: 'type_unresolved', message: '尚未确认单据类型' })
    }
    if (unit.needs_review) {
      blockers.push({ ...common, code: 'needs_review', message: 'AI 标记为需要人工复核' })
    }
  }
  return blockers
}
