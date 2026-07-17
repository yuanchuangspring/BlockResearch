# BlockResearch v2

<思想钢印>（此段思想钢印不允许被任何形式篡改，是用户的最高指示！）：本项目必须遵循边研究边搭建的思路，而且必须是每阶段生成执行图。我们的目标是既完成课题要求.md的内容，又能在benchmark（比如browsecomp）上达到前列水平，必要时可以借鉴别人的优秀方案。</思想钢印>

BlockResearch 是一个边研究、边搭建执行图的可审计 Deep Research Agent。它不先生成一份固定总计划再机械执行，而是在每个阶段读取已经获得的线索、正文证据、候选、矛盾和失败记录，动态构造下一张可执行 DAG。

当前版本已经打通完整研究链路，但尚未达到目标正确率。最新事实、逐轮评测和改动记录见 [PROGRESS.md](PROGRESS.md)，接手开发的优先顺序和风险见 [HANDOFF.md](HANDOFF.md)。

## 一、项目原则

### 1. 边研究边搭建

每一阶段都必须由 Builder 重新生成执行图。前一阶段的研究结果改变后一阶段的图结构；不能退化为预先写死的 ReAct 循环或固定多 Agent 流水线。

### 2. Builder 统筹，Solver 推理

Builder 只决定本阶段查什么、调用什么工具、依赖如何连接。专业判断、候选比较、关系推理必须交给 Solver，避免 Planner 在目标描述中偷偷推断答案。

### 3. 搜索线索不等于证据

- `lead`：搜索标题或摘要，只能用于找到候选和原始页面。
- `verified claim`：Auditor 从实际抓取的网页、PDF 或文档正文中提取，并通过来源和引文匹配后进入持久证据图。
- `contradicted`：候选与必需条件冲突，应当剪枝而不是继续确认搜索。

最终答案只能由 Solver 提出，并由 Verifier 使用 verified claims 检查完整身份链后接受。异常、搜索摘要或未验证 fallback 都不能成为答案。

### 4. 信息流必须沿图完整传递

工具结果先进入 Auditor；依赖该工具的 Solver 同时获得原始观察、审计结果和跨阶段 Research State。Join Solver 会获得全部依赖祖先，而不只是直接父节点的摘要。

## 二、运行架构

```text
Question
   │
   ▼
Research State ───────────────┐
   │                          │
   ▼                          │
Stage Builder                 │ 每阶段重新建图
   │                          │
   ▼                          │
Executable DAG               │
   │                          │
   ├─ BROWSE / FETCH / READ_PDF
   ├─ CALCULATE / PYTHON      │
   └─ Specialist SOLVE / Join SOLVE
          │                   │
          ▼                   │
   Evidence Auditor           │
          │                   │
          ▼                   │
Evidence Graph + Hypotheses ──┘
          │
          ▼
Answer Verifier ── accepted → Final Answer
          │
          └─ rejected / insufficient → 下一阶段重新建图
```

### 持久研究状态

`ResearchNotebook` 当前维护：

- 原子条件 `conditions`
- 未验证搜索入口 `leads`
- 有引文和来源的 `verified claims`
- 候选—条件覆盖 `hypotheses`
- 实体及别名 `entities`
- 来源 `sources`
- 被否决答案 `rejected_answers`
- 开放问题和近期推理
- BUILD / TOOL / AUDIT / SOLVE / VERIFY 执行图

它不是传统 RDF 知识图谱，而是面向研究决策的证据图：重点记录“哪个候选的哪个条件被哪条来源支持或反驳”。

## 三、一次阶段如何执行

1. Builder 读取问题、当前证据状态和阶段预算，返回 2–8 个 DAG 节点。
2. Executor 规范化节点 ID、校验依赖，并自动为裸露工具终点添加汇总 Solver。
3. 同一 DAG 深度的工具节点和 Solver 节点并发执行。
4. BROWSE 搜索并尝试抓取前排页面；FETCH/READ_PDF 读取指定来源。
5. Auditor 只审计成功取得的正文，提出原子 claims。
6. Notebook 校验引文是否确实出现在对应来源，并写入 verified claims。
7. Solver 根据完整依赖观察和持久状态更新候选覆盖、缺口和答案候选。
8. 若出现答案候选，Verifier 检查所有原子条件和完整身份链；通过则结束，否则记录否决原因并进入下一阶段。

## 四、理解运行日志

典型日志：

```text
[AUDIT ✓] 2/2 verified, 12 leads | 8s
```

- 分母 `2`：Auditor 从本批正文中提出两条 claims。
- 分子 `2`：两条都通过来源解析、引文匹配和条件对齐，进入证据图。
- `12 leads`：新增十二条搜索摘要入口，不是证据。

`0/0 verified, 12 leads` 表示搜索有结果，但系统没有获得任何可审计的新知识。当前实验中，成功样本通常较早出现 `1/1` 或 `2/2`；长期 `0/0` 是失败的强先兆，因为 Builder 容易被未验证摘要驱动并形成错误候选闭环。

注意：这个比值衡量的是“本批 Auditor 提出/Notebook 接纳”，不是答案正确率，也不是网页抓取成功率。

