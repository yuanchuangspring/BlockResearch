<div align="center">

<img src="static/title.png" alt="BlockResearch" height="56">

<em>边研究，边搭建 —— 逐阶段动态图构造，证据驱动的深度研究框架</em>

[![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/github/license/yuanchuangspring/BlockResearch?style=flat-square)](LICENSE)

[English](./README.md) | [中文文档](./README_CN.md)

<img src="static/%E5%9B%BE%E7%89%871.png" alt="BlockResearch 概念图" width="680">

</div>

## 为什么需要 BlockResearch？

大语言模型催生了一类新的深度研究 Agent，它们能自主搜索网页、阅读来源并回答复杂开放域问题。但大多数此类系统共享一个根本缺陷：**它们在研究开始之前就决定了研究的结构。**

两类失败模式尤为突出。其一，**逐步响应的 Agent** 根据每次观察生成下一个动作，但当早期搜索返回一个看似合理的候选后，后续查询逐渐围绕该候选收缩。候选从待验证的假设，变成了后续所有信息获取的隐含前提——系统看似执行了多轮研究，实际退化为围绕第一个猜测的线性确认循环。

其二，**固定工作流的 Agent** 将研究编码为预设的角色、任务或阶段管道。不同问题需要不同的研究形态——有的依赖大范围候选发现，有的需要沿中间实体连续多跳推理，还有的需要先扩大候选集再对各候选分别纵向验证。同一任务的最优形态还会随阶段变化。固定拓扑无法表达这种多样性。

**BlockResearch** 通过一个简单但根本的改变来解决这一问题：研究组织的基本单位不是动作或工作流，而是**语义研究积木**——每种积木承担一种明确的认识功能（扩展候选空间、获取来源材料、完成局部推断、检验候选条件）。每个阶段，系统读取当前全部研究状态，从这些积木中构造一张新的可执行图。图可以是面向广度探索的并行结构、面向多跳推理的链式结构，或是先分支再汇合比较的混合结构——图的形状由证据决定，不由框架预设。

> *研究的形状，在过程中成形。*

这一设计带来三个优势：

- **并行候选管理。** 多个假设在同一阶段内并存，各自由独立子图处理。规划者在更高层次管理候选分支，而非围绕单一候选连续生成动作。
- **任务自适应拓扑。** 相同的基础积木可根据问题和当前研究阶段组合出不同的图结构。
- **组件级自进化。** 每个积木的明确语义边界实现了局部归因——失败可以追溯到具体组件，从而完成定向的选择、变异与重组。

---

## 快速开始

```bash
git clone https://github.com/yuanchuangspring/BlockResearch.git
cd BlockResearch
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

创建 `.env`：

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_ENDPOINT=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-v4-flash
BRAVE_API_KEY=...
```

## 使用

**命令行**

```bash
.venv/bin/python -m src.main "2017年哪篇文章称哪首歌是长期以来最美的音乐作品？"
```

**可视化 Demo**

```bash
.venv/bin/uvicorn src.web:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`，输入问题，观察执行图通过 SSE 实时构建。

**测试**

```bash
.venv/bin/python -m unittest discover -s tests -v
```
