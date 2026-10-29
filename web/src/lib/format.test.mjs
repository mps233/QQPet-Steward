import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'qpet-format-'))

try {
  execFileSync(process.execPath, [
    join(here, '../../node_modules/typescript/bin/tsc'),
    join(here, 'format.ts'),
    '--module', 'esnext', '--target', 'es2020',
    '--moduleResolution', 'bundler', '--outDir', out,
  ], { stdio: 'pipe' })
  const { formatUptime, parseUptimeSeconds, advanceUptime, countdownSeconds, hms, wan, wanExact } =
    await import(pathToFileURL(join(out, 'format.js')).href)

  const cases = [
    ['', ''],
    ['00:00:00', '00:00:00'],
    ['00:09:59', '00:09:59'],
    ['23:59:59', '23:59:59'],
    ['24:00:00', '1 \u5929 00:00:00'],
    ['25:01:02', '1 \u5929 01:01:02'],
    ['47:59:59', '1 \u5929 23:59:59'],
    ['48:00:00', '2 \u5929 00:00:00'],
    ['180:01:45', '7 \u5929 12:01:45'],
    ['2400:00:00', '100 \u5929 00:00:00'],
    ['1-00:00:00', '1 \u5929 00:00:00'],
    ['2-03:04:05', '2 \u5929 03:04:05'],
    ['09:59', '09:59'],
    ['invalid', 'invalid'],
    ['24:60:00', '24:60:00'],
    ['24:00:60', '24:00:60'],
    ['-1:00:00', '-1:00:00'],
    ['99999999999999999:00:00', '99999999999999999:00:00'],
  ]
  for (const [input, expected] of cases) {
    assert.equal(formatUptime(input), expected, `uptime: ${input}`)
  }
  const parseCases = [
    ['', null],
    ['00:00:00', 0],
    ['00:00:59', 59],
    ['09:59', 599],
    ['59:59', 3599],
    ['24:00:00', 86400],
    ['180:01:45', 648105],
    ['2-03:04:05', 183845],
    ['01-00:00:00', 86400],
    ['1-00:01', null],
    ['60:00', null],
    ['invalid', null],
    ['23:99:59', null],
    ['99999999999999999:00:00', null],
  ]
  for (const [input, expected] of parseCases) {
    assert.equal(parseUptimeSeconds(input), expected, `parse uptime: ${input}`)
  }
  const tickCases = [
    ['00:00:00', 999, '00:00:00'],
    ['00:00:00', 1000, '00:00:01'],
    ['00:00:59', 1000, '00:01:00'],
    ['00:59:59', 1000, '01:00:00'],
    ['23:59:59', 1000, '1 \u5929 00:00:00'],
    ['23:59:59', 2200, '1 \u5929 00:00:01'],
    ['47:59:59', 1000, '2 \u5929 00:00:00'],
    ['180:01:45', 3250, '7 \u5929 12:01:48'],
    ['2-03:04:05', 1000, '2 \u5929 03:04:06'],
    ['09:59', 1000, '00:10:00'],
    ['', 1000, ''],
    ['invalid', 1000, 'invalid'],
    ['25:01:02', -4000, '1 \u5929 01:01:02'],
    ['00:00:00', NaN, '00:00:00'],
    ['00:00:00', 11000, '00:00:11'],
  ]
  for (const [input, elapsed, expected] of tickCases) {
    assert.equal(advanceUptime(input, elapsed), expected, `advance uptime: ${input} + ${elapsed}ms`)
  }
  const countdownCases = [
    [undefined, 10000, null],
    [null, 10000, null],
    [0, 10000, null],
    [-1, 10000, null],
    [NaN, 10000, null],
    [Infinity, 10000, null],
    [20, NaN, null],
    [20, Infinity, null],
    [20, 10000, 10],
    [20, 11000, 9],
    [20, 19500, 1],
    [20, 20000, 0],
    [20, 30000, 0],
    [20.5, 20000, 1],
  ]
  for (const [target, now, expected] of countdownCases) {
    assert.equal(countdownSeconds(target, now), expected, `countdown: ${target} at ${now}ms`)
  }
  // Countdown formatting must not inherit the uptime's day conversion.
  assert.equal(hms(59), '00:59')
  assert.equal(hms(3600), '1:00:00')
  assert.equal(hms(86400), '24:00:00')
  const wanCases = [
    [0, '0'],
    [9999, '9999'],
    [10000, '1w'],
    [113000, '11.3w'],
    [105000, '10.5w'],
    [113999, '11.4w'],
    [1234567, '123.5w'],
  ]
  for (const [input, expected] of wanCases) {
    assert.equal(wan(input), expected, `wan: ${input}`)
  }
  // 职业树属性沿用游戏写法：最多两位小数、去尾零（真机实测 力量 1.18w）
  const wanExactCases = [
    [0, '0'],
    [878, '878'],
    [9999, '9999'],
    [10000, '1w'],
    [11800, '1.18w'],
    [12000, '1.2w'],
    [134000, '13.4w'],
    [118000, '11.8w'],
  ]
  for (const [input, expected] of wanExactCases) {
    assert.equal(wanExact(input), expected, `wanExact: ${input}`)
  }
  console.log(`Time formatting/clocks: ${cases.length + parseCases.length + tickCases.length + countdownCases.length + wanCases.length + wanExactCases.length + 3} checks passed`)
} finally {
  rmSync(out, { recursive: true, force: true })
}
