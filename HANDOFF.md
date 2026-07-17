# BlockResearch v2 开发交接

> 交接时间：2026-07-17（Asia/Shanghai）  
> 配套文档：[README.md](README.md) 讲系统；[PROGRESS.md](PROGRESS.md) 讲逐轮事实；本文讲接下来怎么做。

## 1. 不可变要求

接手前先阅读 README 顶部的思想钢印。不可删除、改写或绕开：

- 必须边研究边搭建。
- 每个阶段必须动态生成执行图。
- Builder 负责统筹，专业推理交给 Solver。
- 可以大幅调整其余结构，但不能退化成固定总计划、固定流水线或纯 ReAct。
- 同时面向 `课题要求.md` 和 BrowseComp 等 benchmark。

## 2. 当前结论先说清楚

系统工程可靠性和答案可信度已经明显改善，但没有证明正确率显著提升。

- 当前记录的非异常随机完整题为 4/19，约 21%。
- 成功轮次是 R7、R9、R13、R20。
- R1–R25 是不断变化代码上的随机单题，不是冻结前后对照，不能作为正式提升曲线。
- 目标 45%–55% 尚未达到。
- R26 跑到第 2 阶段后按用户要求人工停止，不计分子、分母，也没有完整评测 trace。
- 当前目录不是 Git 仓库，没有可用 commit 历史；接手后建议先初始化版本管理并保存此交接基线，但不要误删现有 `.env` 或轨迹。

不要对用户宣称“已经接近目标”或把异常中止排成普通错误。每次报告应区分：正确、正常完成但错误、异常中止、人工停止。

## 3. 当前最有价值的观察

### 3.1 AUDIT 比值是领先指标

日志：

```text
[AUDIT ✓] accepted/proposed verified, N leads
```

例如 `2/2` 表示 Auditor 从正文提出两条 claim，Notebook 接纳两条。`0/0, 12 leads` 表示只有搜索摘要，没有获得新正文知识。

成功题大多在早期出现 `1/1` 或 `2/2`；失败题常连续多阶段 `0/0`。这不是严格因果证明，但目前是最稳定的轨迹信号。

### 3.2 当前主要失败循环

```text
BROWSE 有很多 snippets
→ 正文抓取为空或无关
→ AUDIT 0/0
→ Solver 从 leads 推断候选
→ Builder 围绕候选继续搜索
→ 更多相似 snippets，但 verified claims 不增长
```

需要解决的是信息增益控制，而不是继续增加提示词中的个别领域规则。

### 3.3 正确候选有时已经出现

R25 在 S5 已转向标准答案 `FormFactor`，但 SEC 403 和投资者站超时使证据无法闭合。现已在 `src/tools.py` 加入：

- SEC 声明式 User-Agent。
- 公开页面直抓失败后的 `r.jina.ai` 只读文本镜像回退。
- 原失败 SEC URL 已实测成功取得 20,000 字正文。

该改动只做了工具级和单元测试，尚未完成新的随机优化外题验证。

## 4. 接手时的代码状态

### 主循环

`src/research.py`

- 默认 8 个阶段。
- 每阶段调用 `build_stage` 产生新图。
- Solver 产生答案候选后调用 Verifier。
- Verifier 拒绝会写入 `rejected_answers`。
- 异常路径已禁止返回未验证 fallback。

### 角色和提示词

`src/director.py`

- `BUILDER_PROMPT`：建图契约、搜索方向、候选剪枝和信息流规则。
- `AUDITOR_PROMPT`：只抽取正文直接支持的原子事实。
- `SOLVER_PROMPT`：专业推理、候选覆盖和动态 query。
- `VERIFIER_PROMPT`：只接受/拒绝候选，检查完整身份链。
- Builder 最多三次契约重试；目前硬校验仅图、首轮 conditions 和首轮语言。

不要重新加入固定“三 Solver beam”等图形硬模板。R17 已证明这会让合法动态图因形状不符而中止。

### DAG 执行

`src/executor.py`

- 支持 BROWSE、FETCH、READ_PDF、CALCULATE、PYTHON、SOLVE。
- 同层节点并发。
- Solver 获得完整依赖祖先。
- 终端工具会自动接入 sink Solver。
- 单 Solver 失败不会清空并行分支。
- Auditor 日志的比值在这里计算。

### 研究状态

`src/notebook.py`

- 搜索摘要持久化为 leads。
- 正文 claim 需要来源解析和引文匹配。
- 候选按别名合并；否决沿别名传播。
- 包含 `unknown`、`unidentified`、`unspecified` 的泛化画像不进入 hypotheses。
- 证据图可由 `to_evidence_graph()`/状态序列化查看。

### 工具

`src/tools.py`

- Serper 优先，Yahoo/DuckDuckGo 回退。
- BROWSE 每条 query 默认抓取前排页面。
- FETCH 支持 HTML、JSON、PDF 转交和 DOCX。
- READ_PDF 使用 PyMuPDF。
- SEC 使用 `SEC_USER_AGENT`，无配置时有项目默认值。
- 直抓失败会尝试只读文本镜像。

关注新回退是否带来两个副作用：镜像正文的来源 URL 映射、Markdown 文本被 BeautifulSoup 处理后的引文匹配。

### LLM

`src/llm.py`

- OpenAI-compatible `AsyncOpenAI`。
- 区分 `reasoning_content` 与最终 `content`。
- 处理 `<think>/<answer>`。
- 空答案、length 截断、连接错误和非法 JSON 有界重试。
- 不要把 reasoning 当最终答案。

## 5. 建议的下一步：先结构化，不再逐题打补丁

