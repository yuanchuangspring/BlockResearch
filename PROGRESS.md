# BlockResearch 进展记录

> 最后更新：2026-07-17 23:02（Asia/Shanghai）  
> 不可变约束：严格遵守 README 的思想钢印——边研究边搭建，并且每阶段动态生成执行图。

## 最终验收

- [ ] 课题要求中的长程检索、证据整合、冲突处理、可复现评测和过程诊断完整落地
- [ ] BrowseComp 纯 harness 隔离评测：达到约 45%–55%
- [ ] 最终结果使用足够大的预先锁定样本，报告样本数、seed、模型、并发度、计算预算与置信区间
- [ ] 调优集与最终隔离评测集严格分离；隔离集不查看题目和轨迹
- [ ] 完整保存问题、执行图、工具结果、证据、判断、答案、耗时和错误类型

## 已完成

- [x] DeepSeek 空响应修复：区分 `reasoning_content` 与最终答案；截断扩容重试；非法/空 JSON 不再静默通过
- [x] 统一两代冲突代码为“每阶段 Builder 生成 DAG”的单一运行链
- [x] Builder 只统筹，专业推理由 SOLVE 节点负责
- [x] 修复同阶段信息流：工具原始输出和审计结果真实进入依赖它的 Solver
- [x] 支持 BROWSE / FETCH / READ_PDF / CALCULATE / PYTHON / SOLVE 节点和多层 DAG
- [x] BUILD / TOOL / AUDIT / SOLVE / VERIFY 写入持续增长的可审计执行图
- [x] 搜索结果作为 lead 跨阶段持久化；正文证据才能成为 verified claim
- [x] Builder 非法图自动重试；中途异常保留已有轨迹和研究状态
- [x] 节点级运行心跳与 Auditor proposed/accepted 计数
- [x] 单元测试：29/29 通过
- [x] 证据知识图谱 v1：Condition / Entity / Claim / Source 节点及 evidence edges
- [x] Solver 候选必须输出 candidate→condition 覆盖及 lead/claim evidence IDs
- [x] 角色模型分级：Builder/Solver 使用 deepseek-v4-pro，Auditor/Verifier 使用 flash
- [x] 零候选的多条件问题使用图内多假设束：并行 Specialist SOLVE → Join SOLVE
- [x] 同一 DAG 深度的 Solver 并发执行，Join 依赖边保证信息完整汇合
- [x] `No match found` 等无答案哨兵值不再进入 Verifier 或成为 fallback 答案
- [x] 执行图支持 Solver 动态生成 queries，依赖它的 BROWSE 自动消费，无需图外编排
- [x] 完成详细 README 与 HANDOFF 交接文档，明确真实基线、核心瓶颈、代码职责和接手优先级

## 随机单题迭代记录

