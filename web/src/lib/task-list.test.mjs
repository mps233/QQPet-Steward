import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'qpet-task-list-'))

try {
  execFileSync(process.execPath, [
    join(here, '../../node_modules/typescript/bin/tsc'),
    join(here, 'task-list.ts'),
    '--module', 'esnext', '--target', 'es2020',
    '--moduleResolution', 'bundler', '--outDir', out,
  ], { stdio: 'pipe' })
  const { taskListSnapshot } = await import(pathToFileURL(join(out, 'task-list.js')).href)
  const known = Object.freeze(['care', 'school', 'gift_bag', 'adventure', 'visit', 'pk', 'work'])
  const initialOrder = Object.freeze(['care', 'gift_bag', 'visit', 'pk', 'adventure', 'school', 'work'])
  const initialEnabled = Object.freeze({ care: true, gift_bag: true, work: false })
  const noTasks = Object.freeze({})

  const initial = taskListSnapshot(noTasks, initialOrder, initialEnabled, known)
  assert.deepEqual(Object.keys(initial), initialOrder, 'fresh users see their configured tasks')
  assert.equal(initial.work.state, 'disabled', 'disabled tasks remain visible and disabled')
  assert.equal(initial.care.state, 'ready')
  assert.deepEqual(noTasks, {}, 'the API snapshot must not be mutated')

  const runtime = Object.freeze({
    work: Object.freeze({ state: 'waiting', next: '2026-10-24 18:00:00', next_ts: 1792836000 }),
    school: Object.freeze({ state: 'done' }),
    care: Object.freeze({ state: 'disabled' }),
  })
  assert.equal(
    taskListSnapshot(runtime, initialOrder, initialEnabled, known),
    runtime,
    'live state, row order and timing come from the scheduler without replacement',
  )

  const limited = taskListSnapshot({}, ['work', 'care', 'work', 'hire_friend', 'unknown'], { work: false }, known)
  assert.deepEqual(Object.keys(limited), ['work', 'care'], 'explicitly omitted tasks stay omitted')
  assert.equal(limited.work.state, 'disabled', 'disabled rows retain their configured position')
  assert.equal(limited.care.state, 'ready')

  for (const order of [undefined, null, '', [], [null, 'unknown', 'hire_friend']]) {
    const fallback = taskListSnapshot(undefined, order, undefined, known)
    assert.deepEqual(Object.keys(fallback), known, 'missing or obsolete order still shows known tasks')
    assert.ok(Object.values(fallback).every((task) => task.state === 'ready'))
  }

  console.log('Task list: first-run, live snapshot, disabled ordering and missing config checks passed')
} finally {
  rmSync(out, { recursive: true, force: true })
}