### P0：把知识增量显式交给 Builder

目标：让 Builder 知道上一阶段是否真正获得知识，而不是自己从长状态中猜。

建议在 Research State 中增加非常小的阶段摘要，而不是复杂状态机：

```json
{
  "last_stage": {
    "new_verified_claims": 0,
    "new_leads": 12,
    "successful_pages": 0,
    "candidate_changes": 1
  }
}
```

然后让 Builder 围绕信息增益选择下一张图：

- 候选尚无：用可索引事件、原话、标题和关系发现命名实体。
- 候选已有但无正文：FETCH/READ 已有可信 URL，或换来源。
- 候选条件有矛盾：剪枝并回到替代候选发现。
- verified claims 已增长：继续补最有判别力的缺口。

不要把这些变成几十个布尔状态或硬编码领域规则。

### P0：建立可比较评测协议

在进一步优化前先锁定：

1. 固定开发集，允许查看题目和轨迹，用于结构优化。
2. 固定隔离集，只允许程序运行，不查看题目和 Agent 过程。
3. 固定 seed、模型、阶段数、并发度和时间/Token 预算。
4. 保存每轮代码版本或 commit hash。
5. 至少报告：准确率、异常率、平均耗时、首条 claim 阶段、连续 0/0、每题 claims 总数。

没有这一步，不要再根据随机单题序列判断“提升了多少”。

### P1：增加评测诊断字段

建议从现有 trace 直接计算，不需要新 Agent：

- `first_verified_stage`
- `verified_claims_by_stage`
- `consecutive_zero_audit_max`
- `successful_fetch_count / attempted_fetch_count`
- `first_named_candidate_stage`
- `candidate_prune_count`
- `answer_condition_coverage`
- LLM、搜索和抓取耗时拆分

这可以验证“早期 1/1、2/2 与成功相关”到底有多强，而不是只凭肉眼。

### P1：检查正文回退的泛化

下一次完整随机题应验证新 `_download`：

- 403/超时是否成功转镜像。
- Auditor 是否从镜像正文产生 claims。
- 原始 URL 是否仍能被 `_resolve_source` 正确解析。
- 是否因为镜像返回错误页而引入假证据。

### P2：性能

Pro Solver 经常耗时 150–300 秒。先统计角色耗时，再考虑：

- 纯 query strategist 继续走 flash。
- 简单来源抽取尽量由 Auditor 完成，不调用 pro。
- 给 Solver 更小且有任务针对性的上下文。
- 不要单纯降低阶段数；复杂题 6 阶段已被证明经常来不及执行最终 queries。

## 6. 不建议继续做的事情

- 不要为某道错题增加国家名、企业名格式、母公司、全称等定向规则。
- 不要把每个错误转化成新的全局状态和检测器。
- 不要强制每张图固定有几个 Solver 或固定 beam 形状。
- 不要让 Builder 推断专业答案。
- 不要把 leads 升级成 verified claims。
- 不要为了提高表面命中率放松 Verifier，让无证据候选通过。
- 不要用不断变化的随机单题结果宣称统计提升。
- 不要改动 README 思想钢印。

## 7. 测试和运行清单

接手后先运行：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

期望：29/29。

检查配置但不要打印值：

```bash
sed -E 's/=.*$/=***REDACTED***/' .env
```

单题烟雾测试：

```bash
.venv/bin/python -m src.main "your question"
```

BrowseComp：

```bash
.venv/bin/python eval_browsecomp.py --limit 1 --max-stages 8 --seed 42
```

正式批量前先确认 `/tmp/browsecomp.csv` 存在。评测会产生可能很大的 `eval_traces/*.json`，不要提交密钥，也不要把数据集问题/答案泄露到公开仓库。

## 8. 关键轨迹索引

轨迹目录：`eval_traces/`。按 PROGRESS 中轮次和时间对应。

- R7：首次完整正确闭环。
- R9：URL→抓取页来源映射问题及官方式 grader。
- R13：多次拒绝错误候选后正确收敛。
- R20：四阶段全链早停成功。
- R21：错误候选别名反复复活。
- R22：SEO/query echo 污染候选图，后因连接错误中止。
- R23：Verifier 异常时未验证 fallback 泄漏，现已修复。
- R24：正确答案 El Wak Stadium，错误使用空间约束做发现。
- R25：正确候选 FormFactor 已出现，但来源访问失败导致无证据答案。

具体 trace 文件名和每轮根因以 PROGRESS 为准；不要只看最终预测，要同时看 conditions、claims、hypotheses、工具 error 和 verification。

## 9. 推荐交接工作顺序

1. 阅读思想钢印、README、PROGRESS R20–R25。
2. 跑 29 个测试。
3. 检查 `src/tools.py` 新正文回退并补模拟测试。
4. 给 trace 增加知识增量诊断，不先改提示词。
5. 固定开发集和隔离集，记录当前基线。
6. 用开发集实现“发现 → 证据化 → 判别”的 Builder 信息增益反馈。
7. 只在开发集优化；隔离集仅程序化跑分。
8. 达到稳定提升后再考虑多轨迹 best-of-N 和更大 test-time compute。

## 10. 交接完成标准

下一位开发者应能回答并用数据证明：

- 连续 `0/0` 与错误率的相关性有多大？
- 新知识增量反馈是否降低连续 `0/0`？
- 正确候选出现后，正文抓取成功率是否提高？
- 固定开发集提升是否泛化到隔离集？
- 提升来自准确率，而不只是减少异常或增加拒答？

达到这些标准后，才算从“逐题修补”转向系统优化。
