# Harvey Labs 使用教程（中文版）

本教程带你从零开始走通 Harvey Labs 的完整流程：配环境、跑 Agent、看结果、打分、对比报告。

---

## 一、Harvey Labs 是什么

Harvey Labs 是一个**法律 AI Agent 基准测试框架**，用于评估大语言模型在真实法律任务中的表现。

核心设计理念：
- **文件系统驱动**：无数据库、无 Web 服务，所有数据以文件形式存储
- **沙盒隔离**：Agent 在 Podman 容器中执行，无网络访问
- **LLM-as-Judge**：由 LLM 作为评审，逐条评估 Agent 产出
- **All-Pass 评分**：只有所有评分标准全部通过，任务才算成功（score=1.0）

---

## 二、系统架构概览

```
tasks/<practice-area>/<task>/task.json + documents/
        │
        ▼
harness.run（Agent 运行）
        │
        ├── Model Adapter → Provider API（Claude/GPT/Gemini/...）
        ├── Sandbox（Podman, network=none）
        └── Agent Loop: chat → tool execution → transcript
        │
        ▼
results/<run-id>/（config.json + metrics.json + transcript.jsonl + output/）
        │
        ▼
evaluation.run_eval（LLM-as-Judge 评分）
        │
        ▼
scores.json + report.html
```

---

## 三、环境准备

### 3.1 系统要求

- Linux（推荐 Ubuntu 22.04+）或 macOS
- Python 3.12+
- Podman（容器运行时）
- Pandoc（文档格式转换）

### 3.2 安装

```bash
# 克隆仓库
git clone https://github.com/harveyai/harvey-labs.git
cd harvey-labs

# 一键安装依赖
./scripts/setup.sh
```

`setup.sh` 会自动安装 `uv`（Python 包管理器）、`podman`、`pandoc`，并拉取沙盒镜像。

### 3.3 配置 API Key

在项目根目录创建 `.env` 文件：

```bash
# 必需 — Anthropic Key 用于 LLM Judge 评分
ANTHROPIC_API_KEY=sk-ant-api03-...

# 可选 — 仅当你需要跑对应模型时配置
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
DEEPSEEK_API_KEY=sk-...
MISTRAL_API_KEY=...
FIREWORKS_API_KEY=...
```

> `.env` 已在 `.gitignore` 中，不会被提交。

---

## 四、理解任务结构

### 4.1 目录结构

```
tasks/
  corporate-ma/                          # 业务领域（Practice Area）
    review-data-room-red-flag-review/    # 具体任务
      task.json                          # 任务定义
      documents/                         # 输入文档集
        contract-001.docx
        financial-statements.xlsx
        due-diligence-report.pdf
        ...
```

### 4.2 task.json 核心字段

| 字段 | 说明 |
|---|---|
| `title` | 任务标题（人类可读） |
| `instructions` | 给 Agent 的任务指令 |
| `work_type` | 工作类型：analyze / draft / review / extract / research |
| `deliverables` | 期望 Agent 产出的文件名 |
| `criteria[]` | 评分标准列表（rubric） |

### 4.3 criteria 评分标准格式

```json
{
  "id": "C-001",
  "title": "识别出合同中的控制权变更条款",
  "deliverables": ["red-flag-memorandum.docx"],
  "match_criteria": "PASS if the memo identifies... FAIL if..."
}
```

### 4.4 查看可用任务

```bash
# 列出全部任务
uv run python -m utils.list_tasks

# 按业务领域筛选
uv run python -m utils.list_tasks --area corporate-ma

# 按工作类型筛选
uv run python -m utils.list_tasks --work-type draft

# 查看单个任务详情
uv run python -m utils.describe_task corporate-ma/review-data-room-red-flag-review
```

---

## 五、运行 Agent

### 5.1 基本运行命令

```bash
uv run python -m harness.run \
  --model anthropic/claude-sonnet-4-6 \
  --task corporate-ma/review-data-room-red-flag-review \
  --max-turns 200
```

### 5.2 运行流程

1. 加载 `task.json` 和 `documents/`
2. 构建 system prompt（harness 框架说明 + skill 手册 + 任务指令）
3. 创建对应 Provider 的 Model Adapter
4. 启动 Podman 沙盒容器
5. 向 Agent 暴露 6 个工具：`bash` / `read` / `write` / `edit` / `glob` / `grep`
6. 进入 Agent Loop：Model 调用 → 执行工具 → 返回结果 → 继续
7. 当 Agent 停止调用工具或达到 max-turns 时结束
8. 保存所有产物到 `results/`

