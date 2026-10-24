import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'qpet-school-course-'))

try {
  execFileSync(process.execPath, [
    join(here, '../../node_modules/typescript/bin/tsc'),
    join(here, 'school-course.ts'),
    '--module', 'esnext', '--target', 'es2020',
    '--moduleResolution', 'bundler', '--outDir', out,
  ], { stdio: 'pipe' })
  const { schoolCourseName } = await import(pathToFileURL(join(out, 'school-course.js')).href)

  const instituteCards = [
    ['力量', '短课', '领航员特训'],
    ['魅力', '短课', '巨星自我修养'],
    ['智力', '短课', '灵犀演说特训'],
    ['力量', '长课', '宗师格斗特训'],
    ['魅力', '长课', '歌剧院首席带演'],
    ['智力', '长课', '策略模拟推演'],
    ['夏令营', '长课', '云顶夏令营'],
  ]
  for (const [attribute, duration, expected] of instituteCards) {
    assert.equal(
      schoolCourseName('进修学院', attribute, duration),
      expected,
      `institute card ${attribute} ${duration} shows its course name`,
    )
  }

  const otherSchoolCards = [
    ['初级学园', '力量', '短课', '蹦蹦跳跳体能课'],
    ['初级学园', '智力', '短课', '看图识世界课'],
    ['初级学园', '魅力', '短课', '闪亮自我表达课'],
    ['初级学园', '力量', '长课', '田径运动课'],
    ['初级学园', '智力', '长课', '世界地理课'],
    ['初级学园', '魅力', '长课', '演说表达课'],
    ['初级学园', '夏令营', '长课', '萌芽夏令营'],
    ['中级学园', '力量', '短课', '障碍挑战课'],
    ['中级学园', '智力', '短课', '机器人拆解课'],
    ['中级学园', '魅力', '短课', '舞台表演课'],
    ['中级学园', '力量', '长课', '野外探险课'],
    ['中级学园', '智力', '长课', '星空观察课'],
    ['中级学园', '魅力', '长课', '料理实验课'],
    ['中级学园', '夏令营', '长课', '奇想夏令营'],
    ['高级学园', '魅力', '短课', '魅力穿搭课'],
    ['高级学园', '力量', '短课', '武术身法课'],
    ['高级学园', '智力', '短课', '爪爪算法课'],
    ['高级学园', '智力', '长课', '梦境编剧课'],
    ['高级学园', '力量', '长课', '力量觉醒课'],
    ['高级学园', '魅力', '长课', '舞台导演课'],
    ['高级学园', '夏令营', '长课', '星辉夏令营'],
  ]
  for (const [stage, attribute, duration, expected] of otherSchoolCards) {
    assert.equal(
      schoolCourseName(stage, attribute, duration),
      expected,
      `${stage} ${attribute} ${duration} shows the documented course name`,
    )
  }
  for (const attribute of ['力量', '智力', '魅力']) {
    const name = schoolCourseName('中级学园', attribute, '长课')
    assert.notEqual(
      name,
      `${attribute} · 长课`,
      'screenshot-confirmed intermediate long courses must not retain attribute placeholders',
    )
    assert.notEqual(
      name,
      '艺术实践课',
      'intermediate long courses must not display the obsolete course name',
    )
  }

  const summerCamps = [
    ['初级学园', '萌芽夏令营'],
    ['中级学园', '奇想夏令营'],
    ['高级学园', '星辉夏令营'],
    ['进修学院', '云顶夏令营'],
  ]
  for (const [stage, expected] of summerCamps) {
    for (const duration of ['短课', '长课', '45分钟', undefined, null, {}]) {
      assert.equal(
        schoolCourseName(stage, '夏令营', duration),
        expected,
        'the seventh card is independent of the regular course duration',
      )
    }
  }
  assert.equal(
    schoolCourseName(' 进修学院\n', '\t魅力 ', ' 长课 '),
    '歌剧院首席带演',
    'recognized strings are trimmed before matching',
  )

  for (const stage of ['未识别学园', '其他学院', '未知学院', '', undefined, null, {}]) {
    assert.equal(
      schoolCourseName(stage, '力量', '短课'),
      '力量 · 短课',
      'unmapped stages retain the selected attribute and kind without guessing a title',
    )
  }
  assert.equal(schoolCourseName('未知学院', ' 夏令营 ', ' 长课 '), '夏令营 · 长课')

  for (const duration of ['45分钟', '90分钟', 45, '', undefined, null, {}]) {
    assert.equal(
      schoolCourseName('进修学院', '力量', duration),
      '力量',
      'a recorded minute duration cannot be treated as a configured course kind',
    )
  }
  for (const attribute of ['', '体力', undefined, null, {}, ['力量']]) {
    assert.equal(
      schoolCourseName('进修学院', attribute, '短课'),
      '短课',
      'invalid attributes are omitted from the fallback label',
    )
  }
  assert.equal(
    schoolCourseName('其他学院', ' 智力 ', ' 长课 '),
    '智力 · 长课',
    'fallback values are trimmed without displaying the school name',
  )

  for (const value of [undefined, null, {}, [], 0, true]) {
    assert.equal(
      schoolCourseName(value, value, value),
      '待识别课程',
      'missing or malformed API values produce a readable placeholder',
    )
  }
  assert.equal(
    schoolCourseName('进修学院', { toString: () => '力量' }, { toString: () => '长课' }),
    '待识别课程',
    'object stringification cannot create a recognized course',
  )

  console.log('School course: complete catalog, obsolete name guards, duration guards and malformed data checks passed')
} finally {
  rmSync(out, { recursive: true, force: true })
}
