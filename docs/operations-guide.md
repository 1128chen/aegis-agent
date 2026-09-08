# AegisAgent · 完整操作手册

> 面向「AI/LLM 应用代码安全评审 + 自进化数据飞轮」平台的 Web / API / CLI / 脚本
> 全功能操作指南。本地开发默认:Python 3.11(venv 实际 3.10)、SQLite、免登录。

---

## 0. 功能总览

| 层 | 能力 | 入口 |
|---|---|---|
| 审查 | 手动 Diff 审查、GitHub PR Webhook、同步/异步、4 角色 Agent | Web「发起审查」/ `POST /v1/reviews` |
| 工程 | RBAC 登录、租户/仓库隔离、任务中心、报告、自动修复、取消/续跑、审计/告警、灰度发布 | Web 各页 / `api.py` |
| 评测/进化 | 失败案例回流 → 候选提示词 → Validation/Holdout 回放 → 版本激活/回滚 | Web「演进实验室」/ `/v1/evolution*` |
| Skill 进化 | SKILL.md 包、候选评测门禁、版本链激活 | `/v1/skills*`、`/v1/skill-evolution*` |
| 垂直场景 | AI 应用安全风险分类 + `ai-app-security` Skill + `ai-*` 确定性扫描器 | Skills 页 / `aegis/ai_risks.py` |
| 数据飞轮 | 轨迹采集、自动/人工标注、流水线、LoRA 训练、Serving、适配器灰度回灌 | Web「标注台/飞轮控制台」/ `/v1/flywheel*` |

---

## 1. 启动与模型配置

### 1.1 选择 provider(影响能跑哪些功能)

| provider | 需要 | 能跑 |
|---|---|---|
| `local` | 无 | 只读页面、飞轮、Skill/扫描器 demo;**Agent 审查不可用** |
| `deepseek` | DeepSeek key | Agent 审查、进化、轨迹回流等全部功能 |
| `openrouter-deepseek-free` / `openrouter-free` | OpenRouter key | 同上(免费档限速) |
| `custom` | 任意 OpenAI 兼容端点 | 同上;也可指向本地已训模型做权重回灌 |

根目录 `.env` 配置(**改后必须重启**):

```env
AEGIS_LLM_PROVIDER=deepseek
AEGIS_DEEPSEEK_API_KEY=sk-xxxx
# 可选:自定义 OpenAI 兼容
# AEGIS_LLM_PROVIDER=custom
# AEGIS_LLM_BASE_URL=http://127.0.0.1:8130/v1
# AEGIS_LLM_API_KEY=flywheel
# AEGIS_LLM_MODEL=aegis-preflight-v1
```

### 1.2 启动服务

```bash
python -m aegis            # 默认 127.0.0.1:8080
AEGIS_PORT=8090 python -m aegis   # 换端口
```

浏览器打开 `http://127.0.0.1:8080`。

### 1.3 启用登录(可选,公网/多用户时)

```env
AEGIS_AUTH_REQUIRED=true
AEGIS_AUTH_SECRET=<随机≥32字节>
AEGIS_BOOTSTRAP_ADMIN_USERNAME=admin
AEGIS_BOOTSTRAP_ADMIN_PASSWORD=<≥10字符>
```

登录后拿 Bearer Token,后续 API 都带 `Authorization: Bearer <token>`。

---

## 2. Web 控制台逐视图操作

### 2.1 运行总览(#overview)
- 顶部 6 张统计卡:总任务 / 已完成 / 失败 / 成功率 / 待处理反馈 / 活跃 Skill 版本。
- 「实际执行链」显示 Tool/Scanner → 4-role LLM Agents → Gate 的编排状态与真实模型/provider。
- 右侧「最近任务」可点进任务详情。
- 顶部按钮:刷新 / 退出登录(需登录时)。

