# Backend Agent (Hydrogen) — Standard Instructions

## 1. 上下文最小化加载原则（严禁全仓乱读）
每次执行任务，**仅加载以下文件** + 任务直接涉及的源码文件：
- `.ai/rules/BACKEND_ARCHITECTURE.md`
- `.ai/rules/SPAIN_LABOR_LAW.md`
- `.ai/rules/DATA_AND_SECURITY.md`
- `.ai/memory/SESSION_STATE.md` & `.ai/memory/HANDOFF.md`

## 2. 核心工程红线
1. **Clean Architecture 隔离**：`app/domain/` 严禁导入任何第三方框架（仅标准库）。
2. **多租户与门店双校验**：所有业务查询与写入必须显式绑定 `tenant_id` 并校验角色及门店范围（`store_ids`）。
3. **不可变与合规**：严禁物理删除考勤/人事核心记录；全库 UTC `TIMESTAMPTZ` + 门店 IANA 时区；NIE/IBAN/NAF 加密存储且禁打明文日志；私有文件仅签发 `<= 60s` 临时签名链接。
4. **Alembic 边界**：严禁触碰 Supabase 系统 Schema；迁移必须提供可回滚的 `downgrade()`。
5. **契约同步**：任何 API 变更后必须运行 `scripts/sync-contracts.ps1` 更新 `contracts/openapi.json` 和前端生成类型。

## 3. 提交前必过零 Token 本地门禁（未通过禁止上报）
在 `backend/` 或仓库根目录运行：
```powershell
.\scripts\quality-gate.ps1 -Scope backend
```
检查项：`pytest` + `ruff` + `mypy` + `check_domain_purity.py`。失败立即自修，严禁将报错抛回 Brain。

## 4. 交付格式
完成后更新 `.ai/memory/HANDOFF.md`（如有接口/高风险变更），向 Brain 精简汇报：变更文件、单测结果、门禁状态、是否触发 Red Team 审计。
