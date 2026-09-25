// 日记页：页内 Tabs 切换 时光日记（日期分组时间线 + 加载更多）/ 个人日记（书架 + 手写本）
// / 我的日记（关于主人的互动评价：档案架 + 铅印卷宗 + 素材进度）
//
// 1.3.0 拟真书本重做：个人日记与我的日记换成两套「物件」语言——
//   目录 = 书架上的书脊（竖排日期 + 心情色作书脊皮，悬停整本抽出）
//   个人日记 = 她手写的线装本（布脊 + 线装孔 + 题签 + 楷体暖纸 + 丝带 + 首字下沉）
//   我的日记 = 第三者留下的铅印卷宗（冷灰打孔纸 + 等宽口径行 + 宋体压痕 + 朱印落款）
// 正文分节（【这段时间】等）在**显示层**解析：存储与注入文本一个字都不改，
// 那些小标题是 prompt 共用的（core/journal.py 中文声明制），改不得。
// hosted-tsx 约束：唯一 export 在任何 JSX 闭合标签之前；辅助组件放文件尾部靠函数声明提升被引用
import { Button, Card, EmptyState, Tabs, useToast } from "@neko/plugin-ui"
import { useEffect, useLocalState, useRef, useState } from "@neko/plugin-ui"
import { BOOK_STYLES } from "./styles_book"
import type {
  DiaryItem, JournalPage, JournalPageHeader, JournalArchiveBrief, ReviewArchiveBrief, ReviewBrief, ReviewEntry, ReviewProgress, TFunc,
} from "./types"
import { fragmentKindKey, journalTrendKey, toneLabelKey } from "./utils"