### 2.2 发起审查(#review)
表单字段:
- **仓库地址**:展示用标签,任意非空 ≤250 字符,建议 `owner/repo`,如 `demo/ai-agent`(详见仓库「授权规则」,0 条授权 = 全放行)。**PR 编号留空** = 手动审查。
- **运行模式**:目前固定 `agentic`(Lead/Security/Correctness- Reliability/Critic 四个真实 LLM 角色)。
- **Unified Diff**:粘贴 `--- a/… / +++ b/… / @@ … @@ / +/-` 格式文本(下方「推荐体验样例」可复制)。
- **异步队列**:勾选 = 提交后立刻返回任务号(`202`),不勾选 = 同步等待完整报告。

提交后可看到:右下策略说明、风险等级 `risk_level`、AI 风险态势(`risk_class` 统计)、执行事实(模型调用数/Token/成本/延迟)。成本来自 `AEGIS_LLM_INPUT/OUTPUT_COST_PER_MILLION`。

### 2.3 任务中心(#tasks)
- 左侧任务列表;点任一项右侧显示完整 JSON(**input/report/trace/collaboration/execution/gates/rejected_findings/context_management**)。
- 完成任务且有 `pull_request` 时出现「创建修复分支」→ 走 VerifiedPatchFixer(自动修复到 `aegis/fix-*` 新分支,draft PR)。
- 下方「审查反馈」:
  - 类型:误报 / 漏报 / 坏修复(**漏报可补 rule_id/path/line**);
  - 关联某条结论可选;
  - 提交后进入失败案例库,供「演进实验室」消费。

### 2.4 Skills(#skills)
- 「组件与 Skill 能力」:LLM Review Agent 运行时状态 + provider/model。
- 动态 Skills 卡片:磁盘 `skills/` 目录发现(含新增 **ai-app-security**)+ DB 版本化 artifact。
- 右上「重新扫描 Skills」→ `POST /v1/skills/reload`。

### 2.5 标注台(#labels)—— 人工 ground truth(数据飞轮)
- 左列:已完成(SUCCESS)任务。
- 选择任务 → 右侧逐条结论:rule_id / severity / **AI risk_class** / title / explanation / evidence。
- 点「采纳」或「误报」→ 写 `finding_accept` 人工标签(`POST /v1/tasks/{id}/labels`);auto_rule 自动标注同屏展示。
- 意义:采纳=真阳性、误报=负样本,驱动 classify 负采样与 preference 偏好对。

### 2.6 飞轮控制台(#flywheel)—— 自优化闭环
6 张卡:采集轨迹(内容级 `llm_traces`)、标注(覆盖任务数)、**classify / instruct / preference** 三类样本数、部署模式(api-only/distill-role/full-replace)+ 活跃 adapter。
- 按钮「运行数据流水线」→ `POST /v1/flywheel/pipeline/run`:拉轨迹 → 清洗/去重 → 格式化 → 导出 `data/sft/manifest-<hash>.jsonl` → 幂等(同内容同版本)。
- 按钮「LoRA 后训练(preflight/classify)」→ `POST /v1/flywheel/train`:真实 CPU 训练,数分钟,输出 run_id / adapter_dir / loss。
- 适配器区:每个 layer(`preflight`/`reviewer`)的版本链(status/score/serving_model_id)+「激活」按钮 → `POST /v1/adapters/{layer}/versions/{v}/activate`(写库后自动重建运行时)。
- 下方显示 serve_url 与调度器最近一次尝试。

### 2.7 演进实验室(#evolution)
- 顶部「从反馈生成候选」→ `POST /v1/evolution/auto`:读未解决失败案例 → 生成候选提示词 → 自动回放评测,激活或拒绝。
- 「评测就绪状态」:validation/holdout 集大小、模型与门禁就绪度。
- 表单「提交候选提示词」:手填 prompt → `POST /v1/evolution/propose` 做新老版本回放对比。
- 下方:失败案例 + 最近评测记录(候选 vs 基线 score)。

---

## 3. REST API(全)

以下假定 `$TOKEN` 已定义(免登录时留空即可)。

