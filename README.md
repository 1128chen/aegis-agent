# AegisAgent · AI 应用代码安全的自进化审查 Agent

> 一套面向 **LLM/AI 应用代码**的多智能体安全审查平台,并且自带**数据飞轮**:
> 每次真实审查的完整轨迹都会被采集、标注、清洗成训练数据,在本地用 LoRA 微调开源小模型,
> 再把权重经版本门禁灰度回灌到运行时——让"代码审查"随使用不断自我进化。
>
> Python 实现,单机即可完整跑通 **真实审查 → 标注 → 训练 → 权重回灌** 全流程。

---

## 目录

- [功能特性](#功能特性)
- [架构总览](#架构总览)
- [快速开始](#快速开始)
- [核心概念](#核心概念)
- [Web 控制台使用](#web-控制台使用)
- [REST API](#rest-api)
- [自进化数据飞轮(核心)](#自进化数据飞轮核心)
- [评测与实验](#评测与实验)
- [配置参考](#配置参考)
- [目录结构](#目录结构)
- [隐私与安全须知](#隐私与安全须知)
- [常见问题](#常见问题)
- [License](#license)

---

## 功能特性

- **多智能体审查**:Lead 委派 → Security / Reliability 按最小特权并行取证 → Critic 盲审 → Lead 综合,支持断点续跑、步数/时间预算、死信重试。
- **垂直场景**:建模 **6 类 AI/LLM 应用特有风险**(prompt 注入、Agent 工具越权、RAG/日志数据泄露、循环失控、模型输出误用、通用安全基线)。
- **内容级轨迹**:每个 LLM 步骤的上下文 / 回复 / 工具结果逐条落库,可按「角色@步骤」回放与归因;含"结论必须可溯源"的一致性校验。
- **自进化(双通道)**:提示词/Skill 层 + **权重层(LoRA 后训练)**;适配器版本注册、非回归门禁、灰度回灌(`distill-role` / `full-replace` / `api-only`)。
- **数据飞轮**:自动/人工标注、任务质量筛选(curation)、分类/偏好/**episode(整段轨迹)**数据集、内容哈希幂等导出。
- **记忆与治理**:跨会话全文检索 + 仓库评审画像自动注入;经验自动沉淀为 Skill;周期治理报告。
- **后端工程**:REST(20+ 接口)、登录/RBAC 与租户隔离、SQLite/PostgreSQL 双存储、Redis/内存队列、GitHub Webhook、Prometheus 指标、Web 控制台与 CLI。

---

## 架构总览

```text
                        ┌────────────── Web 控制台(7+ 视图)──────────────┐
                        │  发起审查 / 任务中心 / 标注台 / 飞轮 / 记忆 / 治理 │
                        └───────────────▲────────────────────────────┘
 HTTP / GitHub Webhook                  │ REST(JSON + Bearer / HMAC)
        │                               ▼
        └──► ReviewService ──► TaskStore(SQLite / PostgreSQL)
                     │
                     ▼
        ReviewHarness(自研有界 Agent Runtime:checkpoint / 预算 / 轨迹 / 续跑)
                     ├─ Lead 委派与综合
                     ├─ Security / Reliability Worker(工具注册表 + 最小特权)
                     ├─ Critic 盲审
                     └─ 四重门禁(格式/证据/置信度/发布)

        ┌────────────────────── Flywheel 数据飞轮 ──────────────────────┐
        │ 轨迹采集(llm_traces)→ 标注(auto/human)→ 流水线(清洗/去重/格式化) │
        │ → 数据集(分类/偏好/episode)→ LoRA 本地训练 → Serving(OpenAI 兼容)│
        │ → 适配器版本注册/门禁/灰度回灌 ───────────────────────────────►│
        └──────────────────────────────────────────────────────────────┘
```

数据流(一句话):`线上审查 → 轨迹 → 标注 → 数据集 → LoRA 微调 → 门禁 → 权重回灌 → 再审查(循环)`

---

## 快速开始

### 环境要求
- Python **3.10+**(推荐 3.11)
- (可选)需要**真实 LLM** 才能跑 4 角色智能体审查;本地只体验数据飞轮 / 评测 / Web 时可用 `local` 模式

### 安装

```bash
git clone <your-repo-url> && cd aegis
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
# source venv/bin/activate
python -m pip install -r requirements.txt
```

### 配置模型(可选;不配则只能体验离线能力)

新建项目根目录 **`.env`**(已被 `.gitignore` 忽略,请勿提交):

```env
AEGIS_LLM_PROVIDER=deepseek
AEGIS_DEEPSEEK_API_KEY=sk-你的Key
```

也支持 `openrouter-*` 或任意 OpenAI 兼容端点(`AEGIS_LLM_PROVIDER=custom` + `AEGIS_LLM_BASE_URL/API_KEY/MODEL`)。

### 启动

```bash
python -m aegis
```

浏览器打开 http://127.0.0.1:8080

健康检查:

```bash
curl http://127.0.0.1:8080/health
```

> 想先离线看数据飞轮与评测,请把 `.env` 里 provider 设为 `local` 或直接不配置。

---

## 核心概念

| 概念 | 说明 |
|---|---|
| `agentic` 运行模式 | 4 个真实 LLM 角色协作审查(需配置模型) |
| **risk_class** | AI 应用风险分类:prompt-injection / tool-escape / data-leak-rag / agent-loop-abuse / llm-misuse / classic-sec |
| Skill | 纯声明式 `SKILL.md`(含内置 `ai-app-security`),可按任务选择、热加载、版本化 |
| `model_adapters` | LoRA 权重版本注册表(层:preflight / reviewer),可激活/回滚 |
| 部署模式 | `api-only`(全 API 模型)· `distill-role`(微调小模型做 Lead 预筛)· `full-replace`(全本地) |
| `llm_traces` | 内容级轨迹:每步 prompt / 回复 / 工具结果 |
| dataset manifest | 数据流水线产物,以内容哈希作为版本,幂等可复现 |

---

## Web 控制台使用

- **运行总览**:任务/成功率统计、执行链、模型状态
- **发起审查**:粘贴仓库与 Diff(仓库名只是标签,允许 0 授权时全放行),可异步
- **任务中心**:查看完整报告 / 轨迹 / 门禁;对结论提交"误报/漏报/坏修复"
- **Skills**:浏览 `ai-app-security` 等技能,动态重载
- **标注台**:对结论逐条「采纳 / 误报」,为数据飞轮提供人工 ground truth
- **飞轮控制台**:查看轨迹/标注/三类+episode 样本数、运行数据流水线、LoRA 训练、适配器激活、轨迹回放守卫
- **记忆库**:跨会话全文检索历史评审 + 仓库画像
- **治理**:对仓库生成周期治理报告并查看历史
- **演进实验室**:失败反馈 → 候选 → Validation/Holdout 回放 → 激活/回滚;经验自动沉淀为 Skill

---

## REST API

部分接口示例(免登录本地模式;开 `AEGIS_AUTH_REQUIRED=true` 后需带 `Authorization: Bearer <token>`):

```bash
# 创建异步审查
curl -X POST "http://127.0.0.1:8080/v1/reviews?async=true" \
  -H 'Content-Type: application/json' \
  -d '{"repository":"demo/ai-agent","diff":"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(user_input)\n"}'

# 任务与报告
curl http://127.0.0.1:8080/v1/tasks/<task_id>
curl http://127.0.0.1:8080/v1/tasks/<task_id>/report

# 数据飞轮 / 评测 / 记忆 / 治理(节选)
curl -X POST http://127.0.0.1:8080/v1/flywheel/pipeline/run -H 'Content-Type: application/json' -d '{}'
curl http://127.0.0.1:8080/v1/flywheel/status
curl http://127.0.0.1:8080/v1/flywheel/replay
curl "http://127.0.0.1:8080/v1/memory/search?repository=demo/ai-agent&q=eval"
curl "http://127.0.0.1:8080/v1/memory/digest?repository=demo/ai-agent"
curl http://127.0.0.1:8080/v1/experience/suggestions?status=pending
curl -X POST http://127.0.0.1:8080/v1/governance/report -H 'Content-Type: application/json' -d '{"repository":"demo/ai-agent"}'
curl http://127.0.0.1:8080/metrics                 # Prometheus
```

完整接口清单见代码 `aegis/api.py`。

---

## 自进化数据飞轮(核心)

体验完整闭环(需已配置模型,并有 API 余额):

1. 在「发起审查」提交一份 **AI 应用** Diff(含 `system_prompt = ... + request...`、`eval(model_output)` 这类行的样例最直观);
2. 在「标注台」对结论点「采纳 / 误报」;
3. 在「飞轮控制台」运行数据流水线 → 观察 classify / episode 等样本数增长与 curation 打分;
4. 运行训练并激活(推荐命令行,CPU 即可):

```bash
# 可选:模型已在本地缓存时离线运行,避免联网
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1   # Windows: set ...

python -m aegis.flywheel train --layer preflight --kind classify
python -m aegis.flywheel gate --layer preflight --version 1
python -m aegis.flywheel activate --layer preflight --version 1
```

5. (可选)权重回灌:用 OpenAI 兼容端点把微调模型暴露给运行时

```bash
python -m aegis.flywheel serve --adapter-dir artifacts/adapters/<run_id> --port 8130
# .env 设置 AEGIS_MODEL_DEPLOYMENT=distill-role 后重启服务
```

---

## 评测与实验

内置两类受控语料(仓库隔离的 train/validation/holdout)与一批可复现实验:

```bash
# 单元测试
python -m unittest discover -s tests

# 演示与基准
python scripts/present_demo.py            # 四层一键演示(离线)
python scripts/ai_app_benchmark.py        # AI 冷路径扫描器 P/R/F1
python scripts/flywheel_demo.py           # 数据飞轮端到端演示
python scripts/generate_controlled_corpora.py   # 重新生成受控评测语料

# 受控实验 / 离线 proof
python scripts/run_prompt_evolution_proof.py
python scripts/run_controlled_experiments.py
python scripts/run_skill_evolution_experiment.py
```

> 真实指标口径:AI 垂直语料(10 例)上冷路径扫描器 Precision 1.0 / Recall 0.83 / F1 0.91,干净代码零误报;
> 本地 CPU + LoRA(Qwen2.5-Coder-0.5B)微调样例 val_loss ≈ 0.52。小样本仅证明机制链路,真实质量需积累运行数据。

---

## 配置参考

主要环境变量(全部见 [.env.example](.env.example)):

| 变量 | 说明 |
|---|---|
| `AEGIS_LLM_PROVIDER` | `local` / `deepseek` / `openrouter-*` / `custom` |
| `AEGIS_DEEPSEEK_API_KEY` / `AEGIS_OPENROUTER_API_KEY` | 对应密钥 |
| `AEGIS_LLM_BASE_URL / API_KEY / MODEL` | 自定义 OpenAI 兼容端点 |
| `AEGIS_MODEL_DEPLOYMENT` | `api-only` / `distill-role` / `full-replace` |
| `AEGIS_FLYWHEEL_CAPTURE` | 是否采集内容级轨迹(默认开) |
| `AEGIS_FLYWHEEL_SERVE_URL` | 本地微调模型服务地址 |
| `AEGIS_FLYWHEEL_DATA_DIR` / `ARTIFACTS_DIR` | 数据集 / 训练产物目录 |
| `AEGIS_GOVERNANCE_REPOSITORIES` | 周期治理扫描的仓库列表 |
| `AEGIS_DATABASE_URL` / `REDIS_URL` | 生产模式:PostgreSQL / Redis |

模型切换、Skill、GitHub Webhook 等更多配置见 [.env.example](.env.example) 与 `aegis/config.py`。

---

## 目录结构

```text
aegis/                 # 主包
  api.py / service.py     # HTTP 服务与业务编排
  agentic_core.py         # 4 角色多智能体运行时(有界 Loop / 轨迹 / 门禁)
  store.py                # SQLite(Postgres 镜像)与全部表
  ai_risks.py             # AI 应用风险分类 + 冷路径扫描器
  experience.py           # 经验自动沉淀(审查 → Skill 学习)
  history.py              # 跨会话全文检索 + 仓库画像
  governance.py           # 周期治理扫描
  flywheel/               # 数据飞轮:datasets/pipeline/labels/curation/replay/
                          #          train/serve/registry/scheduler/__main__
skills/                   # Agent Skill 包(含 ai-app-security)
evaluation_data/          # 受控评测语料(合成,可重新生成)
web/                      # Web 控制台(原生 HTML/JS/CSS)
scripts/                  # 演示 / 实验 / 语料生成脚本
tests/                    # 单元测试(unittest,122 项)
docs/operations-guide.md  # 更完整的操作手册(cmd/浏览器导向)
```

---

## 隐私与安全须知

- **密钥管理**:API Key 只放 `.env`,该文件已被 `.gitignore` 忽略,严禁提交;公开仓库请勿出现任何真实密钥。
- **敏感输出**:内容级轨迹会包含被审查代码;生产使用请按租户隔离存储,并按需清理/加密。
- **仓库授权**:默认空授权 = 放行任意仓库名;一旦添加 `repository_grants` 则只放行白名单,避免在公网服务被滥用。
- **公网暴露**:需要把管理台暴露到公网时,请开启 `AEGIS_AUTH_REQUIRED=true` 并设置强密码;长期建议仅公开 `/health` 与 `/webhooks/github`。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| 审查报 402 Insufficient Balance | DeepSeek 余额不足,请充值后再试 |
| `repository is not authorized` | 该租户已有授权记录 → 把仓库加入 `repository_grants` |
| 训练/服务加载卡在网络 | 已在本地缓存时设 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 走本地 |
| Windows CPU 跑 torch 报 DLL 错误 | 使用 CPU 版并锁定稳定版本(见 `requirements`) |
| 想彻底重来一遍 | 删除 `*.db`、`data/sft`、`artifacts` 后重启服务 |

---

## License


**致谢 / 设计参考**:多智能体编排与门禁基座、自进化数据飞轮、以及借鉴自学习 Agent 的"经验沉淀、轨迹即训练数据、跨会话记忆"等设计思路。

---

> 仓库由个人维护。欢迎 Issue / PR 交流。
