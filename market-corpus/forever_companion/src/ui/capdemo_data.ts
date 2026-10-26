import type { TFunc } from "./types"

export type DemoScene = "chat" | "calendar" | "activity" | "mood" | "tone" | "fragments" | "journal" | "review"
export type DemoStory = {
  scene: DemoScene
  before: string
  after: string
  input: string
  process: string
  output: string
  example: string
  detail: string
}
type DemoStoryMap = Record<string, DemoStory>
type DemoUiMap = Record<string, string>

export const DEMO_STORIES: DemoStoryMap = {
  "whisper": {
    "scene": "chat",
    "before": "聊天照常，不添加阶段参考",
    "after": "状态有变化时，悄悄加入参考",
    "input": "今天状态怎么样？",
    "process": "检查频率与当前阶段",
    "output": "后台状态提示",
    "example": "今天想安静一点。",
    "detail": "提示仅供角色参考，不单独弹出消息。"
  },
  "phase_openers": {
    "scene": "calendar",
    "before": "阶段照常变化，不发送问候提示",
    "after": "进入新阶段，递出一次问候提示",
    "input": "进入新的阶段",
    "process": "检查是否已问候",
    "output": "一次问候提示",
    "example": "今天感觉轻松些了。",
    "detail": "具体表达由角色决定，同一阶段不重复。"
  },
  "activity_sense": {
    "scene": "activity",
    "before": "聊天照常，不添加活动参考",
    "after": "用概括状态，参考你的活动节奏",
    "input": "忙碌 / 暂时离开",
    "process": "检查隐私与活动信号",
    "output": "随状态提示加入",
    "example": "你先忙，等会儿再聊。",
    "detail": "只用宿主概括状态，不读屏幕；隐私时不添加。"
  },
  "anniversary": {
    "scene": "calendar",
    "before": "相处统计保留，不发送纪念提醒",
    "after": "到达纪念节点，后台提醒一次",
    "input": "相伴第 60 天",
    "process": "检查纪念节点与当天记录",
    "output": "后台纪念日提醒",
    "example": "原来已经相伴 60 天了。",
    "detail": "角色决定是否提起，不强制庆祝。"
  },
  "birthday": {
    "scene": "calendar",
    "before": "聊天照常，不发送生日提醒",
    "after": "生日当天首次聊天，递出提醒",
    "input": "生日日期与今天匹配",
    "process": "检查当天是否已提醒",
    "output": "生日提醒与可选记录",
    "example": "生日快乐，愿今天顺心。",
    "detail": "不计算年龄；可选记录由插件固定文案生成。"
  },
  "mood_engine": {
    "scene": "mood",
    "before": "聊天照常，不更新插件情绪状态",
    "after": "角色调用工具，状态与时限同步",
    "input": "角色选择表达开心",
    "process": "调用情绪工具，记录时限",
    "output": "更新情绪状态",
    "example": "暖流涌动 · 30 分钟",
    "detail": "到期只结束工具记录，不代表释怀；回应仍由角色决定。"
  },
  "tone_sense": {
    "scene": "tone",
    "before": "回复照常，不做后台语气分析",
    "after": "回复后分析语气，需要时递出提醒",
    "input": "谢谢你，刚才聊得很开心。",
    "process": "后台分析并更新心情参考",
    "output": "邀请记录或调整状态",
    "example": "可以记下这份开心。",
    "detail": "聊天无需等待；提醒不直接切换情绪动作。"
  },
  "fragments": {
    "scene": "fragments",
    "before": "已有片段保留，不新增自动片段",
    "after": "筛选明确表达，保存原话供检索",
    "input": "我喜欢雨后散步。",
    "process": "提取表达，检查置信度",
    "output": "保存原话片段",
    "example": "喜好 · 我喜欢雨后散步。",
    "detail": "可能遗漏或有误；角色需要时可调用工具检索。"
  },
  "journal": {
    "scene": "journal",
    "before": "已有书页保留，不发定期邀请",
    "after": "到达间隔，递出邀请与参考素材",
    "input": "距上次写作满 7 天",
    "process": "角色决定是否写作",
    "output": "愿意写时保存书页",
    "example": "这段时间，聊过不少小事。",
    "detail": "邀请不等于写作；保存内容不自动加入后续聊天。"
  },
  "review": {
    "scene": "review",
    "before": "已有总结保留，不生成新总结",
    "after": "素材达标后，后台整理相处总结",
    "input": "互动、语气、心情与片段",
    "process": "检查次数或天数，再整理",
    "output": "仅在我的日记展示",
    "example": "这段时间，互动较多，语气轻松。",
    "detail": "非角色亲笔，不是评分；不加入对话或宿主记忆。"
  }
}
export const DEMO_UI: DemoUiMap = {
  "before": "开启前",
  "after": "开启后",
  "play": "播放",
  "pause": "暂停",
  "replay": "重新播放",
  "previous": "上一步",
  "next": "下一步",
  "step": "步骤 {n}：{label}",
  "sample": "示例演示",
  "result": "结果",
  "input": "输入",
  "process": "处理",
  "output": "记录",
  "privacy": "隐私模式",
  "private": "不添加活动参考",
  "host": "宿主概括状态",
  "chat": "聊天",
  "character": "角色",
  "background": "后台",
  "saved": "已有记录保留",
  "choose": "角色决定",
  "write": "愿意写",
  "later": "暂时不写",
  "invitation": "邀请已递出",
  "notebook": "日记",
  "noAge": "不计算年龄",
  "once": "仅提醒一次",
  "optional": "可选记录",
  "local": "仅在面板",
  "analysis": "后台分析",
  "continues": "聊天照常",
  "noCapture": "不新增自动片段",
  "noInvite": "不发定期邀请",
  "noReview": "不生成新总结",
  "noHint": "不添加提示",
  "pending": "等待角色决定",
  "complete": "已保存",
  "sampleNotice": "示例不代表实际回复",
  "unavailable": "暂无演示",
  "notSaved": "未新增书页",
  "privacyNotice": "隐私模式下不添加活动参考",
  "backgroundOnly": "后台提醒，不直接切换情绪动作"
}

export function demoStory(t: TFunc, id: string): DemoStory | null {
  const source = DEMO_STORIES[id]
  if (!source) return null
  return {
    scene: source.scene,
    before: t(`panel.capdemo.${id}.before`, { defaultValue: source.before }),
    after: t(`panel.capdemo.${id}.after`, { defaultValue: source.after }),
    input: t(`panel.capdemo.${id}.input`, { defaultValue: source.input }),
    process: t(`panel.capdemo.${id}.process`, { defaultValue: source.process }),
    output: t(`panel.capdemo.${id}.output`, { defaultValue: source.output }),
    example: t(`panel.capdemo.${id}.example`, { defaultValue: source.example }),
    detail: t(`panel.capdemo.${id}.detail`, { defaultValue: source.detail }),
  }
}

export function demoUi(t: TFunc, key: string): string {
  return t(`panel.capdemo.${key}`, { defaultValue: DEMO_UI[key] || key })
}
