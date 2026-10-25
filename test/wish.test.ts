import { describe, expect, test } from "bun:test"
import { analyzeWish, buildWishPrompt } from "../wb-agent-lib.mjs"

describe("analyzeWish(许愿解析)", () => {
  test("定时提醒:中文数字与单位换算成秒", () => {
    const p = analyzeWish("每隔两小时提醒我喝水")
    expect(p.kind).toBe("reminder")
    expect(p.trigger).toBe("interval")
    expect(p.intervalSec).toBe(7200)
  })

  test("定时提醒:分钟/小时/天", () => {
    expect(analyzeWish("每隔 30 分钟提醒我起来活动").intervalSec).toBe(1800)
    expect(analyzeWish("每小时提醒喝水").intervalSec).toBe(3600)
    expect(analyzeWish("每天提醒我吃药").intervalSec).toBe(86400)
  })

  test("记账类不会被「每天」误判成提醒", () => {
    const p = analyzeWish("每天记账")
    expect(p.kind).toBe("notes")
    expect(p.storage).toBe(true)
    expect(p.intervalSec).toBe(0)
  })

  test("口语「记一下花了多少」也识别为记录存储", () => {
    expect(analyzeWish("帮我记一下花了多少钱").kind).toBe("notes")
  })

  test("没给间隔时只给默认值提示,不瞎算", () => {
    const p = analyzeWish("做个提醒插件")
    expect(p.kind).toBe("reminder")
    expect(p.intervalSec).toBe(0)
    expect(p.defaults.join()).toContain("3600")
  })

  test("联网 / 界面 / 命令 / 启动 信号", () => {
    expect(analyzeWish("查天气").network).toBe(true)
    expect(analyzeWish("做个看板显示统计").ui).toBe(true)
    expect(analyzeWish("帮我执行命令并返回结果").command).toBe(true)
    const s = analyzeWish("开机时跟我打招呼")
    expect(s.kind).toBe("greeting")
    expect(s.trigger).toBe("startup")
  })

  test("什么都没说清时归为 other,不硬猜", () => {
    const p = analyzeWish("做个插件")
    expect(p.kind).toBe("other")
    expect(p.storage).toBe(false)
    expect(p.ui).toBe(false)
  })
})

describe("buildWishPrompt(许愿式编程提示词)", () => {
  test("build 模式:带需求解析、交付验收、翻车点、默认值汇报", () => {
    const s = buildWishPrompt("每隔 30 分钟提醒我起来活动", "build", 0)
    expect(s).toContain("【用户的愿望】每隔 30 分钟提醒我起来活动")
    expect(s).toContain("【需求解析")
    expect(s).toContain("间隔:1800 秒")
    expect(s).toContain("【交付验收")
    expect(s).toContain("常见翻车点")
    expect(s).toContain("我替你定的默认")
    expect(s).toContain("[[WB-Q]]")
  })

  test("plan 模式:要方案四件事与 [[WB-PLAN]],不出现交付验收", () => {
    const s = buildWishPrompt("想做点东西", "plan", 0)
    expect(s).toContain("四件事")
    expect(s).toContain("[[WB-PLAN]]")
    expect(s).not.toContain("【交付验收")
  })

  test("问过一次后 build 模式禁止再提问", () => {
    expect(buildWishPrompt("查天气", "build", 1)).toContain("不许再提问")
    expect(buildWishPrompt("查天气", "build", 0)).toContain("最多问 1 个问题")
  })

  test("历史摘要与长期偏好都排在愿望前面", () => {
    const s = buildWishPrompt("记个账", "build", 0, "之前聊过 A", "【用户长期偏好(必须遵守)】\n1. 用莫兰迪色\n\n")
    const iMem = s.indexOf("更早对话的记忆摘要")
    const iUser = s.indexOf("用户长期偏好")
    const iWish = s.indexOf("【用户的愿望】")
    expect(iMem).toBeGreaterThanOrEqual(0)
    expect(iUser).toBeGreaterThan(iMem)
    expect(iWish).toBeGreaterThan(iUser)
  })

  test("提示词体积可控,不会挤掉正文", () => {
    const s = buildWishPrompt("每隔 30 分钟提醒我起来活动", "build", 0, "摘要".repeat(100))
    expect(s.length).toBeLessThan(4000)
  })
})
