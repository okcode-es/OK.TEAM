# BACKEND ARCHITECTURE RULES (okTeam)

## 1. Clean Architecture 四层隔离红线
`backend/app/` 严格划分为 4 层，依赖方向仅允许**由外向内**指向 `domain`：

| 层级 | 目录 | 允许职责 | 严禁行为 |
| :--- | :--- | :--- | :--- |
| **Domain** | `app/domain/` | 实体、值对象、枚举、领域规则、领域异常、Repository 抽象接口（`Protocol`/`ABC`） | **零外部框架依赖**：严禁导入 `fastapi`、`pydantic`、`sqlalchemy`、`starlette`、`httpx`、`supabase` 等任何第三方框架（仅限 Python 标准库，由 `scripts/check_domain_purity.py` 强制拦截） |
| **Application** | `app/application/` | 用例编排（Use Cases）、应用层 DTO、事务边界协调、调用 Domain 接口 | 严禁引用 HTTP `Request`/`Response` 对象或直接拼写 ORM 查询细节 |
| **Infrastructure** | `app/infrastructure/` | SQLAlchemy 2.0 异步模型、Repository 实现、Alembic 迁移、Supabase JWT/Storage 适配、PII 加密 | 严禁承载业务法条判断与 HTTP 状态码映射 |
| **Interfaces** | `app/interfaces/` | FastAPI Router、Pydantic v2 请求/响应 Schema、依赖注入（`deps.py`）、异常映射 | 严禁出现 Fat Router（不得在路由里直接操作 SQLAlchemy Session 或写核心业务规则） |

## 2. SOLID 与代码规范
- **单一职责（SRP）**：每个 Use Case 聚焦单一业务动作；严禁 God Service。
- **依赖倒置（DIP）**：Application 层依赖 `domain` 定义的 `Protocol` 接口，由 `interfaces/api/deps.py` 注入 `infrastructure` 具体实现。
- **显式类型注解**：全部函数必须通过 `mypy` 严格检查；禁止裸 `dict[str, Any]` 作为领域模型传递。

## 3. 契约先行与 OpenAPI 同步（Single Source of Truth）
- FastAPI 路由的 Pydantic v2 Schema 是前后端接口的**唯一真实来源**。
- 所有路由必须显式声明 `response_model`、`status_code` 和清晰的 `operation_id`。
- 任何 API 路径、参数或响应结构变更后，必须执行 `scripts/sync-contracts.ps1` 重新导出 `contracts/openapi.json` 并生成前端 `schema.d.ts`；严禁前后端接口漂移。

## 4. Alembic 数据库迁移红线
- `alembic/env.py` 必须显式过滤并忽略 Supabase 系统 Schema：`auth`、`storage`、`realtime`、`supabase_functions`、`extensions`、`vault`。
- 所有迁移脚本必须实现可回滚的 `downgrade()`；严禁手工修改线上表结构跳过迁移。

## 5. 本地零 Token 质量门禁（提交前必过）
提交或上报前必须在 `backend/` 下全部通过，不通过直接自修，禁止上报：
1. `uv run pytest`
2. `uv run ruff check .`
3. `uv run mypy app`
4. `uv run python ../scripts/check_domain_purity.py`
