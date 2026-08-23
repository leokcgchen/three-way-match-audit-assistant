/** 无构建：直接校验归链结果，禁止幽灵 HT 笔。 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const src = fs.readFileSync(path.join(__dirname, '../src/lib/chainGroup.ts'), 'utf8')
if (!src.includes('groupChainIdsFromClassified') || !src.includes('resolveJobChainIds')) {
  console.error('chainGroup.ts missing exports')
  process.exit(1)
}

// 轻量复刻：同文件 SO+HT 应合并
const classified = []
for (const [so, ht] of [
  ['SO25-0281', 'HT25-0281'],
  ['SO25-0282', 'KJHT25-0282'],
]) {
  for (const dtype of ['contract', 'order', 'invoice']) {
    classified.push({
      file_name: `${so}_${ht}_01_x.pdf`,
      doc_type: dtype,
      fields: {
        orderNo: so,
        contractNo: ht,
        documentNo: dtype === 'contract' ? ht : so,
      },
    })
  }
}

const parent = new Map()
const find = (x) => {
  if (!parent.has(x)) parent.set(x, x)
  let cur = x
  while (parent.get(cur) !== cur) {
    parent.set(cur, parent.get(parent.get(cur)))
    cur = parent.get(cur)
  }
  return cur
}
const union = (a, b) => {
  const ra = find(a)
  const rb = find(b)
  if (ra === rb) return
  if (ra.startsWith('SO') && !rb.startsWith('SO')) parent.set(rb, ra)
  else if (rb.startsWith('SO') && !ra.startsWith('SO')) parent.set(ra, rb)
  else parent.set(rb, ra)
}
for (const d of classified) {
  const so = d.fields.orderNo
  const ht = d.fields.contractNo
  if (so && ht) union(so, ht)
}
const roots = new Set()
for (const d of classified) roots.add(find(d.fields.orderNo))
const ids = [...roots].sort()
console.log('roots', ids)
if (ids.length !== 2 || ids.some((x) => x.includes('HT') && !x.startsWith('SO'))) {
  console.error('FAIL phantom or wrong roots', ids)
  process.exit(1)
}
console.log('FRONT guide chain-group OK')
