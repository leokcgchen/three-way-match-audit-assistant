import { describe, expect, it } from 'vitest'
import type { PacketFile, PacketUnit } from '../types'
import {
  applyPacketCommand,
  businessIdsForUnit,
  confirmNormalUnits,
  intakeBlockers,
  mergeUnitWithNext,
  mergeUnitWithPrevious,
  reviewSummary,
  splitUnitAtPage,
} from './documentIntake'

function unit(overrides: Partial<PacketUnit> = {}): PacketUnit {
  return {
    unit_id: 'u1',
    source_file: 'packet.pdf',
    page_start: 1,
    page_end: 2,
    pages: [1, 2],
    doc_type: 'contract',
    chain_id: 'SO25-0281',
    business_ids: ['SO25-0281'],
    boundary_confirmed: false,
    needs_review: false,
    ...overrides,
  }
}

const files: PacketFile[] = [
  { file_name: 'packet.pdf', kind: 'packet_single_chain', page_count: 4 },
]

describe('businessIdsForUnit', () => {
  it('uses authoritative business_ids and removes duplicates', () => {
    const value = unit({
      business_ids: ['SO25-0282', 'SO25-0281', 'SO25-0282'],
      chain_id: 'SO25-9999',
    })

    expect(businessIdsForUnit(value)).toEqual(['SO25-0282', 'SO25-0281'])
  })

  it('falls back to the legacy chain only when business_ids is absent', () => {
    expect(businessIdsForUnit(unit({ business_ids: undefined, chain_id: 'SO25-0281' }))).toEqual([
      'SO25-0281',
    ])
    expect(businessIdsForUnit(unit({ business_ids: [], chain_id: 'SO25-0281' }))).toEqual([])
  })
})

describe('reviewSummary', () => {
  it('counts pages once and surfaces pending, unassigned, and anomalous units', () => {
    const units = [
      unit({ unit_id: 'normal', pages: [1, 2], page_start: 1, page_end: 2 }),
      unit({
        unit_id: 'unassigned',
        pages: [3],
        page_start: 3,
        page_end: 3,
        business_ids: [],
        needs_review: true,
      }),
      unit({
        unit_id: 'dropped',
        pages: [4],
        page_start: 4,
        page_end: 4,
        dropped: true,
      }),
    ]

    expect(reviewSummary(units, files)).toEqual({
      fileCount: 1,
      pageCount: 4,
      unitCount: 2,
      pendingCount: 2,
      unassignedCount: 1,
      anomalyCount: 1,
    })
  })
})

describe('confirmNormalUnits', () => {
  it('confirms only assigned, typed, non-anomalous units', () => {
    const values = [
      unit({ unit_id: 'normal' }),
      unit({ unit_id: 'review', needs_review: true }),
      unit({ unit_id: 'unassigned', business_ids: [] }),
      unit({ unit_id: 'unknown', doc_type: 'unresolved' }),
    ]

    const result = confirmNormalUnits(values)

    expect(result.find((item) => item.unit_id === 'normal')?.boundary_confirmed).toBe(true)
    expect(result.filter((item) => item.unit_id !== 'normal').every((item) => !item.boundary_confirmed)).toBe(
      true,
    )
  })
})

describe('splitUnitAtPage', () => {
  it('splits before the selected page and preserves every page exactly once', () => {
    const result = splitUnitAtPage(
      [unit({ pages: [1, 2, 3], page_start: 1, page_end: 3 })],
      'u1',
      3,
    )

    expect(result.map((item) => item.pages)).toEqual([[1, 2], [3]])
    expect(result.flatMap((item) => item.pages)).toEqual([1, 2, 3])
    expect(result.every((item) => item.boundary_confirmed)).toBe(true)
    expect(result[1].split_reason).toBe('manual_split')
  })

  it('does not split at the first page', () => {
    const original = [unit({ pages: [1, 2], page_start: 1, page_end: 2 })]

    expect(splitUnitAtPage(original, 'u1', 1)).toEqual(original)
  })
})

describe('mergeUnitWithPrevious', () => {
  it('merges only adjacent units from the same source file', () => {
    const values = [
      unit({ unit_id: 'u1', pages: [1, 2], page_start: 1, page_end: 2 }),
      unit({ unit_id: 'u2', pages: [3], page_start: 3, page_end: 3 }),
      unit({ unit_id: 'other', source_file: 'other.pdf', pages: [1], page_start: 1, page_end: 1 }),
    ]

    const result = mergeUnitWithPrevious(values, 'u2')

    expect(result.map((item) => [item.unit_id, item.pages])).toEqual([
      ['u1', [1, 2, 3]],
      ['other', [1]],
    ])
    expect(result[0].boundary_confirmed).toBe(true)
  })

  it('leaves non-adjacent units unchanged', () => {
    const values = [
      unit({ unit_id: 'u1', pages: [1], page_start: 1, page_end: 1 }),
      unit({ unit_id: 'u2', pages: [3], page_start: 3, page_end: 3 }),
    ]

    expect(mergeUnitWithPrevious(values, 'u2')).toEqual(values)
  })
})

describe('mergeUnitWithNext', () => {
  it('merges a selected unit with the next contiguous unit', () => {
    const values = [
      unit({ unit_id: 'u1', pages: [1], page_start: 1, page_end: 1 }),
      unit({ unit_id: 'u2', pages: [2], page_start: 2, page_end: 2 }),
    ]

    const result = mergeUnitWithNext(values, 'u1')

    expect(result.filter((item) => !item.dropped)).toHaveLength(1)
    expect(result[0].pages).toEqual([1, 2])
    expect(result[0].unit_id).toBe('u1')
  })

  it('does not merge across files or page gaps', () => {
    const values = [
      unit({ unit_id: 'u1', pages: [1], page_start: 1, page_end: 1 }),
      unit({ unit_id: 'u2', pages: [3], page_start: 3, page_end: 3 }),
    ]
    expect(mergeUnitWithNext(values, 'u1')).toEqual(values)
  })
})

describe('applyPacketCommand', () => {
  it('applies reversible drop and restore commands without mutating the input', () => {
    const original = [unit({ unit_id: 'u1' })]
    const dropped = applyPacketCommand(original, {
      type: 'drop',
      unitId: 'u1',
      reason: '空白页',
    })
    const restored = applyPacketCommand(dropped, { type: 'restore', unitId: 'u1' })

    expect(original[0].dropped).not.toBe(true)
    expect(dropped[0]).toMatchObject({ dropped: true, drop_reason: '空白页' })
    expect(restored[0]).toMatchObject({ dropped: false, boundary_confirmed: false })
  })
})

describe('intakeBlockers', () => {
  it('returns concrete blockers for ownership, boundary, type, and AI anomalies', () => {
    const blockers = intakeBlockers(
      [
        unit({
          unit_id: 'problem',
          business_ids: [],
          doc_type: 'unresolved',
          boundary_confirmed: false,
          needs_review: true,
        }),
      ],
      files,
    )

    expect(blockers.map((item) => item.code)).toEqual([
      'unassigned',
      'boundary_unconfirmed',
      'type_unresolved',
      'needs_review',
    ])
    expect(blockers.every((item) => item.unitId === 'problem')).toBe(true)
  })

  it('ignores dropped units', () => {
    expect(
      intakeBlockers(
        [unit({ dropped: true, business_ids: [], doc_type: 'unresolved', needs_review: true })],
        files,
      ),
    ).toEqual([])
  })
})
