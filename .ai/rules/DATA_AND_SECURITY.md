# DATA & SECURITY RULES (Day 1 四大不可突破地基)

## 地基 1：5 类角色 + 租户与门店双重隔离（RBAC + Scope）
1. **标准 5 类角色**（定义于 `domain/security.py`）：
   - `owner`（企业主）：租户内全门店管理与审批权限。
   - `hr`（人事）：租户内人事、排班、考勤、文档管理权限。
   - `store_manager`（门店经理）：**仅限授权门店（`store_ids`）**内排班、考勤核对与日常管理。
   - `accountant`（外部会计师 / Gestoría）：只读合规报表、考勤汇总、薪资/合同导出权限，禁止修改考勤与排班。
   - `employee`（员工）：仅可访问本人考勤、排班、休假申请与个人文件。
2. **双重校验红线（P0）**：
   - 所有业务读写必须在查询条件中强制绑定 `tenant_id`；涉及门店资源时必须同时校验操作人的授权 `store_ids`（或本人 `employee_id`）。
   - **Supabase 安全边界**：前端仅可使用 Supabase Auth 获取 JWT；**严禁前端直连任何 Supabase 业务表或 Storage Bucket**。所有业务访问必须经 FastAPI 校验 JWT 后执行。

## 地基 2：全库时间统一 UTC + 门店时区（防夏令时错算）
- 数据库所有时间列统一使用 `TIMESTAMPTZ`（UTC 存储）；后端严禁使用 naive `datetime` 或 `datetime.utcnow()`，统一使用 `datetime.now(timezone.utc)`。
- 门店表必须携带 IANA 时区字段（如 `Europe/Madrid`、`Atlantic/Canary`）。
- 跨日班次、实际工时、两班 12h 间隔必须在 Domain 层通过 UTC 绝对差值计算；自然日归属与前端展示基于门店 IANA 时区转换，杜绝欧洲夏令时（DST 23h/25h 切换日）错算。

## 地基 3：软删除 + 4 年法定留存冻结与到期匿名化（GDPR × 西班牙劳动法）
- **严禁物理删除（P0）**：考勤、排班、合同、薪资单、签收与核心人事记录严禁 `DELETE`，统一使用软删除（`deleted_at`）或不可变追加日志。
- **法定留存冻结（Legal Hold）**：面对员工离职或 GDPR 删除权请求（Art. 17 GDPR），处于 **4 年法定留存期（`retention_until`）**内的数据执行合规冻结（*Bloqueo de datos*, Art. 32 LOPDGDD），屏蔽日常业务读写但保留劳工/税务监察可查性；期满后执行不可逆 **PII 匿名化**。
- **敏感字段加密（P0）**：`NIE/DNI`、`IBAN`、`NAF（社保号）` 严禁明文入库、严禁出现在日志/异常堆栈中；必须使用应用层加密（AES-256-GCM）存储，接口输出按角色脱敏。

## 地基 4：私有文件 60 秒临时签名链接 + 签收 SHA-256 哈希留痕
- 所有私有文件（劳动合同、工资单 *Nómina*、NIE 扫描件、病假单 *Baja médica* 等）存入私有对象存储（Private Bucket），严禁公开链接。
- 文件预览/下载必须经 FastAPI 鉴权（租户 + 角色 + 门店/本人归属）后签发有效期 **<= 60 秒** 的临时签名链接（Signed URL）。
- 合规文件签收（合同/工资单/月度考勤确认）必须记录：文件字节流 **SHA-256 哈希值**、签收人 `user_id`、UTC 时间戳、来源 IP 与 User-Agent，确保防篡改与法律举证能力。
