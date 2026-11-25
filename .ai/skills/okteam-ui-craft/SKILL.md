---
name: okteam-ui-craft
description: >-
  okTeam 企业级前端 UI 设计与开发唯一规范（对标 Linear / Stripe / Personio）。
  在开发任何前端页面、组件、表单、表格、排班表、双语文案或公用平板打卡终端前必须加载。
---

# okTeam UI Craft Specification

面向西班牙华人企业主与店长的极简中文体验 + 西语合规输出。追求**克制、高信息密度、零装饰噪音**的企业级生产力工具质感。

## 1. 视觉系统红线（去 AI 味锁死标准）
- **画布与层级**：页面底色固定浅灰 `bg-slate-50`；主工作区/卡片使用纯白 `bg-white`；边界严格使用 `1px` 细边框 `border-slate-200`。
- **色彩规范**：
  - 主色：`slate-900`（主标题、正文核心信息、主操作按钮）。
  - 点缀色：`teal-700`（激活导航、关键强调、焦点环，**全屏视觉占比严格 <= 5%**）。
  - 低饱和语义状态色：成功 `text-emerald-700 bg-emerald-50 border-emerald-200`；警告 `text-amber-700 bg-amber-50 border-amber-200`；危险 `text-rose-700 bg-rose-50 border-rose-200`；中性 `text-slate-600 bg-slate-100 border-slate-200`。
- **数字排版**：所有工时、时间、日期、薪资、人数、统计数值**强制添加 `tabular-nums`**，保证纵向对齐。
- **绝对禁止清单**：
  - 禁止任何渐变背景、渐变文字、发光/霓虹效果。
  - 禁止使用 Emoji 作为功能图标（统一使用 `lucide-react` 16px/20px 细线图标）。
  - 禁止超过 1 层阴影嵌套（默认用 `1px` 细边框分隔层级，仅 Popover/Drawer/Dialog 浮层允许单层 `shadow-sm`）。

## 2. 三套固化母版骨架
所有业务页面必须归入以下 3 套标准骨架之一，禁止随意发明新布局：
1. **高密度吸边表格 + 侧边抽屉页**（考勤流水、员工名册、请假与合规文档列表）：
   - 顶栏紧凑工具条（筛选 + 主操作按钮）+ 全宽吸边 TanStack Table + 点击行从右侧滑出详情抽屉（Sheet/Drawer），减少页面跳转。
2. **双列档案配置页**（员工详细档案、门店与劳动法参数配置）：
   - 左侧固定二级分组导航 + 右侧纯白卡片分区表单（单区双列栅格 `grid-cols-1 md:grid-cols-2`）+ 吸底脏状态保存条。
3. **排班日历页**（门店周/月排班）：
   - 左侧固定员工列（`sticky left-0 bg-white`）+ 右侧横向日期/班次网格 + 实时 `tabular-nums` 周工时统计与 40h/12h 合规冲突标记。

## 3. 中西双语（`zh-CN` / `es-ES`）防爆版规范
- 西语长度常为中文的 2~3 倍：**严禁给按钮、Tab、标签、表头写死固定宽度（如 `w-24`）**，统一使用 `min-w-*` + 水平内边距 `px-3`/`px-4` + 自适应或 `truncate` 配合提示。
- 所有日期、时间、货币、数字格式化必须使用原生 `Intl.DateTimeFormat` / `Intl.NumberFormat`（结合当前语言与门店 IANA 时区），严禁手工字符串拼接。

## 4. 交互与输入法防误触（联动 `modern-web-guidance`）
- **中文 IME 回车防误触**：任何监听 `Enter` 键执行搜索、提交、新建的输入框，**必须判断 `!e.nativeEvent.isComposing && e.keyCode !== 229`**，防止拼音选词误提交。
- **原生能力优先**：优先使用语义化 HTML、原生 `<dialog>`/Popover、表单校验与标准键盘可达性（Focus Visible）。

## 5. 公用平板打卡终端（Kiosk Mode）规范
- 打卡反馈展示完成后，**3 秒内必须自动清屏并重置全部本地状态**（清空员工姓名、工号、最近打卡结果与输入缓存），防止公用设备隐私泄露或下一位员工误操作。

## 6. 外部 UI 能力按需调用规则（控 Token）
- **组件开发**：按需调用 `shadcn` 官方 MCP 查询最新组件文档与用法，严禁凭记忆编造旧版 API。
- **高阶视觉体检（严格按节点触发）**：**仅在完整页面完工或大改版时触发一次**——调用 Vercel `web-design-guidelines` Skill 做细节体检，并用 `chrome-devtools` MCP 切换 `es-ES` 模式截取桌面端（`1440px`）与移动端（`390px`）画面自检。日常小修严禁触发。
