// 日记页：时间线 / 带摘录的日记目录 / 正文优先的阅读页。
// 正文分节（【这段时间】等）在**显示层**解析：存储与注入文本一个字都不改，
// 那些小标题是 prompt 共用的（core/journal.py 中文声明制），改不得。
// hosted-tsx 约束：唯一 export 在任何 JSX 闭合标签之前；辅助组件放文件尾部靠函数声明提升被引用
import { Button, Card, EmptyState, Tabs, useToast } from "@neko/plugin-ui"
import { useEffect, useLocalState, useRef, useState } from "@neko/plugin-ui"
import { BOOK_STYLES } from "./styles_book"
import type {
  DiaryItem, JournalPage, JournalPageHeader, JournalArchiveBrief, JournalWrite, ReviewArchiveBrief, ReviewBrief, ReviewEntry, ReviewProgress, TFunc,
} from "./types"
import { diaryWriteError, fragmentKindKey, journalTrendKey, toneLabelKey } from "./utils"

type CatalogView = { query: string; page: number }

export function DiaryPane(props: {
  key?: string
  t: TFunc
  diary: DiaryItem[]
  diaryTotal: number
  fragmentTotal: number
  journalIndex: JournalPageHeader[]
  // 藏书阁（1.3.0）：活架写满下架的旧页合订本——概览进轮询，全量按需拉取；
  // 面板据此在书架末尾画一根横放书脊，点开即只读翻阅
  journalArchiveBrief?: JournalArchiveBrief
  invitePending?: boolean
  journalWrite?: JournalWrite
  journalSubmitting?: boolean
  reviewSubmitting?: boolean
  lanlan?: string
  // 用 types.ts 的 ReviewBrief（含 1.2.3 的 writing/last_result）：内联复刻一份
  // 窄类型会让 reviewBrief.writing 过不了 hosted-tsx 的类型检查（TS2339）
  reviewBrief?: ReviewBrief
  // 档案室（1.3.0）：活架攒满下架的旧卷宗合档——与藏书阁同款：概览进轮询，
  // 全量按需拉取；面板据此在档案架末尾画那只档案盒，点开即只读翻阅
  reviewArchiveBrief?: ReviewArchiveBrief
  onDeleteFragment: (ts: string) => void
  onLoadJournal: () => Promise<JournalPage[]>
  // 失败回 null（区别于"真的还没有合订本"的空数组）：面板据此弹错，不再静默
  onLoadJournalArchive: () => Promise<JournalPage[] | null>
  onLoadMoreDiary: (offset: number) => Promise<{ items: DiaryItem[]; hasMore: boolean }>
  onInviteJournal: () => void
  onLoadReview: () => Promise<{ entries: ReviewEntry[]; progress: ReviewProgress }>
  // 档案室全量拉取（失败回 null 区别于真空列表，面板据此弹错不静默）
  onLoadReviewArchive: () => Promise<ReviewEntry[] | null>
  onWriteReviewNow: () => void
  settingsChildren?: any
}) {
  const {
    t, diary, diaryTotal, fragmentTotal, journalIndex, journalArchiveBrief, invitePending, lanlan, reviewBrief,
    reviewArchiveBrief,
    journalWrite, journalSubmitting, reviewSubmitting,
    onDeleteFragment, onLoadJournal, onLoadJournalArchive, onLoadMoreDiary, onInviteJournal,
    onLoadReview, onLoadReviewArchive, onWriteReviewNow, settingsChildren,
  } = props
  const toast = useToast()

  // 页内 Tab 持久化：像顶部页签一样记住上次停留在哪一本
  const [tab, setTab] = useLocalState("tide.diary.tab", "time")

  // ---- 时光日记：dashboard 初始 12 条 + "加载更多"追加分页 ----
  const [fullDiary, setFullDiary] = useState<DiaryItem[] | null>(null)
  const [diaryHasMore, setDiaryHasMore] = useState(false)
  const [diaryLoading, setDiaryLoading] = useState(false)
  const timeline = fullDiary || diary
  // ---- 个人日记：整本（目录数据源）+ 当前翻开页（null = 目录视图）----
  const [pages, setPages] = useState<JournalPage[]>([])
  const [bookLoaded, setBookLoaded] = useState(false)
  const [bookLoading, setBookLoading] = useState(false)
  const [openPageNo, setOpenPageNo] = useState<number | null>(null)
  const [journalCatalog, setJournalCatalog] = useState<CatalogView>({ query: "", page: 0 })
  const [reviewCatalog, setReviewCatalog] = useState<CatalogView>({ query: "", page: 0 })
  const openIdx = openPageNo === null ? null : pages.findIndex((p) => (p.page_no || 0) === openPageNo)
  const visiblePage = openIdx !== null && openIdx >= 0 ? pages[openIdx] : null
  // 书页指纹（1.2.3）：页码:段数:末笔时刻 逐页拼接。mood_journal_write 默认
  // 续写在当前页——页数不变，只看页数的旧检测让她续写的段落永远等不到自动
  // 刷新；指纹把"段数/末笔时刻"也纳入观察面
  const bookFingerprint = journalIndex
    .map((p) => `${p.page_no || 0}:${p.entry_count || 0}:${p.last_ts || ""}:${p.title || ""}`)
    .join("|")
  const bookFpSeen = useRef<string | null>(null)
  // ---- 藏书阁（1.3.0）：合订本按需拉取 + 当前翻阅页（按倒序从最新一本数起）----
  const [archivePages, setArchivePages] = useState<JournalPage[]>([])
  const [archiveLoaded, setArchiveLoaded] = useState(false)
  const [archiveLoading, setArchiveLoading] = useState(false)
  const [openArchiveNo, setOpenArchiveNo] = useState<number | null>(null)
  // 翻阅序：入阁序时间正序，显示倒序（最新在前）——新页追加不抽旧页的位，
  // 位置数才不会随入阁平移（1.3.0 显示层纪律）
  const archiveList = archivePages.slice().reverse()
  const archIdx = openArchiveNo === null ? null : archiveList.findIndex((p) => (p.page_no || 0) === openArchiveNo)
  const visibleArchive = archIdx !== null && archIdx >= 0 ? archiveList[archIdx] : null
  const archiveCount = Number((journalArchiveBrief && journalArchiveBrief.pages) || 0)
  // ---- 我的日记：全部篇目（倒序）+ 素材进度 + 当前翻开篇（null = 目录视图）----
  const [reviewEntries, setReviewEntries] = useState<ReviewEntry[]>([])
  const [reviewProgress, setReviewProgress] = useState<ReviewProgress>({})
  const [reviewLoaded, setReviewLoaded] = useState(false)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [openReviewTs, setOpenReviewTs] = useState<string | null>(null)
  const visibleReview = openReviewTs === null ? null
    : (reviewEntries.find((item) => String(item.ts || "") === openReviewTs) || null)
  // 跨卷翻页（1.3.0）：篇目按时间倒序（新卷在前），前一卷=列表前一项（更新），
  // 后一卷=后一项（更旧）——与日记本同口径只翻当前列表，不跨架串档
  const reviewIdx = openReviewTs === null ? -1
    : reviewEntries.findIndex((item) => String(item.ts || "") === openReviewTs)
  // 档案室（1.3.0）：旧卷宗合档——状态机与藏书阁同款（懒拉、brief 卷数过期判定、
  // 翻阅中不自动重拉：淘汰低频且翻阅按 ts 定位，新卷入档不挪位）
  const [reviewArchive, setReviewArchive] = useState<ReviewEntry[]>([])
  const [reviewArchiveLoaded, setReviewArchiveLoaded] = useState(false)
  const [reviewArchiveLoading, setReviewArchiveLoading] = useState(false)
  const [openReviewArchiveTs, setOpenReviewArchiveTs] = useState<string | null>(null)
  const reviewArchiveCount = Number((reviewArchiveBrief && reviewArchiveBrief.entries) || 0)
  const reviewArchIdx = openReviewArchiveTs === null ? -1
    : reviewArchive.findIndex((item) => String(item.ts || "") === openReviewArchiveTs)
  const visibleReviewArchive = reviewArchIdx >= 0 ? reviewArchive[reviewArchIdx] : null

  // 重拉我的日记（挂载/切角色/轮询发现写完新篇时自动共用）
  async function reloadReview() {
    if (reviewLoading) return
    setReviewLoading(true)
    try {
      const loaded = await onLoadReview()
      setReviewEntries(loaded.entries || [])
      setReviewProgress(loaded.progress || {})
      setReviewLoaded(true)
    } catch (err) {
      console.warn("[forever_companion] load review failed:", err)
      setReviewLoaded(true)
    } finally {
      setReviewLoading(false)
    }
  }

  // 翻开档案室（1.3.0）：按需拉全量（brief 卷数变化即视为过期），从最新一卷读起
  async function openReviewArchive() {
    if (reviewArchiveLoading) return
    let list = reviewArchive
    if (!reviewArchiveLoaded || list.length !== reviewArchiveCount) {
      setReviewArchiveLoading(true)
      try {
        const fetched = await onLoadReviewArchive()
        if (fetched === null) {
          // 与藏书阁同款：拉不到当场说清，不演"点了没反应"
          toast.error(t("panel.review.archiveLoadError", { defaultValue: "归档加载失败，请稍后重试" }))
          return
        }
        list = fetched
        setReviewArchive(list)
        setReviewArchiveLoaded(true)
      } catch (err) {
        console.warn("[forever_companion] load review archive failed:", err)
        toast.error(t("panel.review.archiveLoadError", { defaultValue: "归档加载失败，请稍后重试" }))
        return
      } finally {
        setReviewArchiveLoading(false)
      }
    }
    if (!list.length) return
    setOpenReviewArchiveTs(String(list[0].ts || ""))
  }

  // 重拉个人日记整本（挂载/切角色/轮询发现她写了新页或续写时自动共用）
  async function reloadBook() {
    if (bookLoading) return
    setBookLoading(true)
    try {
      const loaded = await onLoadJournal()
      setPages(loaded)
      setBookLoaded(true)
      } catch (err) {
        // 失败也终止 loading，显示空态；console 留痕便于排查（如动作未暴露 403）
        console.warn("[forever_companion] load journal failed:", err)
        setBookLoaded(true)
      } finally {
      setBookLoading(false)
    }
  }

  // 切角色：重置时光日记分页与翻开的页码 + 藏书阁与我的日记状态，重拉日记本
  useEffect(() => {
    setFullDiary(null)
    setDiaryHasMore(false)
    setOpenPageNo(null)
    setJournalCatalog({ query: "", page: 0 })
    setReviewCatalog({ query: "", page: 0 })
    setOpenReviewTs(null)
    setOpenReviewArchiveTs(null)
    setReviewArchive([])
    setReviewArchiveLoaded(false)
    setOpenArchiveNo(null)
    setArchivePages([])
    setArchiveLoaded(false)
    bookFpSeen.current = null  // 指纹基线作废，按新角色的书重认（1.2.3）
    reloadBook()
    reloadReview()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lanlan])

  // 她在面板开着的时候写了/续写了：journal_index 指纹变化 → 自动重拉整本
  //（1.2.3：检测面从页数升级为 页码:段数:末笔时刻 指纹——续写不动页数，
  // 旧逻辑下她连写几段面板毫无动静，只能手动刷新）
  useEffect(() => {
    if (bookFpSeen.current === null) {
      bookFpSeen.current = bookFingerprint
      return
    }
    if (bookFingerprint !== bookFpSeen.current) {
      bookFpSeen.current = bookFingerprint
      if (bookLoaded) reloadBook()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookFingerprint])

  // 满架时新篇替换旧篇，篇数不变；同时观察最后成文时间。
  useEffect(() => {
    if (reviewLoaded && reviewBrief && (
      Number(reviewBrief.entries || 0) !== reviewEntries.length
      || (reviewBrief.last_ts && reviewBrief.last_ts !== String(reviewEntries[0]?.ts || ""))
    )) {
      reloadReview()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewBrief && reviewBrief.entries, reviewBrief && reviewBrief.last_ts])

  async function loadMore() {
    if (diaryLoading) return
    setDiaryLoading(true)
    try {
      const base = fullDiary || diary
      const seen = new Set(base.map((item) => String(item.ts || "")))
      const res = await onLoadMoreDiary(base.length)
      const fresh = (res.items || []).filter((item) => !seen.has(String(item.ts || "")))
      setFullDiary([...base, ...fresh])
      setDiaryHasMore(!!res.hasMore)
    } catch (err) {
      // 加载失败静默：按钮恢复可点，下次重试；console 留痕便于排查
      console.warn("[forever_companion] load more diary failed:", err)
    } finally {
      setDiaryLoading(false)
    }
  }

  // 翻开合订本（1.3.0）：按需拉全量（brief 本数变化即视为过期），从最新一本读起。
  // 翻阅中不自动重拉：淘汰一周至多一次，且翻阅按 page_no 定位、新页入阁不挪位
  async function openArchive() {
    if (archiveLoading) return
    let list = archivePages
    if (!archiveLoaded || list.length !== archiveCount) {
      setArchiveLoading(true)
      try {
        const fetched = await onLoadJournalArchive()
        if (fetched === null) {
          // 面板不再"点了没反应"：拉取失败当场说清（1.3.0 真机验收反馈背锅位）
          toast.error(t("panel.journal.archiveLoadError", { defaultValue: "归档加载失败，请稍后重试" }))
          return
        }
        list = fetched
        setArchivePages(list)
        setArchiveLoaded(true)
      } catch (err) {
        console.warn("[forever_companion] load journal archive failed:", err)
        toast.error(t("panel.journal.archiveLoadError", { defaultValue: "归档加载失败，请稍后重试" }))
        return
      } finally {
        setArchiveLoading(false)
      }
    }
    if (!list.length) return
    setOpenArchiveNo(Number(list[list.length - 1].page_no || 0))
  }

  return (
    <div className="tm-pane">
      {/* 书本层样式与 PANEL_STYLES 并列注入（后注入 = 同特异性下胜出）；
          UA 样式表里 style{display:none}，不会成为 .tm-pane 的 grid 项 */}
      <style key="tmb-book-styles">{BOOK_STYLES}</style>
      <Tabs
        id="tide-diary-tabs"
        activeId={tab}
        onChange={(id: string) => { setTab(id) }}
        items={[
          {
            id: "time",
            label: t("panel.diary.tabTime", { defaultValue: "时光日记" }),
            content: (
              <Card title={t("panel.diary.title", { defaultValue: "时光日记" })}>
                {timeline.length > 0 ? (
                  <div className="tm-diary">
                    <div className="tm-diary-toolbar">
                      <span className="tm-diary-count">
                        {t("panel.diary.summary", { defaultValue: "共 {total} 条 · 手记 {self} · 对话片段 {auto}" })
                          .replace("{total}", String(diaryTotal))
                          .replace("{self}", String(diaryTotal - fragmentTotal))
                          .replace("{auto}", String(fragmentTotal))}
                      </span>
                    </div>
                    <TimelineView t={t} items={timeline} onDelete={onDeleteFragment} />
                    {fullDiary === null || diaryHasMore ? (
                      <div className="tm-diary-more">
                        <Button onClick={() => { loadMore() }} disabled={diaryLoading}>
                          {diaryLoading
                            ? t("panel.diary.loadingMore", { defaultValue: "加载中…" })
                            : t("panel.diary.loadMore", { defaultValue: "加载更多" })}
                        </Button>
                      </div>
                    ) : (
                      <div className="tm-diary-end">{t("panel.diary.endReached", { defaultValue: "已显示全部记录" })}</div>
                    )}
                  </div>
                ) : (
                  <EmptyState
                    title={t("panel.diary.emptyTitle", { defaultValue: "暂无时光记录" })}
                    description={t("panel.diary.emptyDesc", { defaultValue: "这里会保存角色手记，以及从对话中提取的重要片段。" })}
                  />
                )}
              </Card>
            ),
          },
          {
            id: "journal",
            label: t("panel.journal.tabJournal", { defaultValue: "个人日记" }),
            content: (
              <Card className="tmb-card" title={t("panel.journal.title", { defaultValue: "个人日记" })}>
                <div className="tm-journal-toolbar">
                  <span className="tm-diary-count">
                    {t("panel.journal.count", { defaultValue: "共 {n} 篇" }).replace("{n}", String(pages.length || journalIndex.length))}
                  </span>
                  <span className="tm-journal-actions">
                    {/* 刷新按钮已移除（1.2.5）：书页指纹（页码:段数:末笔时刻）随 5s
                        轮询自动检测，新页/续写都会自动重拉整本，无需手动刷新 */}
                    <Button tone="primary" onClick={onInviteJournal} disabled={journalSubmitting || !!(journalWrite && journalWrite.writing)}>
                      {journalSubmitting || (journalWrite && journalWrite.writing)
                        ? t("panel.review.writing", { defaultValue: "生成中…" })
                        : t("panel.journal.invite", { defaultValue: "邀请写日记" })}
                    </Button>
                  </span>
                </div>
                {journalWrite && journalWrite.writing ? (
                  <div className="tm-derived">
                    {t("panel.journal.writingHint", { defaultValue: "正在根据近期素材生成个人日记，保存后会显示在这里。" })}
                  </div>
                ) : journalWrite && journalWrite.last_result && !journalWrite.last_result.written ? (
                  <div className="tm-derived">{diaryWriteError(journalWrite.last_result, t)}</div>
                ) : invitePending ? (
                  <div className="tm-derived">
                    {t("panel.journal.invitePending", { defaultValue: "邀请已发送，等待角色决定是否写作。新内容保存后会显示在这里。" })}
                  </div>
                ) : null}
                {visibleArchive ? (
                  <JournalBook
                    t={t}
                    page={visibleArchive}
                    hasPrev={archIdx !== null && archIdx > 0}
                    hasNext={archIdx !== null && archIdx < archiveList.length - 1}
                    onBack={() => { setOpenArchiveNo(null) }}
                    onPrev={() => { if (archIdx !== null && archIdx > 0) setOpenArchiveNo(archiveList[archIdx - 1].page_no || null) }}
                    onNext={() => { if (archIdx !== null && archIdx < archiveList.length - 1) setOpenArchiveNo(archiveList[archIdx + 1].page_no || null) }}
                    archiveLabel={t("panel.journal.archiveBadge", { defaultValue: "已归档" })}
                  />
                ) : visiblePage ? (
                  <JournalBook
                    t={t}
                    page={visiblePage}
                    hasPrev={openIdx > 0}
                    hasNext={openIdx < pages.length - 1}
                    onBack={() => { setOpenPageNo(null) }}
                    onPrev={() => { if (openIdx > 0) setOpenPageNo(pages[openIdx - 1].page_no || null) }}
                    onNext={() => { if (openIdx < pages.length - 1) setOpenPageNo(pages[openIdx + 1].page_no || null) }}
                  />
                ) : (
                  <JournalShelf
                    t={t}
                    index={pages.length ? pages : journalIndex}
                    onOpen={(no) => { setOpenPageNo(no) }}
                    loading={bookLoading}
                    loaded={bookLoaded}
                    catalog={journalCatalog}
                    onCatalog={setJournalCatalog}
                    stack={archiveCount > 0 ? (
                      <ArchiveStack
                        t={t}
                        brief={journalArchiveBrief || {}}
                        loading={archiveLoading}
                        onOpen={openArchive}
                      />
                    ) : null}
                  />
                )}
              </Card>
            ),
          },
          {
            id: "review",
            label: t("panel.review.tabReview", { defaultValue: "我的日记" }),
            content: (
              <Card className="tmb-card" title={t("panel.review.title", { defaultValue: "我的日记" })}>
                <div className="tm-journal-toolbar">
                  <span className="tm-diary-count">
                    {t("panel.review.count", { defaultValue: "共 {n} 篇" }).replace("{n}", String(reviewEntries.length))}
                  </span>
                  <span className="tm-journal-actions">
                    {/* 刷新按钮已移除：review_brief 篇数/末次成文时间随 5s 轮询自动检测，
                        写完新篇会自动重拉全部篇目，无需手动刷新 */}
                    <Button
                      tone="primary"
                      onClick={onWriteReviewNow}
                      disabled={reviewSubmitting || reviewLoading || !!(reviewBrief && reviewBrief.writing)}
                    >
                      {reviewSubmitting || (reviewBrief && reviewBrief.writing)
                        ? t("panel.review.writing", { defaultValue: "生成中…" })
                        : t("panel.review.writeNow", { defaultValue: "立即生成" })}
                    </Button>
                  </span>
                </div>
                {reviewBrief && reviewBrief.writing ? (
                  <div className="tm-derived">
                    {t("panel.review.writingHint", { defaultValue: "后台模型正在整理近期互动，完成后会显示在这里。" })}
                  </div>
                ) : null}
                {/* 成文失败常驻兜底（1.3.0 第十一轮）：失败结论不能被 toast 时序
                    和"面板当时开着没"绑死——写失败后下一次点击受理时后端会把结论位
                    清空（write_review_now 的作废逻辑），警示行随之消失 */}
                {reviewBrief && !reviewBrief.writing && reviewBrief.last_result && !reviewBrief.last_result.written ? (
                  <div className="tm-derived">
                    {diaryWriteError(reviewBrief.last_result, t)}
                  </div>
                ) : null}
                {visibleReviewArchive ? (
                  <ReviewBook
                    t={t}
                    entry={visibleReviewArchive}
                    hasPrev={reviewArchIdx > 0}
                    hasNext={reviewArchIdx >= 0 && reviewArchIdx < reviewArchive.length - 1}
                    onBack={() => { setOpenReviewArchiveTs(null) }}
                    onPrev={() => { if (reviewArchIdx > 0) setOpenReviewArchiveTs(String(reviewArchive[reviewArchIdx - 1].ts || "")) }}
                    onNext={() => { if (reviewArchIdx >= 0 && reviewArchIdx < reviewArchive.length - 1) setOpenReviewArchiveTs(String(reviewArchive[reviewArchIdx + 1].ts || "")) }}
                    archiveLabel={t("panel.review.archiveBadge", { defaultValue: "已归档" })}
                  />
                ) : visibleReview ? (
                  <ReviewBook
                    t={t}
                    entry={visibleReview}
                    hasPrev={reviewIdx > 0}
                    hasNext={reviewIdx >= 0 && reviewIdx < reviewEntries.length - 1}
                    onBack={() => { setOpenReviewTs(null) }}
                    onPrev={() => { if (reviewIdx > 0) setOpenReviewTs(String(reviewEntries[reviewIdx - 1].ts || "")) }}
                    onNext={() => { if (reviewIdx >= 0 && reviewIdx < reviewEntries.length - 1) setOpenReviewTs(String(reviewEntries[reviewIdx + 1].ts || "")) }}
                  />
                ) : (
                  <ReviewShelf
                    t={t}
                    progress={reviewProgress}
                    entries={reviewEntries}
                    loaded={reviewLoaded}
                    loading={reviewLoading}
                    catalog={reviewCatalog}
                    onCatalog={setReviewCatalog}
                    onOpen={(ts) => { setOpenReviewTs(ts) }}
                    stack={reviewArchiveCount > 0 ? (
                      <ArchiveStack
                        t={t}
                        brief={reviewArchiveBrief || {}}
                        loading={reviewArchiveLoading}
                        onOpen={openReviewArchive}
                        file
                        tip={t("panel.review.archiveTip", { defaultValue: "查看已归档的相处记录，内容仅供阅读" })}
                      />
                    ) : null}
                  />
                )}
              </Card>
            ),
          },
        ]}
      />
      {settingsChildren}
    </div>
  )
}

// 时光日记时间线：按日期分组的卡片流（组头吸顶），手记暖纸色、碎片蓝调气泡
function TimelineView(props: { t: TFunc; items: DiaryItem[]; onDelete: (ts: string) => void }) {
  const { t, items, onDelete } = props
  const groups: Array<{ day: string; rows: DiaryItem[] }> = []
  for (const item of items) {
    const day = dayKeyOf(item.ts)
    const last = groups[groups.length - 1]
    if (last && last.day === day) last.rows.push(item)
    else groups.push({ day, rows: [item] })
  }
  return (
    <div className="tm-timeline">
      {groups.map((group) => (
        <div className="tm-day-group" key={group.day}>
          <div className="tm-day-head">
            <span>{dayLabelOf(group.day, t)}</span>
            <span className="tm-day-count">{t("panel.diary.dayCount", { defaultValue: "{n} 条" }).replace("{n}", String(group.rows.length))}</span>
          </div>
          {group.rows.map((item) => (
            <DiaryRow key={String(item.ts || "")} t={t} item={item} onDelete={onDelete} />
          ))}
        </div>
      ))}
    </div>
  )
}

// 时间线单行：她手写的显示心情与正文，自动碎片显示类型徽标、原话摘录与删除按钮
//（key 由列表处传入，受限运行时会把它并入 props 做类型检查，故声明在此）
function DiaryRow(props: { key?: string; t: TFunc; item: DiaryItem; onDelete: (ts: string) => void }) {
  const { t, item, onDelete } = props
  const isAuto = String(item.source || "self") === "auto"
  if (isAuto) {
    return (
      <div className="tm-tl-row" data-source="auto">
        <div className="tm-tl-head">
          <span className="tm-tl-badge" data-kind={item.kind || "important"}>
            {t(fragmentKindKey(item.kind), { defaultValue: String(item.kind || "") })}
          </span>
          <span className="tm-tl-ts">{fmtTs(item.ts)}</span>
          <span className="tm-tl-spacer" />
          <button className="tm-tl-delete" type="button" onClick={() => onDelete(String(item.ts || ""))}>
            {t("panel.diary.delete", { defaultValue: "删除" })}
          </button>
        </div>
        <div className="tm-tl-quote">「{item.quote}」</div>
        {item.note ? <div className="tm-tl-note">{item.note}</div> : null}
      </div>
    )
  }
  return (
    <div className="tm-tl-row" data-source="self">
      <div className="tm-tl-head">
        <span className="tm-tl-badge" data-kind="self">{t("panel.diary.selfBadge", { defaultValue: "角色手记" })}</span>
        {item.mood ? <span className="tm-tl-mood">{item.mood}</span> : null}
        <span className="tm-tl-ts">{fmtTs(item.ts)}</span>
      </div>
      <div className="tm-tl-entry">{item.entry}</div>
    </div>
  )
}

function diaryHeading(title: string | undefined, recordedAt: string | undefined, t: TFunc): string {
  let heading = String(title || "").trim()
  const recorded = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(recordedAt || ""))
  const prefix = /^(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})日?(?=$|[\s·•:：|｜—–-])[\s·•:：|｜—–-]*/.exec(heading)
  // Hide only a repeated recording-date prefix; keep historical dates in real titles.
  if (recorded && prefix && [1, 2, 3].every(index => Number(recorded[index]) === Number(prefix[index]))) {
    heading = heading.slice(prefix[0].length).trim()
  }
  return heading || t("panel.journal.untitled", { defaultValue: "未命名日记" })
}

