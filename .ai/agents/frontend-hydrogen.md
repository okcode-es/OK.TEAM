# Frontend Agent (Hydrogen) — Standard Instructions

## 1. 上下文最小化加载原则
每次执行任务，**仅加载以下文件** + 任务直接涉及的页面/组件文件：
- `.ai/skills/okteam-ui-craft/SKILL.md`
- `modern-web-guidance` Skill
- `.ai/memory/SESSION_STATE.md` & `.ai/memory/HANDOFF.md`
- 开发 shadcn/ui 组件时，**按需调用 `shadcn` 官方 MCP** 查阅最新组件 API，严禁凭记忆编写旧版组件用法。

## 2. 核心工程红线
1. **Supabase 安全边界**：仅允许通过 `src/lib/supabase.ts` 使用 Supabase Auth 获取 JWT；**严禁直连任何业务表或 Storage Bucket**。
2. **严禁手写接口类型**：所有后端接口请求与数据类型必须消费 `src/api/generated/schema.d.ts`（由 `contracts/openapi.json` 自动生成）与 `src/api/client.ts`。
3. **Bulletproof React 目录**：业务代码严格收敛至 `src/features/<feature>/`。
4. **视觉、双语与表单安全铁律**：严格遵循 `okteam-ui-craft`（深海夜蓝 `#0B1120` 侧边栏、`#F3F5F9` 主画布、`#2563EB` 主色、白底可读小字 ≥ `text-slate-600`、`tabular-nums`、无渐变/霓虹/Emoji、窄屏 KPI 与按钮禁硬截断、原生 `Intl` 格式化、`!isComposing` 防 IME 误触、表单遵循 `AbortController` + 未就绪禁写 + 失败重试 + 作用域 `key` 重置 + 保存后父级实时刷新、Kiosk 3 秒清屏）。

## 3. 提交前必过零 Token 本地门禁（未通过禁止上报）
```powershell
.\scripts\quality-gate.ps1 -Scope frontend
```
检查项：`tsc --noEmit` + `eslint`。不通过直接自修，禁止上报。

## 4. 高阶视觉体检（严格按节点触发，日常小修不触发）
**仅在完整页面完工或大改版时触发一次**：
1. 加载 `.ai/skills/web-design-guidelines/SKILL.md` 对页面做细节体检。
2. 使用 `chrome-devtools` MCP 在 `zh-CN` 与 `es-ES` 下分别检查桌面端（`1440×900`）与移动端（`390×844`），除排版/溢出/对比度外，对新增或修改的编辑表单必须实测“填写→保存→当页数据刷新→重开回显”闭环，并在动效页面验证 `prefers-reduced-motion`。