| 轮次 | 结果 | 用时 | 系统性发现 | 处理 |
|---|---:|---:|---|---|
| R1 | 中止 | 15m | 180秒×3重试导致长时间无心跳 | 超时降至90秒×2；增加节点心跳 |
| R2 | 0/1 | 284s | Builder 返回 `blocks:null` 引发异常且轨迹丢失 | Builder协议校验重试；异常携带部分轨迹 |
| R3 | 0/1 | 527s | 搜索结果没有跨阶段保存，0 claims 后反复重搜 | 搜索结果确定性写入 leads；审计记录增强 |
| R4 | 0/1 | 807s | leads 成功驱动 Pinakbet Festival 多跳建图，但126条线索缺乏实体/条件聚合，最终候选漂移 | 已实现证据知识图谱 v1 |
| R5 | 0/1 | 500s | 图谱锁定候选并获得条件覆盖，但SEO查询回声伪造多条一致 leads，将 Bath 自我强化；正确答案 Leeds | Auditor仅审正文；status程序校正；rejected_answers阻断确认偏差 |
| R6 | 0/1 | 730s | 英文医生题首轮擅自使用中文查询，整条路径偏到中国医学史；未输出无证据人名 | Stage 1语言/地域偏置校验；稀有关系对子检索 |
| R7 | **1/1** | 830s | Uganda初始候选被拒后，Evidence Graph分叉到Zimbabwe；最终7条verified claims支持首任总理 | 首次完整闭环成功；继续随机泛化验证 |
| R8 | 0/1 | 1230s | 论文题在零候选状态下单Solver过早枚举少量作者，后续反复搜索错误候选集合；输出No match | 早期阶段强制多假设束：并行Specialists + Join Solver；同层Solver并发 |
| R9 | **1/1**（官方式grader） | 1054s | 找到`The Pikeman (1798 Monument)`，标准答案为`1798 Monument`；Auditor用URL标注正文，Notebook只接受block ID，6条claim全丢 | 修复URL→抓取页→block映射；Verifier获取正文；官方式LLM grader；答案字段简短化 |
| R10 | 0/1 | 1258s | 图能从CeraVe错路分叉到多品牌，但全程0 verified；Solver能推理检索策略却无法把动态query传给后续BROWSE | Auditor抽取支持+反证局部事实；打通`SOLVE→dynamic BROWSE→SOLVE`图内信息流 |
| R11 | 0/1 | 1277s | 动态query DAG连续成功运行，但存在型桥接线索被过早收缩为单个著名样例，lead被下阶段Builder当成硬约束 | 保留桥接实体候选集；lead只生成临时分支，只有verified claim可改写为硬约束 |
| R12 | 异常中止 | 408s | Auditor首次在新题上2/2 claims成功入图；一个并行Solver无效JSON导致整层`gather`抛错，成功分支也丢失 | Solver节点级故障隔离：失败显式入图，其他分支与Join继续 |
| R13 | **1/1** | 1274s | 动态查询+定向抓取得到3条verified claims；`opponent birth year`与Houston先后被Verifier拒绝，最终有证据收敛到Glasgow | 完整验证“保留不确定性→分支重建→正文入图→拒绝错候选→正确收敛” |
| R14 | 异常中止 | 83s | Stage 1 Builder两次输出均未通过契约，但旧错误不保留具体违规项 | 3次有界重试；将blocks/conditions/language/beam具体违规反馈Builder并写入轨迹 |
| R15 | 0/1 | 1413s | 正确识别Romania并找到官方PDF，但INSSE持续503；S6 Solver产生了新query却无下一阶段执行 | 随机评测从6阶段恢复为8；最终阶段必须图内执行动态query；FETCH支持DOCX正文 |
| R16 | 0/1 | 1352s | 动态DAG与8阶段终局执行成功，但关系线索被偷换成类型词`boss romance`；Instagram薄页伪验证；Verifier已拒绝的候选被旧fallback最终输出 | 拒绝候选清除fallback；保留关系语义；薄页不审计；数字condition ID规范化 |
| R17 | 异常中止 | 172s | Builder连续生成有图/条件/正确语言的方案，仅因未符合人工规定的3-Solver beam形状被全部拒绝 | 删除beam硬检测；beam与动态query均作为Builder可选策略，不强制图模板 |
| R18 | 0/1 | 1039s | 自由DAG稳定运行，但无地域证据即切德/法语；把米级距离这类派生条件当作原文检索词，受SEO query echo诱导进入德国错路 | 可观测事实用于发现、派生条件用于验证；Stage 1不自行翻译；query echo不升级；终端工具自动汇入sink Solver |
| R19 | 0/1 | 868s | 发现/验证DAG高效运行且获得3 claims，但Verifier只看到“姓名+学位”真证据，未看原子条件/候选覆盖，错把未连回原博客的人接受并提前终止 | Auditor获取conditions；claim只能验证自己标注的condition；Verifier获取全条件与候选覆盖图，要求身份链闭合 |
| R20 | **1/1** | 779s | 4阶段获得论文候选、致谢子女名与公司创办证据，全链Verifier接受`Preston` | 验证claim→condition对齐与全链早停成功；纯query strategist改用flash降低延迟 |
| R21 | 0/1 | 1360s | 正确拒绝证据链不全的Charles Ellis，但其姓名/头衔变体被图谱当成多个候选反复复活；从宽泛疾病侧正向搜索，未优先反查稀有履历交集，正确答案为Ross Andel | 候选别名实体归一化并共享否决；Builder按最小候选集选择搜索方向，优先“稀有人物签名→作品关系”反向检索 |
| R22 | 异常中止 | 798s | S1策略节点失败后图可继续恢复；S2–S4被无关域名SEO关键词回显诱导，将无姓名的地域画像写成候选；S5 API连接错误终止，标准答案Wale Adebayo | 统一认识论写入Research State；搜索片段只作导航；候选必须是命名实体；发现查询限一个稀有短语或两个关系，禁止全条件关键词袋 |
| R23 | 异常中止（曾误计0/1） | 618s | 首图正确采用“事实锚点→查询策略→搜索→综合”多层DAG，但Verifier输出截断；异常分支把未验证的中间锚点`Meryl Streep, 1933`泄漏成最终答案，标准答案KT Tunstall | 异常路径永不返回fallback，只有Verifier accepted候选可成为答案；新增回归测试 |
| R24 | 0/1 | 1643s | 标准答案El Wak Stadium；系统以地图距离/半径做发现，受SEO片段诱导锁定Ulinzi；即使k2/k5已矛盾仍持续确认搜索，直到S7才FETCH正文 | 事件/公告先发现、空间条件后验证；unknown/unidentified实体禁止入候选图；任一必需条件矛盾即剪枝；连续无正文证据须FETCH已有URL或换锚点 |
| R25 | 0/1 | 2212s | S1事件发现、S2及时FETCH、S5切换到正确候选FormFactor，流程改进生效；但SEC 403、投资者站超时，11个FETCH/READ节点重复失败，最终仅1个候选条件verified | SEC使用声明式User-Agent；所有公开页面直抓失败后统一走只读文本镜像；原失败SEC URL实测成功获取20,000字正文 |

