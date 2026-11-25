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
4. **视觉与双语铁律**：严格遵循 `okteam-ui-craft`（`slate-50`/`white`/`slate-200`/`slate-900`/`teal-700<=5%`、`tabular-nums`、无渐变/霓虹/Emoji、按钮禁固定宽度、原生 `Intl` 格式化、输入框 `!isComposing` 防中文 IME 误触、公用平板打卡 3 秒清屏）。

## 3. 提交前必过零 Token 本地门禁（未通过禁止上报）
```powershell
.\scripts\quality-gate.ps1 -Scope frontend
```
检查项：`tsc --noEmit` + `eslint`。不通过直接自修，禁止上报。

## 4. 高阶视觉体检（严格按节点触发，日常小修不触发）
**仅在完整页面完工或大改版时触发一次**：
1. 加载 `.ai/skills/web-design-guidelines/SKILL.md` 对页面做细节体检。
2. 使用 `chrome-devtools` MCP 将应用切换至西语（`es-ES`）模式，分别截取桌面端（`1440px`）与移动端（`390px`）画面，核查文本溢出、对齐、对比度与表格密度后汇报结果。