```bash
# 登录(仅 auth 开启时)
curl -s -X POST http://127.0.0.1:8080/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"...","tenant_id":""}'
```

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /health | 健康检查/模型/队列/运行模式 | - |
| GET | /metrics | Prometheus 指标文本 | read |
| GET | /api/dashboard | 总览统计+最近任务+LLM | read |
| GET | /api/tasks?limit=N | 任务列表(摘要列) | read |
| GET | /api/skills | scanner + agent skills | read |
| GET | /api/failures | 失败案例 | audit |
| GET | /api/audit | 审计日志 | audit |
| GET | /api/alerts | 告警 | read |
| GET | /api/deployments/llm-review | 当前发布配置 | read |
| GET | /api/queue/dead-letters | 死信队列 | manage |
| POST | /v1/reviews[?async=true] | 创建审查(同步 201 / 异步 202) | review |
| GET | /v1/tasks/{id} | 任务详情 | read |
| GET | /v1/tasks/{id}/report | Markdown 报告 | read |
| GET | /v1/tasks/{id}/feedback | 该任务反馈历史 | review |
| POST | /v1/tasks/{id}/feedback | 记录 误报/漏报/坏修复 | review |
| GET | /v1/tasks/{id}/labels | 标注历史(auto+human) | review |
| POST | /v1/tasks/{id}/labels | 写 finding_accept 等标注 | review |
| POST | /v1/tasks/{id}/fix | 创建修复分支 | fix |
| POST | /v1/tasks/{id}/cancel | 请求取消 | review |
| POST | /v1/tasks/{id}/resume | 从 checkpoint 续跑 | review |
| GET | /v1/evaluation/cases | 评测样本(validation;holdout 不暴露) | read |
| POST | /v1/evaluation/cases | 新增评测样本 | manage |
| POST | /v1/evolution/auto | 失败案例→候选→评测 | manage |
| POST | /v1/evolution/propose | 评测指定候选 prompt | manage |
| GET | /v1/evolution/runs | 新旧版本评测记录 | read |
| GET | /v1/evolution/status | 评测门禁就绪状态 | read |
| POST | /v1/skills/reload | 重新扫描磁盘 Skills | manage |
| GET/POST | /v1/skill-evolution/{name}/… | SKILL.md artifact 版本/评测/激活 | manage |
| GET | /v1/flywheel/status | 飞轮计数/池/适配器/调度 | read |
| GET | /v1/flywheel/datasets | 各 kind 选中样本数 | read |
| GET | /v1/flywheel/training-runs | 训练记录 | read |
| POST | /v1/flywheel/pipeline/run | 跑数据流水线 | manage |
| POST | /v1/flywheel/train | LoRA 训练 | manage |
| GET | /v1/adapters/{layer} | 适配器版本链 | read |
| POST | /v1/adapters/{layer}/versions/{v}/activate | 激活/回滚权重版本 | manage |
| POST | /v1/deployments/llm-review | 配置灰度/影子发布 | manage |
| POST | /v1/queue/dead-letters/replay | 重放死信 | manage |
| POST | /webhooks/github | GitHub PR webhook(HMAC 校验) | 签名 |

示例:异步提交 + 轮询 + 标注意见

```bash
REPO='demo/ai-agent'
curl -s -X POST "http://127.0.0.1:8080/v1/reviews?async=true" -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  --data-binary @- <<'JSON'
{"repository":"demo/ai-agent","mode":"agentic","diff":"--- a/agent.py\n+++ b/agent.py\n@@ -1 +1,3 @@\n-old\n+def run(user_input):\n+    system_prompt = \"You are a coding assistant. Rule: \" + request.args[\"rule\"]\n+    return eval(completion.choices[0].message.content)\n"}
JSON
# 返回 {"task_id":"...","state":"PENDING",...}
curl -s http://127.0.0.1:8080/v1/tasks/$TASK_ID | python -m json.tool
curl -s http://127.0.0.1:8080/v1/tasks/$TASK_ID/report   # Markdown 报告
```

---

## 4. 飞轮 CLI(权重侧自动化)

```bash
python -m aegis.flywheel status                                  # 就绪状态/计数
python -m aegis.flywheel pipeline                                # 跑数据流水线
python -m aegis.flywheel train --layer preflight --kind classify # LoRA 训练
python -m aegis.flywheel gate --layer preflight --version 2      # 非回归门禁+激活
python -m aegis.flywheel activate --layer preflight --version 2  # 直接激活
python -m aegis.flywheel serve --adapter-dir artifacts/adapters/<run_id> --port 8130
```