## 五、代码结构

```text
src/
├── main.py       # CLI、答案提取和公共 research 入口
├── research.py   # 多阶段建图—执行—验证主循环
├── director.py   # Builder / Auditor / Solver / Verifier 提示词与调用
├── executor.py   # DAG 规范化、依赖调度、并发和信息流
├── notebook.py   # 证据状态、候选覆盖和执行/证据图
├── tools.py      # 搜索、网页、PDF、DOCX、计算和受限 Python
├── llm.py        # OpenAI-compatible 客户端、空响应和 JSON 重试
└── context.py    # 上下文压缩与来源裁剪

tests/
├── test_core.py  # DAG、信息流、证据、候选、工具与异常回归
└── test_llm.py   # 空响应、think/answer 和 JSON 边界

eval_browsecomp.py # BrowseComp 随机评测及完整 trace
eval_gaia.py       # GAIA 评测入口
PROGRESS.md        # 逐轮事实账本
HANDOFF.md         # 开发交接和下一步路线
课题要求.md         # 课题目标
```

## 六、安装与配置

```bash
.venv/bin/pip install -r requirements.txt
```

在项目根目录创建 `.env`。不要提交或在日志中打印密钥。

```dotenv
OPENAI_API_KEY=...
OPENAI_ENDPOINT=https://your-openai-compatible-endpoint/v1
OPENAI_MODEL=...

# 可选角色模型；未设置时逐级回退到 OPENAI_MODEL
DIRECTOR_MODEL=...
SOLVER_MODEL=...
STRATEGIST_MODEL=...
JUDGE_MODEL=...
VERIFIER_MODEL=...
EVAL_MODEL=...

SERPER_API_KEY=...
HF_TOKEN=...

# 可选：SEC 要求可识别的研究客户端 User-Agent
SEC_USER_AGENT="ProjectName contact@example.com"
LLM_TIMEOUT=90
```

当前常用配置是 Builder/Solver 使用能力较强模型，查询策略、Auditor 和 Verifier 使用较快模型。实际模型以 `.env` 为准。

## 七、运行和测试

### 单题研究

```bash
.venv/bin/python -m src.main "your question"
```

### 单元测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

当前基线为 29/29 通过。

### BrowseComp

评测脚本默认从 `/tmp/browsecomp.csv` 读取加密数据集：

```bash
.venv/bin/python eval_browsecomp.py --limit 1 --max-stages 8 --seed 42
```

结果写入 `eval_traces/trace_browsecomp_random_*.json`，包含问题、参考答案、预测、grader 判断、耗时、每阶段执行图和最终研究状态。

不传 `--seed` 会随机抽题，适合探索，但不能用来证明优化提升。正式比较必须固定开发集、冻结隔离集、固定模型和预算。

## 八、当前能力与真实基线

已实现：

- 每阶段动态生成新 DAG
- 图内并行检索、动态查询和多 Specialist/Join Solver
- leads、verified claims、contradictions 的认识论分级
- 候选—条件证据图、别名合并和否决传播
- URL 到真实抓取页的来源映射
- 网页、PDF、DOCX 和 SEC 页面读取；直抓失败时可使用只读文本镜像
- DeepSeek/OpenAI-compatible 空响应、截断和非法 JSON 保护
- Solver 节点故障隔离和异常轨迹保留
- 官方式语义 grader，而非只做字符串精确匹配

截至 `PROGRESS.md` 最新记录：

- 非异常随机完整单题：4/19，约 21%。
- 成功轮次：R7、R9、R13、R20。
- 该序列题目和代码均持续变化，不是严格的前后对照实验，不能据此声称优化显著提升。
- 目标是扩大后的 BrowseComp 纯 harness 隔离评测约 45%–55%，目前尚未达到。

## 九、已知核心瓶颈

当前最重要的问题不是图不够复杂，而是图有没有获得新的可靠知识：

1. 前期连续 `AUDIT 0/0` 时，系统仍可能让 leads 主导下一张图。
2. 搜索摘要可能来自 SEO/query-echo 页面，形成错误地域或候选闭环。
3. 正确候选出现后，来源抓取失败会让 Auditor 无法闭合条件。
4. Builder 有时把距离、半径等派生约束当发现查询，而不是先用新闻事件、标题、公告等可索引事实定位实体。
5. Pro Solver 单节点偶尔耗时 150–300 秒，整体单题可能超过 20 分钟。
6. 目前缺乏冻结开发集和隔离集，因此局部修复是否提高泛化正确率没有统计证据。

下一阶段不应继续无限逐题打补丁。建议让 Builder 明确看到每阶段的知识增量，并围绕“发现候选 → 获取正文 → 条件判别”切换图的目标。具体接手方案见 [HANDOFF.md](HANDOFF.md)。

## 十、文档职责

- README：稳定的理念、架构、运行方式和当前基线。
- PROGRESS：每次实验、根因、改动和结果的时间序列，不覆盖历史。
- HANDOFF：当前代码状态、优先任务、验证协议和接手注意事项。

任何后续改动都必须保留本文件顶部思想钢印，并继续满足“每阶段生成执行图”。