### 5.3 支持的模型

| Provider | 模型示例 | 前缀 |
|---|---|---|
| Anthropic | `anthropic/claude-sonnet-4-6` | `claude*` |
| OpenAI | `openai/gpt-5.4` | `gpt*`, `o1*`, `o3*`, `o4*` |
| Google | `google/gemini-3.1-pro-preview` | `gemini*` |
| Mistral | `mistral/mistral-medium-3.5` | `mistral*` |
| DeepSeek | `deepseek/DeepSeek-V3-2` | `deepseek*` |
| Fireworks | `kimi-k2p6` / `glm-5p2` | `kimi*`, `glm*`, `nemotron*` |

### 5.4 常用参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--model` | 必填 | 模型标识符 |
| `--task` | 必填 | 任务 ID |
| `--max-turns` | 200 | Agent 最大交互轮次 |
| `--temperature` | 0.0 | 采样温度 |
| `--shell-timeout` | 60 | bash 命令超时（秒） |
| `--reasoning-effort` | 无 | 推理深度（low/medium/high/max） |
| `--skills` | 全部 | 加载哪些 skill（不传参=禁用全部） |

### 5.5 运行输出示例

```
Loading task: corporate-ma/review-data-room-red-flag-review
Sandbox: podman (documents=/path/to/documents)
Creating adapter for: anthropic/claude-sonnet-4-6
Starting agent loop (max 200 turns)...
Tools: 6 (bash, read, write, edit, glob, grep)

============================================================
Run complete: corporate-ma/review-data-room-red-flag-review/claude-sonnet-4-6/20260627-142301
  Model:          anthropic/claude-sonnet-4-6
  Turns:          24
  Input tokens:   210,450
  Output tokens:  18,930
  Wall clock:     180.4s
  Docs read:      31/60
  Finished:       True

Results saved to: results/corporate-ma/review-data-room-red-flag-review/claude-sonnet-4-6/20260627-142301
```

---

## 六、检查运行结果

每次运行都会在 `results/<run-id>/` 下生成以下文件：

| 文件 | 内容 |
|---|---|
| `config.json` | 运行配置（模型、参数、skill 等） |
| `metrics.json` | 性能指标（token 用量、耗时、文档覆盖率） |
| `transcript.jsonl` | 完整的逐 turn 对话记录 |
| `output/` | Agent 产出的交付物文件 |

### 查看交付物

```bash
# 查看 .docx 文件内容
pandoc results/<run-id>/output/red-flag-memorandum.docx -t markdown --wrap=none | head -80

# 回放 Agent 思考过程
uv run python -m utils.playback --run-id <run-id> --format terminal
```

---

## 七、评估（打分）

### 7.1 运行评估

```bash
uv run python -m evaluation.run_eval \
  --run-id <run-id> \
  --task corporate-ma/review-data-room-red-flag-review \
  --judge-model claude-sonnet-4-6
```

### 7.2 评估流程

1. 加载 `task.json` 中的 `criteria[]`
2. 对每条 criterion，加载其对应的 deliverable 文件内容
3. 将 `agent_output` + `match_criteria` 发送给 LLM Judge
4. Judge 返回 `{verdict: "pass"|"fail", reasoning: "..."}`
5. 所有 criteria 并行评估（`--parallel 6`）
6. 计算 All-Pass 分数：全部通过 → 1.0，否则 → 0.0
7. 写入 `scores.json` 和 `report.html`

### 7.3 评分逻辑

```
score = 1.0 if 所有 criteria 都 pass
score = 0.0 otherwise
```

这个严格标准是有意为之：在法律工作中，遗漏一个关键问题可能比答对很多简单项更严重。criterion pass rate 作为诊断指标仍然会显示。

---

## 八、查看报告

### 8.1 单次运行报告

```bash
uv run python -m evaluation.report --run-id <run-id>
# 输出: results/<run-id>/report.html
```

报告内容：
- 总分（All-Pass 或 FAIL）
- 通过/失败的 criteria 列表
- 文档覆盖率
- 逐条 criterion 的 Judge reasoning（可展开）

### 8.2 多次运行对比