`distill-role`(Lead 预筛走小模型)或 `full-replace`(全本地)需设 `AEGIS_MODEL_DEPLOYMENT` 并重启。

---

## 5. 脚本 / 实验 / 测试

| 脚本 | 作用 | 是否需要模型 |
|---|---|---|
| present_demo.py | 四层一键演示(场景→语料→飞轮→权重) | 否 |
| flywheel_demo.py | seed→标注→流水线→(--train) | 否(训练可选) |
| ai_app_benchmark.py | AI 冷路径扫描器 P/R/F1 | 否 |
| run_real_lora.py | 真实 CPU LoRA 训练(下载 Qwen) | 否(联网) |
| generate_controlled_corpora.py | 重新生成两份受控评测语料 | 否 |
| run_controlled_experiments.py | 受控评测(Accuracy/Skill 消融) | 否 |
| run_accuracy_experiment.py | AccuracyExperimentSuite | 否 |
| run_skill_evolution_experiment.py | Skill 三臂消融 | 否 |
| run_prompt_evolution_proof.py | 离线 prompt 进化 proof(报告 json/md) | 否 |
| run_agentic_evaluation.py / run_real_pr_benchmark.py | 真实 PR 评测/基准(需 ≥300 公共 PR + 模型) | 是 |
| import_github_pr_dataset.py | 导入 GitHub PR 数据集 | 是(GH) |

```bash
python scripts/present_demo.py
python scripts/ai_app_benchmark.py
python -m unittest discover -s tests        # 104 用例全绿
```

---

## 6. 推荐完整体验路线

**阶段 A · 离线认识(0 key)**
1. 启动服务 → Web 各视图浏览;
2. 「发起审查」local 模式看 Skills/标注台/飞轮(用 demo 库:见「仓库地址」/「启动用 demo 库」);
3. 跑 `present_demo.py`、`benchmark.py`、CLI `status`;
4. 起已训 adapter 的 `serve` 在 8130 做一次离线推理。

**阶段 B · 真实闭环(需 DeepSeek key)**
1. `.env` 配 deepseek → 重启;
2. 提交 2–3 个 AI 应用 diff → 任务中心看 4 角色协作、AI risk posture、成本;
3. 标注台逐条采纳/误报 → `llm_traces`+labels 累积;
4. 飞轮控制台:运行流水线(样本数上升)→ LoRA 后训练(看 loss)→ gate → 激活 → 切 `distill-role` 重启 → 再跑同一 diff,看 Lead 走本地小模型、Token 下降。

**阶段 C · 演进实验室**
1. 对某条结论提交「误报/漏报」→ 「演进实验室」点「从反馈生成候选」→ 看候选 vs 基线回放、是否激活/回滚;
2. Skills 页「重新扫描」观察 ai-app-security。

**阶段 D · GitHub 集成(可选,需公网)**
`cloudflared tunnel --url http://127.0.0.1:8080` + 仓库 Settings→Webhooks 填 payload/secret,只订阅 pull_request;新 PR 自动入任务中心,`AEGIS_AUTO_POST_REVIEW=true` 时回写评论,`fix` 可建自动修复分支。PAT 权限详见 README「GitHub Webhook」。

---

## 7. 常见问题

- **401 / 模型未配置**:`.env` 未生效或没重启;local 模式不能跑 Agent 审查,只能查状态/演示。
- **repository is not authorized**:库里已有授权记录 → 白名单外被拒;给该租户补 grant(见「仓库地址」),或改用已授权名。
- **task 打不开**:任务 id 若是字母数字自定义(如 `lora-seed-…`),部分 base 路由只认 uuid-hex;体验请用默认 uuid 任务或用标注台入口。
- **训练报 torch 错误**:装 `torch==2.5.1+cpu`(本机 2.14 wheel DLL 失败已规避),`pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu`。
- **中文乱码**:Windows GBK 终端显示问题;用浏览器或 UTF-8 终端。