// 目录日期固定取首次记录，标题和正文摘录只在显示层整理。
function JournalShelf(props: {
  t: TFunc
  index: Array<JournalPageHeader & { entries?: Array<{ text?: string }> }>
  onOpen: (pageNo: number) => void
  loading: boolean
  loaded: boolean
  catalog: CatalogView
  onCatalog: (value: CatalogView) => void
  // 归档入口不受当前目录是否为空或搜索结果影响。
  stack?: any
}) {
  const { t, index, onOpen, loading, loaded, stack, catalog, onCatalog } = props
  const query = catalog.query.trim().toLowerCase()
  const matches = index.filter((page) => !query || [
    page.title, page.started_at, page.last_ts, ...(page.entries || []).map((entry) => entry.text),
  ].join(" ").toLowerCase().includes(query))
  const total = Math.max(1, Math.ceil(matches.length / 8))
  const position = Math.max(0, Math.min(catalog.page, total - 1))
  return (
    <div className="tmb-shelf">
      {index.length ? <CatalogTools t={t} catalog={catalog} onCatalog={onCatalog} position={position} total={total} journal /> : null}
      {index.length && !matches.length ? <div className="tmb-no-matches" role="status">{t("panel.journal.noMatches", { defaultValue: "没有找到相关日记" })}</div> : null}
      {!index.length ? loaded ? (
        <EmptyState
          title={t("panel.journal.emptyTitle", { defaultValue: "暂无个人日记" })}
          description={t("panel.journal.emptyDesc", { defaultValue: "角色写下的日记会显示在这里。也可以发送写作邀请，但不保证立即生成。" })}
        />
      ) : <div className="tm-derived" role="status">{loading ? t("panel.journal.loading", { defaultValue: "加载日记中…" }) : ""}</div> : null}
      {matches.slice(position * 8, position * 8 + 8).map((page) => {
        const no = Number(page.page_no || 0)
        const excerpt = diaryExcerpt((page.entries || []).map((entry) => String(entry.text || "")).join("\n"))
        return (
          <button
            key={String(no)}
            type="button"
            className="tmb-spine"
            title={[page.title, shelfTip(t, no, journalRangeLabel(page), t(journalTrendKey(page.mood_avg), { defaultValue: "" }))].filter(Boolean).join(" · ")}
            onClick={() => onOpen(no)}
          >
            <span className="tmb-cover-meta">
              <time className="tmb-record-date" dateTime={page.started_at}>{String(page.started_at || "").slice(0, 10) || t("panel.journal.noDate", { defaultValue: "未注明日期" })}</time>
            </span>
            <span className="tmb-cover-title">{diaryHeading(page.title, page.started_at, t)}</span>
            <span className="tmb-cover-excerpt">{excerpt || "--"}</span>
            <span className="tmb-cover-bottom">
              <span className="tmb-cover-trend"><span className="tm-mood-dot" style={{ background: shelfInk(page.mood_avg) }} />{t(journalTrendKey(page.mood_avg), { defaultValue: "" })}</span>
              {page.legacy ? <span>{t("panel.journal.legacy", { defaultValue: "旧版周记" })}</span> : null}
              <span className="tmb-chevron" aria-hidden="true" />
            </span>
          </button>
        )
      })}
      {stack}
    </div>
  )
}

