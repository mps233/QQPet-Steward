interface SchoolCourses {
  courses: Record<string, readonly [string, string]>
  summerCamp: string
}

// 只收录有出处的名字；按属性和档位查表，不能假定长课与短课的卡序相同。
// 课程名及核对来源见 docs/course-catalog.md。
const SCHOOL_COURSES: Record<string, SchoolCourses> = {
  初级学园: {
    courses: {
      力量: ['蹦蹦跳跳体能课', '田径运动课'],
      智力: ['看图识世界课', '世界地理课'],
      魅力: ['闪亮自我表达课', '演说表达课'],
    },
    summerCamp: '萌芽夏令营',
  },
  中级学园: {
    courses: {
      力量: ['障碍挑战课', '野外探险课'],
      智力: ['机器人拆解课', '星空观察课'],
      魅力: ['舞台表演课', '料理实验课'],
    },
    summerCamp: '奇想夏令营',
  },
  高级学园: {
    courses: {
      力量: ['武术身法课', '力量觉醒课'],
      智力: ['爪爪算法课', '梦境编剧课'],
      魅力: ['魅力穿搭课', '舞台导演课'],
    },
    summerCamp: '星辉夏令营',
  },
  进修学院: {
    courses: {
      力量: ['领航员特训', '宗师格斗特训'],
      魅力: ['巨星自我修养', '歌剧院首席带演'],
      智力: ['灵犀演说特训', '策略模拟推演'],
    },
    summerCamp: '云顶夏令营',
  },
}

const text = (value: unknown): string => typeof value === 'string' ? value.trim() : ''
const SUBJECTS = new Set(['力量', '智力', '魅力', '夏令营'])

/** 任务按配置选择的课程；duration 必须用配置档位，不能用结算的实际分钟数。 */
export function schoolCourseName(stage: unknown, attribute: unknown, duration: unknown): string {
  const rawAttribute = text(attribute)
  const subject = SUBJECTS.has(rawAttribute) ? rawAttribute : ''
  const rawDuration = text(duration)
  const kind = rawDuration === '短课' || rawDuration === '长课' ? rawDuration : ''
  const school = Object.prototype.hasOwnProperty.call(SCHOOL_COURSES, text(stage))
    ? SCHOOL_COURSES[text(stage)] : undefined

  if (school) {
    if (subject === '夏令营') return school.summerCamp
    const courses = school.courses[subject]
    const name = courses && kind ? courses[kind === '短课' ? 0 : 1] : null
    if (name) return name
  }
  return [subject, kind].filter(Boolean).join(' · ') || '待识别课程'
}
