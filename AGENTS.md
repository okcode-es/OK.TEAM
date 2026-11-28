# okTeam Repository Agent Guide (`AGENTS.md`)

> 本文件为 OpenAI Codex CLI 与所有跨平台编程 Agent 的根目录入口（与 [GEMINI.md](GEMINI.md) 互为镜像）。所有业务、架构与合规细则以 `.ai/` 为单一真源，请按需加载，严禁全仓乱读。

## 1. 启动与交接必读（双向接力 Antigravity ↔ Codex）
每次接手任务前，按顺序仅读取：
1. `.ai/memory/SESSION_STATE.md`（当前进度、Alembic head、最新门禁状态，硬限 `<= 50` 行）
2. `.ai/memory/HANDOFF.md`（最新交接快照、改动文件、接口摘要与待办）
3. 执行 `git status -s` 检查工作区是否存在未提交现场

每次结束本轮工作或准备切回另一工具（Codex ↔ Antigravity）前，必须：
1. 如改动了后端 API/Schema，先跑 `.\scripts\sync-contracts.ps1`；
2. 跑通 `.\scripts\quality-gate.ps1`（或对应 `-Scope backend` / `-Scope frontend`）；
3. 更新 `.ai/memory/SESSION_STATE.md`（保持 `<= 50` 行）与 `.ai/memory/HANDOFF.md`（如有架构/业务决策同步写入 `.ai/memory/DECISIONS.md`）。

## 2. 单一真源路由（按任务按需读取）
- **后端架构与规范**：`.ai/rules/BACKEND_ARCHITECTURE.md`、`.ai/agents/backend-hydrogen.md`
- **西班牙劳动法合规**：`.ai/rules/SPAIN_LABOR_LAW.md`
- **数据安全与 GDPR**：`.ai/rules/DATA_AND_SECURITY.md`
- **前端设计与交互规范**：`.ai/skills/okteam-ui-craft/SKILL.md`、`.ai/agents/frontend-hydrogen.md`
- **红队对抗审查清单**：`.ai/agents/red-team-gatekeeper.md`

## 3. 不可违反的工程与合规红线
1. **领域纯度**：`backend/app/domain/` 严禁导入任何第三方库（仅 Python 标准库）。
2. **多租户与门店/部门隔离**：所有读写必须绑定 `tenant_id`，并校验角色及 `store_ids ∩ department_scope`。
3. **隐私与不可变留存**：NIE/DNI/IBAN/NAF 必须加密存储、`kiosk_pin_hash` 必须加盐哈希且严禁回传或打印日志；严禁物理删除考勤/合同/核心人事记录；私有文件仅签发 `<= 60s` 临时签名链接。
4. **Alembic 边界**：严禁触碰 Supabase 系统 Schema（`auth`/`storage`/`realtime` 等），每个迁移必须具备可回滚 `downgrade()`。
5. **契约先行与前端边界**：前端严禁直连 Supabase 业务表或 Storage Bucket；严禁手写后端接口 DTO 类型，所有接口类型必须来自 `frontend/src/api/generated/schema.d.ts`（由 `.\scripts\sync-contracts.ps1` 生成）。
6. **双语齐平**：前端所有可见文案必须同时写入 `frontend/src/i18n/locales/zh-CN.json` 与 `es-ES.json`，保持键名 100% 一致。

## 4. 省 Token 与模型/推理挡位策略（ADR-016）
- **模型与 `reasoning_effort` 分级（Codex / GPT）**：
  - 架构设计、数据库迁移/RBAC/GDPR/劳动法红队审查、复杂并发排错 → 旗舰模型 + `high`
  - 常规前后端功能开发、单测与重构 → Codex 主力模型 + `medium`
  - 文案/i18n、样式微调、简单文档更新 → Mini 轻量模型 + `low`
- **默认单 Agent 直改**：中小型改动直接端到端执行并内联自查 `.ai/rules/`，避免多 Agent 重复读取大文件；仅在大型跨栈变更或高危迁移/权限/关账改动时触发独立红队审查（`<= 2` 轮熔断）。
- **上下文节流**：
  - **禁止整文件读取** `contracts/openapi.json` 或 `frontend/src/api/generated/schema.d.ts`，一律用 `rg` 精准定位目标接口/类型片段；
  - 对大源码文件先检索行号再局部读取；
  - 机器能查的交给本地脚本（`sync-contracts.ps1` / `quality-gate.ps1`），绝不靠人工肉眼多轮试错；
  - 浏览器验收优先 DOM/接口断言 + 最多 1 张关键截图，仅大改版或修移动端溢出时做双语 × 双视口全套截屏。
- **里程碑换新会话**：每完成一个阶段性交付或 `git commit` 后，更新 `SESSION_STATE.md` 并主动提醒开新会话。

## 5. 常用本地命令（PowerShell，仓库根目录）
- 同步契约（后端改完必跑）：`.\scripts\sync-contracts.ps1`
- 全量质量门禁：`.\scripts\quality-gate.ps1`（支持 `-Scope backend` / `-Scope frontend` / `-Scope all`）
- 前端生产构建：`cd frontend; npm run build`
- 后端单独测试：`cd backend; .\.venv\Scripts\python.exe -m pytest -q`