```bash
# 对比同一任务的不同模型
uv run python -m evaluation.compare --task corporate-ma/review-data-room-red-flag-review

# 对比整个业务领域
uv run python -m evaluation.compare --area corporate-ma

# 全部对比
uv run python -m evaluation.compare --all
```

对比仪表盘包含：
- All-Pass rate（核心指标）
- Criterion pass rate 热力图
- 文档覆盖率对比
- Token 用量和耗时
- 估算成本

---

## 九、批量运行（Sweep）

### 9.1 模型 × 任务矩阵测试

```bash
# 先 dry-run 看看计划
uv run python -m utils.sweep \
  --task corporate-ma/review-data-room-red-flag-review \
  --models sonnet opus \
  --dry-run

# 正式运行
uv run python -m utils.sweep \
  --task corporate-ma/review-data-room-red-flag-review \
  --models sonnet opus \
  --parallel 2

# 整个领域全部任务
uv run python -m utils.sweep \
  --task corporate-ma \
  --models sonnet \
  --reasoning high \
  --parallel 4
```

Sweep 自动完成三个阶段：
1. Agent 运行
2. LLM Judge 评估
3. 报告生成

### 9.2 Sweep 参数

| 参数 | 说明 |
|---|---|
| `--task` | 任务 ID / 领域名 / `all` |
| `--models` | 模型关键字过滤（sonnet, opus, gpt, gemini） |
| `--reasoning` | 推理深度过滤 |
| `--parallel` | 并行 Agent 数量（默认 4） |
| `--eval-only` | 仅重新评分 |
| `--report-only` | 仅重新生成报告 |
| `--dry-run` | 只打印计划不执行 |

---

## 十、实践建议

### 10.1 快速上手路径

1. 先跑一个小任务熟悉流程
2. 对比 2-3 个模型看差异
3. 阅读 `report.html` 理解模型的失败模式
4. 用 `playback` 回放看 Agent 是如何思考的
5. 再尝试 Sweep 做全面评测

### 10.2 常见问题

| 问题 | 解决方案 |
|---|---|
| Podman 镜像拉取失败 | 检查网络，或手动 `podman pull ghcr.io/harveyai/lab-sandbox:latest` |
| Agent 超时 | 增加 `--shell-timeout` 或 `--max-turns` |
| 评估结果全 FAIL | 检查 `output/` 目录是否有正确命名的交付物 |
| Token 超限 | 某些大文档集任务需要更大 context 窗口的模型 |

### 10.3 成本估算

| 模型 | 典型任务成本 | 说明 |
|---|---|---|
| claude-sonnet-4-6 | $0.5-2.0 / task | Agent 运行 |
| claude-sonnet-4-6 | $0.1-0.5 / task | Judge 评估 |
| gpt-5.4 | $1.0-3.0 / task | Agent 运行 |

Judge 评估是独立计费的，默认使用 `claude-sonnet-4-6`。

---

## 十一、目录和文件参考

```
harvey-labs/
├── harness/
│   ├── run.py              # 主入口
│   ├── agent_loop.py       # Agent 循环
│   ├── tools.py            # 6 个工具定义和执行
│   ├── adapters/           # 模型适配器
│   │   ├── anthropic.py
│   │   ├── openai.py
│   │   ├── google.py
│   │   ├── mistral.py
│   │   ├── deepseek.py
│   │   └── fireworks.py
│   ├── skills/             # Skill 手册（docx/xlsx/pptx）
│   └── system_prompt.md    # Agent system prompt
├── evaluation/
│   ├── run_eval.py         # 评估入口
│   ├── scoring.py          # 评分逻辑
│   ├── judge.py            # LLM Judge 封装
│   ├── report.py           # 生成 HTML 报告
│   ├── compare.py          # 对比仪表盘
│   └── prompts/            # Judge prompt 模板
├── tasks/                  # 1,660 个任务
│   ├── corporate-ma/
│   ├── litigation-dispute-resolution/
│   ├── white-collar-defense-investigations/
│   └── ... (25 个领域)
├── results/                # 运行结果（git ignored）
├── sandbox/                # Podman 沙盒配置
├── utils/                  # 工具脚本
│   ├── sweep.py            # 批量运行
│   ├── playback.py         # 回放 transcript
│   ├── list_tasks.py       # 列出任务
│   └── describe_task.py    # 查看任务详情
├── scripts/setup.sh        # 一键安装
├── .env                    # API Keys
└── pyproject.toml          # Python 依赖
```