export function DiaryPane(props: {
  t: TFunc
  diary: DiaryItem[]
  diaryTotal: number
  fragmentTotal: number
  journalIndex: JournalPageHeader[]
  // 藏书阁（1.3.0）：活架写满下架的旧页合订本——概览进轮询，全量按需拉取；
  // 面板据此在书架末尾画一根横放书脊，点开即只读翻阅
  journalArchiveBrief?: JournalArchiveBrief
  invitePending?: boolean
  lanlan?: string
  // 用 types.ts 的 ReviewBrief（含 1.2.3 的 writing/last_result）：内联复刻一份
  // 窄类型会让 reviewBrief.writing 过不了 hosted-tsx 的类型检查（TS2339）
  reviewBrief?: ReviewBrief
  // 档案室（1.3.0）：活架攒满下架的旧卷宗合档——与藏书阁同款：概览进轮询，
  // 全量按需拉取；面板据此在档案架末尾画那只档案盒，点开即只读翻阅
  reviewArchiveBrief?: ReviewArchiveBrief
  onClearDiary: () => void
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
  onClearReview: () => void
  settingsChildren?: any
}) {
  const {
    t, diary, diaryTotal, fragmentTotal, journalIndex, journalArchiveBrief, invitePending, lanlan, reviewBrief,
    reviewArchiveBrief,
    onClearDiary, onDeleteFragment, onLoadJournal, onLoadJournalArchive, onLoadMoreDiary, onInviteJournal,
    onLoadReview, onLoadReviewArchive, onWriteReviewNow, onClearReview, settingsChildren,
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
  const openIdx = openPageNo === null ? null : pages.findIndex((p) => (p.page_no || 0) === openPageNo)
  const visiblePage = openIdx !== null && openIdx >= 0 ? pages[openIdx] : null
  // 书页指纹（1.2.3）：页码:段数:末笔时刻 逐页拼接。mood_journal_write 默认
  // 续写在当前页——页数不变，只看页数的旧检测让她续写的段落永远等不到自动
  // 刷新；指纹把"段数/末笔时刻"也纳入观察面
  const bookFingerprint = journalIndex
    .map((p) => `${p.page_no || 0}:${p.entry_count || 0}:${p.last_ts || ""}`)
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
          toast.error(t("panel.review.archiveLoadError", { defaultValue: "没能取到旧卷宗（通道不可用），稍后再点一次试试" }))
          return
        }
        list = fetched
        setReviewArchive(list)
        setReviewArchiveLoaded(true)
      } catch (err) {
        console.warn("[forever_companion] load review archive failed:", err)
        toast.error(t("panel.review.archiveLoadError", { defaultValue: "没能取到旧卷宗（通道不可用），稍后再点一次试试" }))
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

  // 我的日记写了新篇：dashboard 的 review_brief 篇数变化 → 自动重拉全部篇目
  useEffect(() => {
    if (reviewLoaded && reviewBrief && Number(reviewBrief.entries || 0) !== reviewEntries.length) {
      reloadReview()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewBrief && reviewBrief.entries])

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
          toast.error(t("panel.journal.archiveLoadError", { defaultValue: "没能取到合订本（通道不可用），稍后再点一次试试" }))
          return
        }
        list = fetched
        setArchivePages(list)
        setArchiveLoaded(true)
      } catch (err) {
        console.warn("[forever_companion] load journal archive failed:", err)
        toast.error(t("panel.journal.archiveLoadError", { defaultValue: "没能取到合订本（通道不可用），稍后再点一次试试" }))
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
                        {t("panel.diary.summary", { defaultValue: "共 {total} 条 · 她手写 {self} · 自动碎片 {auto}" })
                          .replace("{total}", String(diaryTotal))
                          .replace("{self}", String(diaryTotal - fragmentTotal))
                          .replace("{auto}", String(fragmentTotal))}
                      </span>
                      <Button tone="danger" onClick={onClearDiary}>
                        {t("actions.clear_diary.label", { defaultValue: "清空时光日记" })}
                      </Button>
                    </div>
                    <TimelineView t={t} items={timeline} onDelete={onDeleteFragment} />
                    {fullDiary === null || diaryHasMore ? (
                      <div className="tm-diary-more">
                        <Button onClick={() => { loadMore() }} disabled={diaryLoading}>
                          {diaryLoading
                            ? t("panel.diary.loadingMore", { defaultValue: "翻找中…" })
                            : t("panel.diary.loadMore", { defaultValue: "加载更多" })}
                        </Button>
                      </div>
                    ) : (
                      <div className="tm-diary-end">{t("panel.diary.endReached", { defaultValue: "已经翻到最早的一篇了" })}</div>
                    )}
                  </div>
                ) : (
                  <EmptyState
                    title={t("panel.diary.emptyTitle", { defaultValue: "日记本还是空的" })}
                    description={t("panel.diary.emptyDesc", { defaultValue: "她主动写下的手记、以及小模型从对话里替她记下的重要片段，都会出现在这里。" })}
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
                    {t("panel.journal.count", { defaultValue: "共 {n} 页" }).replace("{n}", String(pages.length || journalIndex.length))}
                  </span>
                  <span className="tm-journal-actions">
                    {/* 刷新按钮已移除（1.2.5）：书页指纹（页码:段数:末笔时刻）随 5s
                        轮询自动检测，新页/续写都会自动重拉整本，无需手动刷新 */}
                    <Button tone="primary" onClick={onInviteJournal}>
                      {t("panel.journal.invite", { defaultValue: "请她写一篇" })}
                    </Button>
                  </span>
                </div>
                {invitePending ? (
                  <div className="tm-derived">
                    {t("panel.journal.invitePending", { defaultValue: "邀请已递出，正等她落笔——她说写就写、说缓就缓，下一页会自己出现在这里。" })}
                  </div>
                ) : null}
                <div className="tm-derived">
                  {t("panel.journal.hint", { defaultValue: "她每隔一段时间自己写的一篇日记，只给你看；翻页看看她这段时间在想什么。" })}
                </div>
                {visibleArchive ? (
                  <JournalBook
                    t={t}
                    page={visibleArchive}
                    position={archIdx !== null ? archIdx + 1 : 1}
                    totalPages={archiveList.length}
                    hasPrev={archIdx !== null && archIdx > 0}
                    hasNext={archIdx !== null && archIdx < archiveList.length - 1}
                    onBack={() => { setOpenArchiveNo(null) }}
                    onPrev={() => { if (archIdx !== null && archIdx > 0) setOpenArchiveNo(archiveList[archIdx - 1].page_no || null) }}
                    onNext={() => { if (archIdx !== null && archIdx < archiveList.length - 1) setOpenArchiveNo(archiveList[archIdx + 1].page_no || null) }}
                    archiveLabel={t("panel.journal.archiveBadge", { defaultValue: "藏书阁·合订本" })}
                  />
                ) : bookLoaded && pages.length === 0 ? (
                  <EmptyState
                    title={t("panel.journal.emptyTitle", { defaultValue: "还没有写过日记" })}
                    description={t("panel.journal.emptyDesc", { defaultValue: "点右上角「请她写一篇」递个邀请；当她愿意写时，第一页会出现在这里。" })}
                  />
                ) : visiblePage ? (
                  <JournalBook
                    t={t}
                    page={visiblePage}
                    position={openIdx + 1}
                    totalPages={pages.length}
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
                    {/* 刷新按钮已移除（1.2.5）：review_brief 篇数随 5s 轮询自动检测，
                        写完新篇会自动重拉全部篇目，无需手动刷新 */}
                    <Button
                      tone="primary"
                      onClick={onWriteReviewNow}
                      disabled={reviewLoading || !!(reviewBrief && reviewBrief.writing)}
                    >
                      {reviewBrief && reviewBrief.writing
                        ? t("panel.review.writing", { defaultValue: "正在写…" })
                        : t("panel.review.writeNow", { defaultValue: "立即写一篇" })}
                    </Button>
                    <Button tone="danger" onClick={onClearReview}>
                      {t("panel.review.clear", { defaultValue: "清空" })}
                    </Button>
                  </span>
                </div>
                {reviewBrief && reviewBrief.writing ? (
                  <div className="tm-derived">
                    {t("panel.review.writingHint", { defaultValue: "她正把这段时间写下来，写完会自动出现在这里，不用守着。" })}
                  </div>
                ) : null}
                {/* 成文失败常驻兜底（1.3.0 第十一轮）：失败结论不能被 toast 时序
                    和"面板当时开着没"绑死——写失败后下一次点击受理时后端会把结论位
                    清空（write_review_now 的作废逻辑），警示行随之消失 */}
                {reviewBrief && !reviewBrief.writing && reviewBrief.last_result && !reviewBrief.last_result.written ? (
                  <div className="tm-derived">
                    {t("panel.review.lastFailed", { defaultValue: "上一篇没写成，素材还留着，可以再点一次。" })}
                  </div>
                ) : null}
                <div className="tm-derived">
                  {t("panel.review.hint", { defaultValue: "对我的记录" })}
                </div>
                {visibleReviewArchive ? (
                  <ReviewBook
                    t={t}
                    entry={visibleReviewArchive}
                    position={reviewArchIdx + 1}
                    totalFiles={reviewArchive.length}
                    hasPrev={reviewArchIdx > 0}
                    hasNext={reviewArchIdx >= 0 && reviewArchIdx < reviewArchive.length - 1}
                    onBack={() => { setOpenReviewArchiveTs(null) }}
                    onPrev={() => { if (reviewArchIdx > 0) setOpenReviewArchiveTs(String(reviewArchive[reviewArchIdx - 1].ts || "")) }}
                    onNext={() => { if (reviewArchIdx >= 0 && reviewArchIdx < reviewArchive.length - 1) setOpenReviewArchiveTs(String(reviewArchive[reviewArchIdx + 1].ts || "")) }}
                    archiveLabel={t("panel.review.archiveBadge", { defaultValue: "档案室·旧卷宗" })}
                  />
                ) : visibleReview ? (
                  <ReviewBook
                    t={t}
                    entry={visibleReview}
                    position={reviewIdx + 1}
                    totalFiles={reviewEntries.length}
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
                    onOpen={(ts) => { setOpenReviewTs(ts) }}
                    stack={reviewArchiveCount > 0 ? (
                      <ArchiveStack
                        t={t}
                        brief={reviewArchiveBrief || {}}
                        loading={reviewArchiveLoading}
                        onOpen={openReviewArchive}
                        file
                        seal={t("panel.review.archiveSeal", { defaultValue: "档" })}
                        tip={t("panel.review.archiveTip", { defaultValue: "攒满下架的旧卷宗都封存在这里——点开只读翻阅" })}
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
        <span className="tm-tl-badge" data-kind="self">{t("panel.diary.selfBadge", { defaultValue: "她写" })}</span>
        {item.mood ? <span className="tm-tl-mood">{item.mood}</span> : null}
        <span className="tm-tl-ts">{fmtTs(item.ts)}</span>
      </div>
      <div className="tm-tl-entry">{item.entry}</div>
    </div>
  )
}

// 个人日记书架：一排"站着的书"——每页一根书脊（竖排起始日期；1.3.1e 精简：
// 页码方块与书根段数退场，信息全在悬停 tooltip 与翻开后的页眉——用户反馈架上
// "2、1、2、1"数字成排多余），书脊皮色 = 那段时间的心情均值（shelfInk：1.3.1d
// 起为封皮同族粉的明度阶——色相零漂移、心情只驱动浓淡，整架永远整齐；
// 目录行心情圆点同函数）；悬停整本抽出，点一根＝抽出来翻开。旧版迁移页贴角签
function JournalShelf(props: {
  t: TFunc
  index: Array<JournalPageHeader & { entries?: Array<{ text?: string }> }>
  onOpen: (pageNo: number) => void
  loading: boolean
  // 藏书阁（1.3.0）：书架末尾那根横放的合订本（空则不画）——真元素坐在同一块木隔板上
  stack?: any
}) {
  const { t, index, onOpen, loading, stack } = props
  if (!index.length) {
    return <div className="tm-derived">{loading ? t("panel.journal.loading", { defaultValue: "翻开日记本…" }) : ""}</div>
  }
  return (
    <div className="tmb-shelf">
      {index.map((page) => {
        const no = Number(page.page_no || 0)
        return (
          <button
            key={String(no)}
            type="button"
            className="tmb-spine"
            title={shelfTip(t, no, journalRangeLabel(page), t(journalTrendKey(page.mood_avg), { defaultValue: "" }))}
            style={{ "--tmb-spine-base": shelfInk(page.mood_avg) } as Record<string, string>}
            onClick={() => onOpen(no)}
          >
            <span className="tmb-spine-top" />
            <span className="tmb-spine-date">{spineDate(page.started_at)}</span>
            {page.legacy ? <span className="tmb-spine-flag">{t("panel.journal.legacyShort", { defaultValue: "旧" })}</span> : null}
          </button>
        )
      })}
      {stack}
    </div>
  )
}

// 手写本阅读视图：布面书脊（线装三孔 + 竖排题签）+ 暖纸页 + 丝带书签 + sticky 页脚
// （目录与翻页常驻下缘，正文随外层自然滚动 = 连续卷轴 + 分页视觉）。
// 每段续写是一张自己的纸（自己的边与影），正文里的【小标题】在显示层解析成分节
function JournalBook(props: {
  t: TFunc
  page: JournalPage
  // 架上第几本（1 起，随淘汰变）——与 page_no（全书累计页码，永不重编）是两个数，
  // 各到一个位：页眉用页码（版权页那个），页脚用架上位置（与上一页/下一页同口径）。
  // 混用会在淘汰发生后显成「63 / 52」这种废话（1.3.0 真机前自查发现）
  position: number
  totalPages: number
  hasPrev: boolean
  hasNext: boolean
  onBack: () => void
  onPrev: () => void
  onNext: () => void
  // 藏书阁模式（1.3.0）：题签换口径——同一套纸，两处出身（页眉页码/日期/心情照常，
  // 都是存储里现成的事实）
  archiveLabel?: string
}) {
  const { t, page, position, totalPages, hasPrev, hasNext, onBack, onPrev, onNext, archiveLabel } = props
  const sheets = (page.entries || []).map((entry, idx) => ({
    key: String(entry.ts || idx),
    when: fmtTs(entry.ts),
    secs: splitSections(entry.text),
  }))
  // 首字下沉全页只给一次：定位到第一个真有正文的节
  let leadE = -1
  let leadS = -1
  for (let e = 0; e < sheets.length && leadE < 0; e++) {
    for (let s = 0; s < sheets[e].secs.length; s++) {
      if (sheets[e].secs[s].paras.length) { leadE = e; leadS = s; break }
    }
  }
  const segLabel = t("panel.journal.entryCount", { defaultValue: "{n} 段" }).replace("{n}", String(sheets.length))
  return (
    <div className="tmb-book">
      <div className="tmb-book-strip">
        <span className="tmb-book-stitch" />
        <span className="tmb-book-title">{archiveLabel || t("panel.journal.spineTitle", { defaultValue: "她的日记" })}</span>
      </div>
      <div className="tmb-page">
        <span className="tmb-ribbon" />
        <div className="tmb-head">
          <span className="tmb-head-kicker">{t("panel.journal.kicker", { defaultValue: "她的手写" })}</span>
          <span className="tmb-head-no">
            {t("panel.journal.pageN", { defaultValue: "第 {n} 页" }).replace("{n}", String(page.page_no || ""))}
          </span>
          <span className="tmb-head-meta">{journalRangeLabel(page) || t("panel.journal.noDate", { defaultValue: "未注明日期" })}</span>
          {page.legacy ? <span className="tmb-head-meta">{t("panel.journal.legacy", { defaultValue: "旧版周记" })}</span> : null}
          <span className="tmb-head-spacer" />
          <span className="tmb-head-trend">
            <span className="tm-mood-dot" style={{ background: shelfInk(page.mood_avg) }} />
            {t(journalTrendKey(page.mood_avg), { defaultValue: "" })}
          </span>
        </div>
        <div className="tmb-page-body">
          {sheets.map((sheet, ei) => (
            <div className="tmb-entry" key={sheet.key}>
              {sheet.when ? <div className="tmb-entry-when">{sheet.when}</div> : null}
              {sheet.secs.map((sec, si) => (
                <div className="tmb-sec" key={String(si)}>
                  {sec.title ? <div className="tmb-sec-title">{sec.title}</div> : null}
                  {sec.paras.map((para, pi) => (
                    <div
                      key={String(pi)}
                      className={ei === leadE && si === leadS && pi === 0 ? "tmb-text tmb-text--lead" : "tmb-text"}
                    >
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
            {t("panel.journal.backToShelf", { defaultValue: "← 放回书架" })}
          </button>
          <span className="tmb-foot-mid">
            <button type="button" className="tmb-btn" onClick={onPrev} disabled={!hasPrev}>
              {t("panel.journal.prev", { defaultValue: "上一页" })}
            </button>
            <span className="tmb-ind">{position} / {totalPages}</span>
            <span className="tmb-leaf-ind">{segLabel}</span>
            <button type="button" className="tmb-btn" onClick={onNext} disabled={!hasNext}>
              {t("panel.journal.next", { defaultValue: "下一页" })}
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

// 我的日记档案架：一根根档案盒脊（硬纸壳 + 竖排成文日期 + 书根轮数 + 盒号），
// 冷色不参与心情（这本是第三者写的）；上方一条等宽口径的素材进度——
// 1.3.0 补齐双门槛：轮数进度条之外天数维度与到期判定也可见（满 N 轮或满 M 天
// 任先到先写，过去只显轮数那条，天数到了用户看不出为什么突然动笔）；
// 架末尾那只档案盒（stack）即档案室入口（空则不画）
function ReviewShelf(props: {
  t: TFunc
  progress: ReviewProgress
  entries: ReviewEntry[]
  loaded: boolean
  loading: boolean
  onOpen: (ts: string) => void
  // 档案室入口（1.3.0）：与日记书架的合订本摞同位同款，由调用侧画好传入
  stack?: any
}) {
  const { t, progress, entries, loaded, loading, onOpen, stack } = props
  const turns = Number(progress.turns || 0)
  const threshold = Math.max(1, Number(progress.turns_threshold || 50))
  const pct = Math.min(100, Math.round((turns / threshold) * 100))
  const days = Number(progress.days || 0)
  const daysThreshold = Math.max(1, Number(progress.days_threshold || 7))
  const daysPct = Math.min(100, Math.round((days / daysThreshold) * 100))
  if (!entries.length) {
    return loaded ? (
      <EmptyState
        title={t("panel.review.emptyTitle", { defaultValue: "还没有写过评价" })}
        description={t("panel.review.emptyDesc", { defaultValue: "攒够一段时间的相处素材后会自动写第一篇；也可以点右上角「立即写一篇」（至少需要 10 轮互动）。" })}
      />
    ) : (
      <div className="tm-derived">{loading ? t("panel.journal.loading", { defaultValue: "翻开日记本…" }) : ""}</div>
    )
  }
  return (
    <div className="tm-review">
      <div className="tmb-meter">
        <div className="tmb-meter-row">
          <span>
            {t("panel.review.progress", { defaultValue: "下一份素材：{n} / {total} 轮" })
              .replace("{n}", String(turns))
              .replace("{total}", String(threshold))}
          </span>
          <span className="tmb-head-spacer" />
          {progress.due ? (
            <span className="tmb-meter-due">{t("panel.review.dueNow", { defaultValue: "到时候了，会自己动笔" })}</span>
          ) : null}
          <span>{pct}%</span>
        </div>
        <div className="tmb-meter-track">
          <div className="tmb-meter-fill" style={{ width: `${pct}%` }} />
        </div>
        {/* 天数维度（1.3.0）：轮数没满但日子到了同样成文——两条门槛都要看得见 */}
        <div className="tmb-meter-row tmb-meter-row--sub">
          <span>
            {t("panel.review.daysProgress", { defaultValue: "或满 {total} 天（现第 {d} 天）" })
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
      <div className="tmb-shelf">
        {entries.map((entry, i) => {
          const ts = String(entry.ts || "")
          // 1.3.1e：卷数方块与书根「轮」字退场（架上数字成排被用户判多余），
          // 轮数/区间/成文日全部收进悬停 tooltip；脊面只留竖排成文日
          const turns = String(entry.turns ?? 0)
          return (
            <button
              key={ts || String(i)}
              type="button"
              className="tmb-spine tmb-spine--file"
              title={fileTip(t, ts, spanShort(entry.span), turns)}
              onClick={() => onOpen(ts)}
            >
              <span className="tmb-spine-top" />
              <span className="tmb-spine-date">{spineDate(ts)}</span>
            </button>
          )
        })}
        {stack}
      </div>
    </div>
  )
}

// 铅印卷宗阅读视图（1.3.0 改版：双栏档案袋）：冷白纸页内分两栏——
// 左窄栏=卷首事实（成文日/区间/轮数/她自主起的情绪）+「本卷依据」快照栏
// （语气分布/心情走向/原话摘录，旧篇目缺字段整栏隐藏）+雾靛落款印，读作
// 档案袋封面；右宽栏=居中标题+纯正文。DOM 顺序正文在前、卷尾信息在后，
// 窄窗（<460px）塔成单列时自然就是「正文优先」降级；宽窗用 grid 显式
// 指定栏位把信息栏拉回左列。页眉行取消（字段全部住进左栏，去重）；
// 页脚去掉与左栏重复的「{n} 轮」。与日记本同款 sticky 页脚与跨卷翻页，
// 档案室模式题签换口径同一套纸两处出身
function ReviewBook(props: {
  t: TFunc
  entry: ReviewEntry
  position: number
  totalFiles: number
  hasPrev: boolean
  hasNext: boolean
  onBack: () => void
  onPrev: () => void
  onNext: () => void
  // 档案室模式（1.3.0）：题签换口径——卷首日期/区间/依据都是存储里现成的事实
  archiveLabel?: string
}) {
  const { t, entry, position, totalFiles, hasPrev, hasNext, onBack, onPrev, onNext, archiveLabel } = props
  const paras = splitParas(entry.text)
  const turns = String(entry.turns ?? 0)
  const span = spanShort(entry.span)
  const selfActions = String(entry.self_action_count ?? 0)
  // 本卷依据：三项都取自 review_record 的快照字段，缺任一项只缺那一行
  const toneEntries = Object.entries(entry.tone || {}).filter(([, n]) => Number(n) > 0)
  const toneParts = toneEntries.map(([label, n]) => {
    const key = toneLabelKey(label)
    const word = key ? t(key, { defaultValue: label }) : label
    return `${word}×${Number(n)}`
  })
  const moodAvg = entry.mood_avg === null || entry.mood_avg === undefined ? null : Number(entry.mood_avg)
  const quotes = Array.isArray(entry.quotes) ? entry.quotes.filter((q) => String(q && q.quote || "").trim()) : []
  const hasEvidence = toneParts.length > 0 || moodAvg !== null || quotes.length > 0
  return (
    <div className="tmb-book">
      <div className="tmb-book-strip tmb-book-strip--file">
        <span className="tmb-book-holes" />
        <span className="tmb-book-title tmb-book-title--file">{archiveLabel || t("panel.review.spineTitle", { defaultValue: "相处卷宗" })}</span>
      </div>
      <div className="tmb-page tmb-page--file">
        <div className="tmb-file-cols">
          <div className="tmb-file-main">
            <div className="tmb-file-title">{t("panel.review.docTitle", { defaultValue: "关于这段时间的记录" })}</div>
            {paras.map((para, pi) => (
              <div className="tmb-text tmb-text--file" key={String(pi)}>{para}</div>
            ))}
          </div>
          <aside className="tmb-file-aside">
            <div className="tmb-file-date">{String(entry.ts || "").slice(0, 10) || t("panel.journal.noDate", { defaultValue: "未注明日期" })}</div>
            <div className="tmb-file-kicker">{t("panel.review.kicker", { defaultValue: "第三者记录" })} · {t("panel.review.tipComposed", { defaultValue: "成文" })}</div>
            <div className="tmb-file-rows">
              <div className="tmb-file-row">
                <span>{t("panel.review.fieldSpan", { defaultValue: "统计区间" })}</span>
                <b>{span || "--"}</b>
              </div>
              <div className="tmb-file-row">
                <span>{t("panel.review.fieldTurns", { defaultValue: "互动轮数" })}</span>
                <b>{turns}</b>
              </div>
              <div className="tmb-file-row">
                <span>{t("panel.review.fieldSelfActions", { defaultValue: "她自主起的情绪" })}</span>
                <b>{selfActions}</b>
              </div>
            </div>
            {hasEvidence ? (
              <div className="tmb-evidence">
                <div className="tmb-evidence-title">{t("panel.review.evidence", { defaultValue: "本卷依据" })}</div>
                {toneParts.length ? (
                  <div className="tmb-evidence-line">
                    <span className="tmb-evidence-label">{t("panel.review.evidenceTone", { defaultValue: "她的语气分布" })}</span>
                    <span className="tmb-evidence-value">{toneParts.join(" · ")}</span>
                  </div>
                ) : null}
                {moodAvg !== null ? (
                  <div className="tmb-evidence-line">
                    <span className="tmb-evidence-label">{t("panel.review.evidenceMood", { defaultValue: "她的心情走向" })}</span>
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
            ) : null}
            <div className="tmb-file-sign">
              <span className="tmb-seal">{t("panel.review.seal", { defaultValue: "观察者记" })}</span>
            </div>
          </aside>
        </div>
        <div className="tmb-foot">
          <button type="button" className="tmb-btn" onClick={onBack}>
            {t("panel.review.backToShelf", { defaultValue: "← 放回架上" })}
          </button>
          <span className="tmb-foot-mid">
            <button type="button" className="tmb-btn" onClick={onPrev} disabled={!hasPrev}>
              {t("panel.review.prevFile", { defaultValue: "前一卷" })}
            </button>
            <span className="tmb-ind">{position} / {totalFiles}</span>
            <button type="button" className="tmb-btn" onClick={onNext} disabled={!hasNext}>
              {t("panel.review.nextFile", { defaultValue: "后一卷" })}
            </button>
          </span>
        </div>
      </div>
    </div>
  )
}

// ---- 书本视图的显示层辅助（全部纯函数；存储与注入文本零改动）----

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

// 书脊皮色（1.3.1d 单色族明度阶）：全书脊锁死在封皮同族粉（H≈340），心情只驱动
// 浓淡——开心落鲜亮粉、低落落深灰玫瑰、无数据/近零落雾粉。上一版（1.3.1c）以暖灰
// 为中点向灰玫（H345）与雾紫（H268）两端插值，摆幅近 77°：相邻本冷暖交错时架上
// 读作"乱"不读作"梯度"，用户反馈"书脊颜色不统一"。色相零漂移后情绪信息仍在，
// 只是从"变色"降为"变深浅"；|mood|/0.85 归一（预览对拍时的饱和点，保留），
// 暖=好/冷=坏（浓=好/灰=坏）的语义方向不变
function shelfInk(mood?: number | null): string {
  if (mood === null || mood === undefined) return "rgb(232, 214, 220)"
  const v = Math.max(-1, Math.min(1, mood))
  const to = v < 0 ? [197, 164, 178] : [240, 182, 203]
  const zero = [232, 214, 220]
  const ratio = Math.min(1, Math.abs(v) / 0.85)
  const mix = (i: number) => Math.round(zero[i] + (to[i] - zero[i]) * ratio)
  return `rgb(${mix(0)}, ${mix(1)}, ${mix(2)})`
}

// 书脊上的竖排日期：只用起始日（一根脊装不下区间），完整区间在 title 里
function spineDate(ts?: string): string {
  return String(ts || "").replace("T", " ").slice(5, 10) || "--"
}

// "2026-08-01~2026-08-07" → "08-01 ~ 08-07"（等宽口径行与盒脊悬停共用）
function spanShort(span?: string): string {
  const parts = String(span || "").split("~").map((p) => p.trim().slice(5, 10)).filter((p) => p)
  return parts.join(" ~ ")
}

// 悬停说明（个人日记）：第 N 页 · 日期区间 · 她的心情
// 这里的 N 是 page_no（全书累计页码，与页眉同一个数），永不重编
function shelfTip(t: TFunc, no: number, range: string, trend: string): string {
  const parts: string[] = [t("panel.journal.pageN", { defaultValue: "第 {n} 页" }).replace("{n}", String(no))]
  if (range) parts.push(range)
  if (trend) parts.push(trend)
  return parts.join(" · ")
}

// 藏书阁的合订本（1.3.0）：书架末尾横叠的一小摞——下架的旧页都在这儿，只读翻阅。
// 不标本数、不提 52 上限（1.3.0 显示层纪律：不在活架上加计数）；悬停只给时段事实。
// 1.3.0 我的日记档案室复用本组件：file 换冷灰档案盒配色，seal/tip 换口径
function ArchiveStack(props: {
  t: TFunc
  brief: JournalArchiveBrief | ReviewArchiveBrief
  loading: boolean
  onOpen: () => void
  file?: boolean
  seal?: string
  tip?: string
}) {
  const { t, brief, loading, onOpen, file, seal, tip } = props
  const from = String(brief.first_ts || "").slice(0, 10)
  const to = String(brief.last_ts || "").slice(0, 10)
  const fullTip = [
    tip || t("panel.journal.archiveTip", { defaultValue: "写满下架的旧页，合订在这一摞里——点开只读翻阅" }),
    from ? `${from} – ${to || "?"}` : "",
    loading ? (file
      ? t("panel.review.archiveLoading", { defaultValue: "取旧卷宗…" })
      : t("panel.journal.archiveLoading", { defaultValue: "取合订本…" })) : "",
  ].filter((p) => p).join(" · ")
  return (
    <button type="button" className={file ? "tmb-stack tmb-stack--file" : "tmb-stack"} title={fullTip} onClick={onOpen}>
      <span className="tmb-stack-slab tmb-stack-slab--back" />
      <span className="tmb-stack-slab tmb-stack-slab--mid" />
      <span className="tmb-stack-slab tmb-stack-slab--top">
        <span className="tmb-stack-seal">{seal || t("panel.journal.archiveSeal", { defaultValue: "藏" })}</span>
        <span className="tmb-stack-ribbon" />
      </span>
    </button>
  )
}

// 悬停说明（我的日记）：成文日 · 统计区间 · 互动轮数——全部取存储里现成的值，
// 不派生任何会随淘汰平移的序号
function fileTip(t: TFunc, ts: string, span: string, turns: string): string {
  const parts: string[] = [
    `${t("panel.review.tipComposed", { defaultValue: "成文" })} ${ts.slice(0, 10) || "--"}`,
  ]
  if (span) parts.push(`${t("panel.review.fieldSpan", { defaultValue: "统计区间" })} ${span}`)
  parts.push(t("panel.review.turnsMeta", { defaultValue: "{n} 轮" }).replace("{n}", turns))
  return parts.join(" · ")
}
