# JL-Agent

本地简历生成 Agent：输入少量个人背景 + 目标岗位 JD，一键生成专业、可打印、页面填满（无大面积空白）的 HTML 简历。

> 开发状态：核心功能已交付（R17~R21：时间约束、插件双层启动、高级设置抽屉、便携版分发、版本机制）。

## 核心特性

- **一键生成**：填写基本信息、教育/实习/项目经历与目标岗位 JD，自动生成完整简历。
- **面向岗位定制**：JD 分析产出共享事实表，项目经验、自我评价、技能板块按岗位定向优化。
- **页面填满**：内置排版层（compact/normal/loose 三档全局密度）+ 内容层（详略/数量档）双层动态适配，收敛至页末填充度 ≥85%，一页/两页版分别定制。
- **数量硬性约束**：项目条数由页数与实习经历条数决定（如 1 页无实习 2 个项目），绝不漂移。
- **真实优先**：数值直接生成合理数值（不用占位符）；AI 创造/美化内容有来源标记；编辑锁定项绝不自动裁剪。
- **本地单用户**：所有数据保存在本机，无云服务、无账户，数据不出机。
- **零构建**：前端为原生 HTML/CSS/JS，后端 FastAPI，一键启动。

## 技术栈

- Python 3.10+ / FastAPI / uvicorn / pydantic v2 / httpx / Pillow / jsonschema
- 前端：原生 HTML/CSS/JS（零构建）
- LLM：OpenAI 兼容接口（默认 DeepSeek），可在配置中指定 base_url / model

## 快速开始

### 1. 环境准备

```bash
# 建议 Python 3.10+
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env        # 填入你的 API Key（如 DEEPSEEK_API_KEY=sk-xxx）
cp config.example.json config.json   # 可选：修改 base_url / model / 搜索配置
```

- `.env` 存放密钥，**不入库**（已被 .gitignore 忽略）。
- `config.json` 未创建时自动回退到 `config.example.json`。

### 3. 启动

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000> 即进入简历工作台。

### 免环境部署（无需安装 Python）

| 方式 | 适用 | 说明 |
|---|---|---|
| **独立 EXE** | 大众用户（Windows 10/11） | 单文件自包含，双击即用：[EXE 安装指南与 FAQ](docs/EXE_GUIDE.md) |
| **便携 ZIP** | 分享给朋友 / 无 Python 环境 | 内含嵌入式 Python + 依赖，解压双击 bat：[构建脚本](portable/build_portable.py) |

### 版本更新

- 版本号唯一来源：`app/version.py`（顶栏自动显示 `v<版本>`）。
- 检查更新：`python scripts\update_check.py`（对比 GitHub Release latest tag）。

详细部署、环境配置、更新与发布流程见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

### 4. 使用流程

1. **填写简历信息**：基本信息、照片（可选）、教育背景（≤3 条）、实习经历（≤2 段）、项目经验、技能（必填）、证书荣誉（可选）。
2. **填写目标岗位 JD**（1~5 套，同一职业方向），选择一页/两页版与水印模式。
3. **生成**：任务串行执行，SSE 实时展示板块进度（JD 分析 → 各板块生成 → 装配 → 适配收敛）。
4. **预览与编辑**：直接编辑内容（编辑项自动锁定为 critical，不会被自动裁剪），可单条重新生成。
5. **导出**：确认 AI 生成内容清单后导出 HTML，浏览器直接打印（Ctrl+P）为 PDF。

## 目录结构

```
JL-Agent/
├── README.md
├── requirements.txt
├── config.example.json        # 配置样例
├── .env.example               # 密钥样例
├── app/
│   ├── main.py                # FastAPI 入口
│   ├── config.py              # 配置加载与脱敏
│   ├── schemas/               # 数据模型（= 数据契约）
│   ├── api/                   # 路由层（resume/upload/generate/skills/adjust/export/...）
│   ├── core/                  # 校验 / 规则加载 / LLM provider / 错误码
│   ├── engine/                # 生成引擎（分析/事实表/DAG/预算/缓存/审阅）
│   ├── adapter/               # 动态适配决策与应用
│   ├── search/                # 联网搜索与深度取材
│   └── assets/                # 模板装配辅助
├── rules/                     # 规则文件（行业模板库 / 技能 / 项目 / JD 规则）
├── templates/                 # 简历模板（一页版 / 两页版）
└── frontend/                  # 前端（index.html + css + js）
```

## 配置说明

| 配置项 | 说明 | 默认 |
|---|---|---|
| `LLM_API_KEY` / `.env` | LLM API Key（脱敏展示，不入日志） | — |
| `config.json > provider.baseUrl` | OpenAI 兼容接口地址 | DeepSeek 官方 |
| `config.json > provider.model` | 模型名（UI 不展示） | deepseek-v4-flash |
| `config.json > search` | 联网搜索 API（Tavily/Serper，可选） | 关闭 |
| `config.json > paths` | 数据/规则/模板路径 | 仓库内相对路径 |

## 规则文件

- `rules/industries/*.json`：行业模板库（互联网/金融/快消/制造/游戏）。
- `rules/skills/rules.json`：技能相关性判定。
- `rules/projects/mapping.json`：JD 方向 ↔ 项目类型映射。
- `rules/jobs/rules.json`：主题一致性判定。
- 全部规则文件经 jsonschema 校验后加载，变更后由 `GET /api/health` 暴露版本号。

## 接口与文档

- 工程契约（数据契约 / API 契约 / 引擎设计 / 验收清单）：[docs/contract.md](docs/contract.md)
- 健康检查：`GET /api/health`（返回规则版本列表）

### 主要接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/resume` | 创建/整存简历（无 id 则新建） |
| GET/PUT/DELETE | `/api/resume/{id}` | 简历详情 / 更新 / 删除 |
| POST | `/api/upload/photo` | 照片上传（JPG/PNG、200~4000px、≤5MB） |
| POST | `/api/skills/validate` | 技能相关性三档判定（pass/weak/block，关键词兜底） |
| POST | `/api/skills/extend` | 基于 JD 推荐补充技能 |
| GET | `/api/search/mode` | 搜索能力检测（apiReady/deepAvailable/missing） |
| POST | `/api/generate` | 提交关卡（JD 分析 + 技能相关性 + 主题一致性 + 数量约束）→ 创建任务 |
| GET | `/api/task/{id}` | 任务快照（state/progress/stage） |
| POST | `/api/task/{id}/cancel` | 取消任务 |
| GET | `/api/task/{id}/events` | SSE 事件流 |

### 测试

```powershell
# 启动服务后运行冒烟/回归测试
.venv\Scripts\python.exe tests\smoke_api.py      # API 冒烟（无 LLM Key 时验证降级错误码）
.venv\Scripts\python.exe tests\logic_check.py    # 核心逻辑回归（判定边界/容错，无 LLM 依赖）
```

## License

内部使用 / 待定（详见仓库）。
