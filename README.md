# 多源数据采集与RPA自动化平台

面向网页抓取、API接入、增量采集、RPA流程编排、数据校验与采集监控的数据工程平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据源配置 | `datasource` | 数据源 | 源端编号、连接类型、接入地址 |
| 网页采集规则 | `crawler` | 采集规则 | 规则编号、目标网址、页面选择器 |
| API接入管理 | `apiclient` | API客户端 | 客户端编号、接口名称、请求方法 |
| 增量采集 | `incremental` | 增量游标 | 游标编号、来源标识、增量字段 |
| RPA任务编排 | `robot_task` | RPA任务 | 任务编号、任务名称、页面定位器 |
| 数据接入通道 | `ingestion` | 接入通道 | 通道编号、通道名称、输入格式 |
| 格式解析器 | `parser` | 解析配置 | 解析编号、输入格式、输出格式 |
| 采集结果校验 | `validator` | 校验规则 | 规则编号、校验字段、校验类型 |
| 采集去重 | `dedup` | 去重策略 | 策略编号、输入来源、去重键 |
| 采集调度 | `schedule` | 调度计划 | 计划编号、关联任务、调度周期 |
| 采集监控 | `monitor` | 监控面板 | 面板编号、采集源总数、今日采集量 |
| 采集日志 | `log_viewer` | 日志条目 | 日志编号、关联任务、日志级别 |
| 凭证管理 | `auth` | 认证凭证 | 凭证编号、凭证类型、关联数据源 |
| 采集转换管道 | `transform` | 转换节点 | 节点编号、输入来源、输出目标 |
| 数据流转编排 | `flow` | 流转管道 | 管道编号、管道名称、起始节点 |
| 数据血缘追踪 | `lineage` | 血缘关系 | 关系编号、上游节点、下游节点 |
| 采集报告 | `report` | 采集报告 | 报告编号、采集源、统计周期 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 转换执行与血缘

采集转换管道除了维护节点，还会按节点的 `依赖节点` 真正执行 `转换表达式`，
产出带版本信息的结果，并把字段版本关系写进数据血缘追踪。

### 数据口径

- **输入版本**：一条输入明细由“明细行版本 + 每个字段的字段版本”组成。更正字段时
  新增一条输入版本并生成新批次，**只有真正改动的字段换字段版本**；老批次仍按自己的
  数据版本读到原来的值和原来的字段版本。
- **运行快照**：每次运行在开始时固定输入快照，结果行记录 `输入版本`、`输入字段版本`、
  `输出字段版本` 与 `数据批次`。结果行一旦写入不再改动，所以不同批次的结果各自稳定。
- **失败传播**：节点按依赖做拓扑排序执行。前置节点没有已确认结果时，后继节点直接
  `阻塞`，不会拿未确认结果顶替；未确认结果会标出 `失败节点` 和 `失败原因`，也不能
  作为血缘追溯的起点。
- **更正重试**：重试时按“读取的字段版本 + 表达式 + 输出目标”判断节点是否受影响，
  未受影响的节点直接沿用上一次运行的结果（保留原运行编号与批次），不会重跑，
  也不会被改写成新值。被更正的明细只重跑依赖该字段的链路。
- **血缘持久化**：每个已确认结果都会写入来源字段版本 → 输出字段版本的关系，
  可按结果编号、批次、节点或字段版本查询，也可从已确认结果逐级追溯到原始字段版本。

### 示例链路

示例输入按“分拣明细”组织，三条节点组成两条链路：

```text
分拣明细 ──TRAN-KG（重量克 / 1000 → 重量公斤）──TRAN-REGION（sum(重量公斤) by 地区）
        └─TRAN-BOX（trim(箱号) → 规范箱号）
```

把 `D-0001` 的 `重量克` 从 1000 更正为 1200 后重试：`TRAN-KG` 与 `TRAN-REGION`
按新批次重算（华东合计 3 → 3.2），`TRAN-BOX` 整条分支沿用原结果；两个批次各自
读自己的字段版本（v1 得 1 公斤，v2 得 1.2 公斤）。

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/transform/batches` | 输入批次列表 |
| GET | `/api/transform/inputs?batch=` | 按批次查看当时生效的输入明细 |
| GET | `/api/transform/inputs/{明细}/versions` | 一条明细的全部输入版本 |
| POST | `/api/transform/inputs/{明细}/corrections` | 发布字段更正，生成新批次 |
| POST | `/api/transform/runs` | 执行转换，`{ values: { batch, retry } }` |
| GET | `/api/transform/runs` | 运行列表（失败/阻塞/沿用节点） |
| GET | `/api/transform/results?batch=&node=&status=` | 结果明细，含失败节点 |
| GET | `/api/transform/results/{明细}/history` | 同一明细各批次结果对照 |
| GET | `/api/lineage/relations?field_version=` | 按字段版本查询执行期血缘关系 |
| GET | `/api/lineage/trace/{结果编号}` | 从已确认结果追溯原始字段版本 |

### 测试

```bash
cd backend
python3 -m pytest tests -q
```