## 当前架构

```text
每阶段：Research State → Builder → Stage DAG → Executor → Auditor/Solver → Verifier
                                  │
                                  └── 原始输出与依赖边完整保留

跨阶段：Evidence Graph（Condition / Entity / Claim / Source）+ hypotheses + gaps
```

## 当前主要问题

1. Evidence Graph v1 已验证能驱动候选条件 join，但仍需结合 pro 模型验证替代假设质量。
2. Auditor 常因首批页面抓取失败或无关而得到 0 verified claim。
3. leads 数量仍可能过多，需要依靠候选覆盖图选择相关 evidence IDs。
4. 评测尚未达到目标：当前非异常随机完整轮次 4/19（R7、R9、R13、R20正确；R1、R12、R14、R17、R22、R23异常中止不计分母）。
5. R13轨迹规模：46个执行节点；Evidence Graph 153节点、152边；3条verified claims；最终Verifier accepted。

## 外部方案启发

- BrowseComp官方结果表明，持续浏览大量网站、灵活改写查询和增加test-time compute会稳定提高表现。
- 官方64次采样实验中，majority/weighted/best-of-N相对单次尝试提升约15%–25%，best-of-N最好。
- 后续将在每阶段执行图内部维护多假设束，避免单路径早期偏航；不改成图外多套固定Agent流水线。

## 正在进行

- [x] 实现证据知识图谱：Entity / Condition / Claim / Source / Hypothesis
- [x] 明确区分 lead、verified、contradicted，并保留 quote 与来源
- [x] 为 Builder/Solver 提供候选条件覆盖和未解决 join，而不是平面长列表
- [x] R9 随机题：Stage 1已实际生成两个并行Specialist与Join Solver
- [x] 官方式grader复评R9为正确，同指一个实体
- [x] R10随机单题：确认候选分叉有效，暴露动态工具参数信息流缺口
- [x] R11验证`SOLVE→dynamic BROWSE→SOLVE`在多个阶段实际运行
- [x] R12验证Auditor URL映射与局部claim入图，暴露并行Solver故障传播
- [x] R13随机单题正确，动态执行图与Verifier完整闭环
- [x] R14暴露Builder契约失败缺乏可诊断性
- [x] R15验证Builder成功自修正，暴露6阶段未执行最后queries与文档格式缺口
- [x] R16验证8阶段动态长程图可执行，暴露被拒候选回流与关系语义漂移
- [x] R17暴露beam硬校验过度约束Builder，已删除固定图形检测
- [x] R18验证自由DAG稳定执行，暴露可观测性误判与无证据语言偏置
- [x] R19验证发现/验证DAG有效，暴露Verifier缺少全局条件与身份链
- [x] R20随机单题正确，全链验证在4阶段安全早停
- [x] R21验证strategist flash单节点约10–15秒且全链Verifier正确拒绝，但暴露候选实体重复和搜索方向选择问题
- [x] R22暴露SEO查询回显污染候选图；最终因API连接错误异常中止，不计正确率
- [x] R23首图验证反向事实锚点与短查询生效；暴露异常分支绕过Verifier的fallback泄漏
- [x] R24验证异常答案门已封闭；暴露派生空间约束被误用于发现、矛盾候选未剪枝
- [x] R25验证“事件先发现→及时FETCH→候选切换”生效；暴露来源反爬导致正确候选无法闭证
- [ ] R26人工暂停：运行至S2后按用户要求停止，不计评测；后续先完成知识增量诊断与固定评测协议

## 下一里程碑

1. 继续随机单题驱动，只修系统性信息流/检索/证据问题。
2. 将完整随机单次正确率提升到可进入批量评测的水平。
3. 锁定调优集与隔离集，评估单轨迹、多轨迹聚合和test-time compute的性价比。
4. 用扩大的纯 harness 隔离样本验收45%–55%，不以5题小样本代替。
