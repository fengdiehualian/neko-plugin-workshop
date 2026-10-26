// Skill 列表面板 — Hosted UI（官方规范）
// 说明：按 N.E.K.O Hosted UI 约定，组件与 hooks 一律从 @neko/plugin-ui 导入，
// 禁止裸导入 react / npm 包（surface iframe 无法解析）。
// 数据来源：Python @ui.context(id="skills") 提供的 props.state，刷新走 api.refresh()。
import {
  Page,
  Card,
  Stack,
  Text,
  Input,
  RefreshButton,
  EmptyState,
  useState,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type SkillItem = { name: string; description: string; path: string; kind: string }
type SkillState = { skills?: SkillItem[]; count?: number; scan_status?: string }

const KIND_TONE: Record<string, string> = {
  symlink: "#fef3c7",
  namespace: "#ede9fe",
  standard: "#e0e7ff",
}

export default function SkillsPanel(props: PluginSurfaceProps<SkillState>) {
  const { state } = props
  const [keyword, setKeyword] = useState("")

  const skills = Array.isArray(state?.skills) ? state.skills : []
  const shown = skills.filter(
    (s) => !keyword || (s.name + (s.description || "")).toLowerCase().includes(keyword.toLowerCase())
  )

  return (
    <Page title="已加载 Skill" subtitle={state?.scan_status || "扫描状态未知"}>
      <Card title={`Skill 列表（${state?.count ?? skills.length} 个）`}>
        <Stack>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索 skill"
              style={{ flex: 1 }}
            />
            <RefreshButton />
          </div>
          {shown.length === 0 ? (
            <EmptyState title="无匹配 skill" />
          ) : (
            shown.map((s) => (
              <Card key={s.path}>
                <Stack>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <strong>{s.name}</strong>
                    <span
                      style={{
                        fontSize: 11,
                        padding: "2px 6px",
                        borderRadius: 4,
                        background: KIND_TONE[s.kind] || "#e5e7eb",
                      }}
                    >
                      {s.kind}
                    </span>
                  </div>
                  <Text>{s.description || "（无描述）"}</Text>
                  <Text style={{ color: "#9ca3af", fontSize: 11 }}>{s.path}</Text>
                </Stack>
              </Card>
            ))
          )}
        </Stack>
      </Card>
    </Page>
  )
}
