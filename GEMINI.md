# okTeam Workspace Efficiency & Session Rules

> 本文件由 Antigravity 自动加载，作为 `OK.TEAM` 仓库常驻协作护栏；业务与合规细则仍以 `.ai/rules/`、`.ai/skills/okteam-ui-craft/SKILL.md` 及 `.ai/memory/` 为单一真源。

## 1. 开新对话主动提醒（Session Boundary Hygiene）
- 每次完成一个阶段性里程碑（如 Phase 完成、`git commit` / `git push` 完成、跨模块功能交付），或当前对话已经过 `/compact`、累计较多工具调用与截图轮次时，**必须在回复末尾主动用 1 句话给出明确判断**：
  - 建议开新对话时：明确提示原因，并确认已更新 `.ai/memory/SESSION_STATE.md`（与必要时的 `HANDOFF.md`），新对话可直接无缝衔接。
  - 仍处于同一小任务上下文且轮次较轻时：简短说明“当前上下文较轻，可继续本对话”。

## 2. 按任务体量自适应执行（Token-Efficient Execution，见 ADR-016）
- **中小型任务（默认单 Agent 直改）**：单模块功能、Bug 修复、UI/文案/样式微调、单接口或常规 CRUD、小范围重构，**直接由当前主 Agent 读写代码**，并按 `.ai/rules/DATA_AND_SECURITY.md` 与 `.ai/rules/SPAIN_LABOR_LAW.md` 执行内联安全/合规自查，**禁止**机械拆分为多个冷启动 Swarm Worker 或独立 Red Team 子 Agent 造成上下文重复读取。
- **大型/高危任务（按需启用多 Agent）**：仅当任务同时包含大型独立并行子流（如全新跨前后端大模块），或涉及高危鉴权/跨租户隔离/GDPR 隐私/薪资关账/核心数据库迁移且独立对抗审查具有明确收益时，才按需启 Swarm Worker 或 Red Team 子 Agent（严格维持 ≤ 2 轮熔断）。

## 3. 精简验证与上下文节流
- **分层浏览器验收**：常规改动优先使用 DOM/脚本断言 + **最多 1 张关键截图**验证；仅在整页大改版、新增复杂交互布局或修复移动端排版问题时，才执行双语（`zh-CN` / `es-ES`）× 双视口（`1440×900` / `390×844`）全套截屏（≤ 2 轮）。
- **按需加载大文档**：读取超大源码文件或外部指南（如 `modern-web-guidance`）时精准定位目标片段，避免一次性批量拉取数十 KB 非必要文档长期驻留上下文。
- **质量门禁不降级**：省 Token 只省重复上下文与冗余轮次，绝不跳过 `scripts/quality-gate.ps1`、`sync-contracts.ps1` 或核心安全自查。