// 一篇日记包含首次记录与续写；导航切换篇目，编号直接取持久化 page_no。
function JournalBook(props: {
  t: TFunc
  page: JournalPage
  hasPrev: boolean
  hasNext: boolean
  onBack: () => void
  onPrev: () => void
  onNext: () => void
  // 藏书阁模式（1.3.0）：题签换口径——同一套纸，两处出身（页眉页码/日期/心情照常，
  // 都是存储里现成的事实）
  archiveLabel?: string
}) {
  const { t, page, hasPrev, hasNext, onBack, onPrev, onNext, archiveLabel } = props
  const readingRef = useReadingStart(String(page.page_no || ""), archiveLabel)
  const sheets = (page.entries || []).map((entry, idx) => ({
    key: String(entry.ts || idx),
    when: String(entry.ts || "").replace("T", " ").slice(0, 16),
    secs: splitSections(entry.text),
  }))
  const diaryNo = page.page_no ? t("panel.journal.diaryNo", { defaultValue: "日记 #{n}" }).replace("{n}", String(page.page_no)) : ""
  const diaryTitle = diaryHeading(page.title, page.started_at, t)
  return (
    <div className="tmb-book" ref={readingRef}>
      <div className="tmb-page">
        <div className="tmb-head">
          <div className="tmb-reading-kicker">
            <span>{t("panel.journal.kicker", { defaultValue: "角色日记" })}</span>
            {archiveLabel ? <span className="tmb-archive-badge">{archiveLabel}</span> : null}
            <span className="tmb-head-no">{diaryNo}</span>
          </div>
          <h3 className="tmb-reading-title">{diaryTitle}</h3>
          <div className="tmb-reading-meta">
            <span>{journalRangeLabel(page)}</span>
            <span className="tmb-head-trend"><span className="tm-mood-dot" style={{ background: shelfInk(page.mood_avg) }} />{t(journalTrendKey(page.mood_avg), { defaultValue: "" })}</span>
            {page.legacy ? <span>{t("panel.journal.legacy", { defaultValue: "旧版周记" })}</span> : null}
          </div>
        </div>
        <div className="tmb-page-body">
          {sheets.map((sheet, idx) => (
            <div className="tmb-entry" key={sheet.key}>
              <div className="tmb-entry-heading">
                <span className="tmb-entry-kind">{idx === 0
                  ? t("panel.journal.firstWritten", { defaultValue: "首次记录" })
                  : t("panel.journal.continued", { defaultValue: "续写" })}</span>
                {sheet.when ? <span className="tmb-entry-when">{sheet.when}</span> : null}
              </div>
              {sheet.secs.map((sec, si) => (
                <div className="tmb-sec" key={String(si)}>
                  {sec.title ? <div className="tmb-sec-title">{sec.title}</div> : null}
                  {sec.paras.map((para, pi) => (
                    <div key={String(pi)} className="tmb-text">
                      {para}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="tmb-foot">
          <button type="button" className="tmb-btn" onClick={onBack}>
            {t("panel.journal.backToShelf", { defaultValue: "← 返回日记列表" })}
          </button>
          <span className="tmb-foot-mid tmb-foot-mid--reader">
            <button type="button" className="tmb-btn" onClick={onPrev} disabled={!hasPrev}>
              {t("panel.journal.prevDiary", { defaultValue: "上一篇" })}
            </button>
            <span className="tmb-current-diary" aria-live="polite">
              <span className="tmb-current-title" title={diaryTitle}>{diaryTitle}</span>
              <span className="tmb-ind">{diaryNo}</span>
            </span>
            <button type="button" className="tmb-btn" onClick={onNext} disabled={!hasNext}>
              {t("panel.journal.nextDiary", { defaultValue: "下一篇" })}
            </button>
          </span>
        </div>
      </div>
    </div>
  )
}

// ISO 时间戳 → "MM-DD HH:mm"（年月日分组与单页区间各自另切）；坏数据原样返回
function fmtTs(ts?: string): string {
  const raw = String(ts || "")
  if (!raw) return ""
  return raw.replace("T", " ").slice(5, 16)
}

// ts → 日期 key（YYYY-MM-DD）
function dayKeyOf(ts?: string): string {
  return String(ts || "").slice(0, 10) || "----"
}

// 日期 key → 分组头文案："2026年8月29日"；坏 key 回退 i18n 的"未注明日期"
function dayLabelOf(day: string, t: TFunc): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!m) return t("panel.journal.noDate", { defaultValue: "未注明日期" })
  return `${Number(m[1])}年${Number(m[2])}月${Number(m[3])}日`
}

// 页的日期区间：起始日期 ~ 最后落笔日期（同天只显示一个）
function journalRangeLabel(page: JournalPageHeader): string {
  const start = String(page.started_at || "").replace("T", " ").slice(0, 10)
  const last = String(page.last_ts || "").replace("T", " ").slice(0, 10)
  if (!start && !last) return ""
  if (!last || start === last) return start
  return `${start} ~ ${last}`
}

// 素材双门槛即使尚无成文也可见；目录直接显示摘录与统计区间。
function ReviewShelf(props: {
  t: TFunc
  progress: ReviewProgress
  entries: ReviewEntry[]
  loaded: boolean
  loading: boolean
  catalog: CatalogView
  onCatalog: (value: CatalogView) => void
  onOpen: (ts: string) => void
  // 两处归档入口共用组件。
  stack?: any
}) {
  const { t, progress, entries, loaded, loading, onOpen, stack, catalog, onCatalog } = props
  const query = catalog.query.trim().toLowerCase()
  const matches = entries.filter((entry) => !query || [entry.title, entry.ts, entry.span, entry.text].join(" ").toLowerCase().includes(query))
  const total = Math.max(1, Math.ceil(matches.length / 8))
  const position = Math.max(0, Math.min(catalog.page, total - 1))
  const turns = Number(progress.turns || 0)
  const threshold = Math.max(1, Number(progress.turns_threshold || 50))
  const pct = Math.min(100, Math.round((turns / threshold) * 100))
  const days = Number(progress.days || 0)
  const daysThreshold = Math.max(1, Number(progress.days_threshold || 7))
  const daysPct = Math.min(100, Math.round((days / daysThreshold) * 100))
  return (
    <div className="tm-review">
      {loaded ? (
      <div className="tmb-meter">
        <div className="tmb-meter-row">
          <span>
            {t("panel.review.progress", { defaultValue: "互动素材：{n} / {total} 轮" })
              .replace("{n}", String(turns))
              .replace("{total}", String(threshold))}
          </span>
          <span className="tmb-head-spacer" />
          {progress.due ? (
            <span className="tmb-meter-due">{t("panel.review.dueNow", { defaultValue: "已达到生成条件，等待后台处理" })}</span>
          ) : null}
          <span>{pct}%</span>
        </div>
        <div className="tmb-meter-track">
          <div className="tmb-meter-fill" style={{ width: `${pct}%` }} />
        </div>
        {/* 天数维度（1.3.0）：轮数没满但日子到了同样成文——两条门槛都要看得见 */}
        <div className="tmb-meter-row tmb-meter-row--sub">
          <span>
            {t("panel.review.daysProgress", { defaultValue: "或达到 {total} 天（目前第 {d} 天）" })
              .replace("{total}", String(daysThreshold))
              .replace("{d}", String(days))}
          </span>
          <span className="tmb-head-spacer" />
          <span>{daysPct}%</span>
        </div>
        <div className="tmb-meter-track">
          <div className="tmb-meter-fill" style={{ width: `${daysPct}%` }} />
        </div>
      </div>
      ) : null}
      <div className="tmb-shelf">
        {entries.length ? <CatalogTools t={t} catalog={catalog} onCatalog={onCatalog} position={position} total={total} /> : null}
        {entries.length && !matches.length ? <div className="tmb-no-matches" role="status">{t("panel.journal.noMatches", { defaultValue: "没有找到相关日记" })}</div> : null}
        {!entries.length ? loaded ? (
          <EmptyState
            title={t("panel.review.emptyTitle", { defaultValue: "暂无相处记录" })}
            description={t("panel.review.emptyDesc", { defaultValue: "积累足够的互动素材后会自动生成。也可点击「立即生成」（至少需要 10 轮互动）。结果由模型整理，可能存在偏差。" })}
          />
        ) : <div className="tm-derived" role="status">{loading ? t("panel.journal.loading", { defaultValue: "加载日记中…" }) : ""}</div> : null}
        {matches.slice(position * 8, position * 8 + 8).map((entry, i) => {
          const ts = String(entry.ts || "")
          const turns = entry.turns === undefined ? "" : String(entry.turns)
          return (
            <button
              key={ts || String(i)}
              type="button"
              className="tmb-spine tmb-spine--file"
              title={[entry.title, fileTip(t, ts, spanShort(entry.span), turns)].filter(Boolean).join(" · ")}
              onClick={() => onOpen(ts)}
            >
              <span className="tmb-cover-meta">
                <time className="tmb-record-date" dateTime={ts}>{ts.slice(0, 10) || t("panel.journal.noDate", { defaultValue: "未注明日期" })}</time>
              </span>
              <span className="tmb-cover-title">{diaryHeading(entry.title, ts, t)}</span>
              <span className="tmb-cover-excerpt">{diaryExcerpt(entry.text) || "--"}</span>
              <span className="tmb-cover-bottom">
                <span className="tmb-chevron" aria-hidden="true" />
              </span>
            </button>
          )
        })}
        {stack}
      </div>
    </div>
  )
}

// 我的日记：正文优先，参考素材可展开；旧篇目缺字段时隐藏对应内容。
function ReviewBook(props: {
  t: TFunc
  entry: ReviewEntry
  hasPrev: boolean
  hasNext: boolean
  onBack: () => void
  onPrev: () => void
  onNext: () => void
  // 档案室模式（1.3.0）：题签换口径——卷首日期/区间/依据都是存储里现成的事实
  archiveLabel?: string
}) {
  const { t, entry, hasPrev, hasNext, onBack, onPrev, onNext, archiveLabel } = props
  const readingRef = useReadingStart(String(entry.ts || ""), archiveLabel)
  const paras = splitParas(entry.text)
  const turns = entry.turns === undefined ? "" : String(entry.turns)
  const span = spanShort(entry.span)
  const selfActions = String(entry.self_action_count ?? 0)
  // 本卷依据：三项都取自 review_record 的快照字段，缺任一项只缺那一行
  const toneEntries = Object.entries(entry.tone || {}).filter(([, n]) => Number(n) > 0)
  const toneParts = toneEntries.map(([label, n]) => {
    const key = toneLabelKey(label)
    const word = key ? t(key, { defaultValue: label }) : label
    return `${word}×${Number(n)}`
  })
  const moodAvg = entry.mood_avg === null || entry.mood_avg === undefined || !Number.isFinite(Number(entry.mood_avg)) ? null : Number(entry.mood_avg)
  const quotes = Array.isArray(entry.quotes) ? entry.quotes.filter((q) => String(q && q.quote || "").trim()) : []
  const hasEvidence = toneParts.length > 0 || moodAvg !== null || quotes.length > 0
  const diaryTitle = diaryHeading(entry.title, entry.ts, t)
  const composedAt = String(entry.ts || "").replace("T", " ").slice(0, 16) || t("panel.journal.noDate", { defaultValue: "未注明日期" })
  return (
    <div className="tmb-book" ref={readingRef}>
      <div className="tmb-page tmb-page--file">
        <div className="tmb-head">
          <div className="tmb-reading-kicker">
            <span>{t("panel.review.kicker", { defaultValue: "相处记录" })}</span>
            {archiveLabel ? <span className="tmb-archive-badge">{archiveLabel}</span> : null}
            <span className="tmb-head-no" title={String(entry.ts || "")}>{composedAt}</span>
          </div>
          <h3 className="tmb-reading-title">{diaryTitle}</h3>
          <div className="tmb-reading-meta">
            {span ? <span>{t("panel.review.fieldSpan", { defaultValue: "统计区间" })} · {span}</span> : null}
          </div>
        </div>
        <div className="tmb-file-cols">
          <div className="tmb-file-main">
            {paras.map((para, pi) => (
              <div className="tmb-text tmb-text--file" key={String(pi)}>{para}</div>
            ))}
          </div>
          <aside className="tmb-file-aside">
            <div className="tmb-file-sign"><span className="tmb-seal">{t("panel.review.seal", { defaultValue: "相处小结" })}</span></div>
            <div className="tmb-reading-note">{t("panel.review.hint", { defaultValue: "后台模型整理的相处记录，仅供回顾，不是对你的评分。" })}</div>
            {hasEvidence || entry.self_action_count !== undefined || entry.turns !== undefined ? (
              <details className="tmb-reference">
                <summary>{t("panel.review.evidence", { defaultValue: "参考素材" })}</summary>
              <div className="tmb-evidence">
                {entry.turns !== undefined ? (
                  <div className="tmb-evidence-line">
                    <span className="tmb-evidence-label">{t("panel.review.fieldTurns", { defaultValue: "互动轮数" })}</span>
                    <span className="tmb-evidence-value">{turns}</span>
                  </div>
                ) : null}
                {entry.self_action_count !== undefined ? (
                  <div className="tmb-evidence-line">
                    <span className="tmb-evidence-label">{t("panel.review.fieldSelfActions", { defaultValue: "自主情绪次数" })}</span>
                    <span className="tmb-evidence-value">{selfActions}</span>
                  </div>
                ) : null}
                {toneParts.length ? (
                  <div className="tmb-evidence-line">
                    <span className="tmb-evidence-label">{t("panel.review.evidenceTone", { defaultValue: "语气分布" })}</span>
                    <span className="tmb-evidence-value">{toneParts.join(" · ")}</span>
                  </div>
                ) : null}
                {moodAvg !== null ? (
                  <div className="tmb-evidence-line">
                    <span className="tmb-evidence-label">{t("panel.review.evidenceMood", { defaultValue: "心情趋势" })}</span>
                    <span className="tmb-evidence-value">
                      {t(journalTrendKey(moodAvg), { defaultValue: "" })}（{moodAvg >= 0 ? "+" : ""}{moodAvg.toFixed(2)}）
                    </span>
                  </div>
                ) : null}
                {quotes.map((q, qi) => (
                  <div className="tmb-quote" key={String(qi)}>
                    <span className="tmb-quote-kind">{t(fragmentKindKey(q.kind), { defaultValue: "他说" })}</span>
                    <span className="tmb-quote-text">「{String(q.quote || "")}」</span>
                  </div>
                ))}
              </div>
              </details>
            ) : null}
          </aside>
        </div>
        <div className="tmb-foot">
          <button type="button" className="tmb-btn" onClick={onBack}>
            {t("panel.review.backToShelf", { defaultValue: "← 返回日记列表" })}
          </button>
          <span className="tmb-foot-mid tmb-foot-mid--reader">
            <button type="button" className="tmb-btn" onClick={onPrev} disabled={!hasPrev}>
              {t("panel.review.prevFile", { defaultValue: "上一篇" })}
            </button>
            <span className="tmb-current-diary" aria-live="polite">
              <span className="tmb-current-title" title={diaryTitle}>{diaryTitle}</span>
              <span className="tmb-ind">{composedAt}</span>
            </span>
            <button type="button" className="tmb-btn" onClick={onNext} disabled={!hasNext}>
              {t("panel.review.nextFile", { defaultValue: "下一篇" })}
            </button>
          </span>
        </div>
      </div>
    </div>
  )
}

// ---- 书本视图的显示层辅助（存储与注入文本零改动）----

function useReadingStart(identity: string, archiveLabel?: string) {
  const ref = useRef<any>(null)
  useEffect(() => {
    const content = ref.current && ref.current.closest(".tm-content")
    if (content) content.scrollTop = 0
  }, [identity, archiveLabel])
  return ref
}

// 后端拼装规则（core/journal.py）：每个非空字段一行「【小标题】正文」，
// 只填"想说的"时不加标题。此处按行反解成节，标题原样显示（中文声明制，
// 是她说出口的词，不做二次翻译），后续无标题的行并入当前节
function splitSections(text?: string): Array<{ title: string; paras: string[] }> {
  const secs: Array<{ title: string; paras: string[] }> = []
  for (const line of String(text || "").split("\n")) {
    const body = line.trim()
    const hit = /^【([^】]{1,12})】([\s\S]*)$/.exec(body)
    if (hit) {
      const tail = hit[2].trim()
      secs.push({ title: hit[1].trim(), paras: tail ? [tail] : [] })
      continue
    }
    if (!body) continue
    if (!secs.length) secs.push({ title: "", paras: [body] })
    else secs[secs.length - 1].paras.push(body)
  }
  return secs
}

// 我的日记正文：按行切段（模型成文是一篇成段评价，段落即自然段）
function splitParas(text?: string): string[] {
  const out: string[] = []
  for (const line of String(text || "").split("\n")) {
    const body = line.trim()
    if (body) out.push(body)
  }
  return out
}

// 心情仅改变小圆点的浅蓝浓淡，正文与目录底色保持统一。
function shelfInk(mood?: number | null): string {
  if (mood === null || mood === undefined || !Number.isFinite(mood)) return "rgb(174, 196, 211)"
  const v = Math.max(-1, Math.min(1, mood))
  const to = v < 0 ? [132, 155, 176] : [92, 169, 215]
  const zero = [174, 196, 211]
  const ratio = Math.min(1, Math.abs(v) / 0.85)
  const mix = (i: number) => Math.round(zero[i] + (to[i] - zero[i]) * ratio)
  return `rgb(${mix(0)}, ${mix(1)}, ${mix(2)})`
}

function diaryExcerpt(text?: string): string {
  return splitSections(text).map((section) => section.paras.join(" ")).join(" ").slice(0, 120)
}

// "2026-08-01~2026-08-07" → "08-01 ~ 08-07"（等宽口径行与盒脊悬停共用）
function spanShort(span?: string): string {
  const parts = String(span || "").split("~").map((p) => p.trim().slice(5, 10)).filter((p) => p)
  return parts.join(" ~ ")
}

// 悬停说明（个人日记）：第 N 页 · 日期区间 · 她的心情
// 这里的 N 是 page_no（全书累计页码，与页眉同一个数），永不重编
function shelfTip(t: TFunc, no: number, range: string, trend: string): string {
  const parts: string[] = no ? [t("panel.journal.diaryNo", { defaultValue: "日记 #{n}" }).replace("{n}", String(no))] : []
  if (range) parts.push(range)
  if (trend) parts.push(trend)
  return parts.join(" · ")
}

// 归档入口仍按需拉取，显示真实归档时段。
function ArchiveStack(props: {
  t: TFunc
  brief: JournalArchiveBrief | ReviewArchiveBrief
  loading: boolean
  onOpen: () => void
  file?: boolean
  tip?: string
}) {
  const { t, brief, loading, onOpen, file, tip } = props
  const from = String(brief.first_ts || "").slice(0, 10)
  const to = String(brief.last_ts || "").slice(0, 10)
  const fullTip = [
    tip || t("panel.journal.archiveTip", { defaultValue: "查看已归档的个人日记，内容仅供阅读" }),
    from ? `${from} – ${to || "?"}` : "",
    loading ? (file
      ? t("panel.review.archiveLoading", { defaultValue: "加载归档中…" })
      : t("panel.journal.archiveLoading", { defaultValue: "加载归档中…" })) : "",
  ].filter((p) => p).join(" · ")
  return (
    <button type="button" className={file ? "tmb-stack tmb-stack--file" : "tmb-stack"} title={fullTip} onClick={onOpen} disabled={loading} aria-busy={loading}>
      <span className="tmb-archive-icon" aria-hidden="true" />
      <span>{loading
        ? t("panel.journal.archiveLoading", { defaultValue: "加载归档中…" })
        : t("panel.journal.archiveBadge", { defaultValue: "已归档" })}</span>
      <span className="tmb-archive-range">{from}{to && to !== from ? ` ~ ${to}` : ""}</span>
      <span className="tmb-chevron" aria-hidden="true" />
    </button>
  )
}

function CatalogTools(props: {
  t: TFunc
  catalog: CatalogView
  onCatalog: (value: CatalogView) => void
  position: number
  total: number
  journal?: boolean
}) {
  const { t, catalog, onCatalog, position, total, journal } = props
  const searchLabel = journal
    ? t("panel.journal.search", { defaultValue: "搜索标题、日期或正文" })
    : t("panel.review.search", { defaultValue: "搜索标题、日期或正文" })
  const prev = t("panel.journal.prev", { defaultValue: "上一页" })
  const next = t("panel.journal.next", { defaultValue: "下一页" })
  return (
    <div className="tmb-catalog-tools">
      <input className="tmb-search" type="search" aria-label={searchLabel} placeholder={searchLabel} value={catalog.query} onInput={(event: any) => onCatalog({ query: event.target.value, page: 0 })} />
      <span className="tmb-catalog-nav">
        <button type="button" className="tmb-catalog-btn" aria-label={prev} title={prev} disabled={!position} onClick={() => onCatalog({ query: catalog.query, page: position - 1 })}>
          <span className="tmb-chevron tmb-chevron--prev" aria-hidden="true" />
        </button>
        <span className="tmb-ind" aria-live="polite">{t("panel.journal.catalogPage", { defaultValue: "目录 {n} / {total}" }).replace("{n}", String(position + 1)).replace("{total}", String(total))}</span>
        <button type="button" className="tmb-catalog-btn" aria-label={next} title={next} disabled={position >= total - 1} onClick={() => onCatalog({ query: catalog.query, page: position + 1 })}>
          <span className="tmb-chevron" aria-hidden="true" />
        </button>
      </span>
    </div>
  )
}

// 悬停说明（我的日记）：成文日 · 统计区间 · 互动轮数——全部取存储里现成的值，
// 不派生任何会随淘汰平移的序号
function fileTip(t: TFunc, ts: string, span: string, turns: string): string {
  const parts: string[] = [
    `${t("panel.review.tipComposed", { defaultValue: "生成" })} ${ts.slice(0, 10) || "--"}`,
  ]
  if (span) parts.push(`${t("panel.review.fieldSpan", { defaultValue: "统计区间" })} ${span}`)
  if (turns) parts.push(t("panel.review.turnsMeta", { defaultValue: "{n} 轮" }).replace("{n}", turns))
  return parts.join(" · ")
}
