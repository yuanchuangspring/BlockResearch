<p align="center">
  <img src="static/title.png" alt="BlockResearch" height="48">
</p>

<p align="center">
  <em>Research While Building — Stage-wise dynamic graph construction for evidence-grounded deep research</em>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License"></a>
  <a href="README_CN.md">中文</a>
</p>

<p align="center">
  <img src="static/%E5%9B%BE%E7%89%871.png" alt="BlockResearch Concept" width="680">
</p>

---

## Why BlockResearch?

Large language models have enabled a new class of deep research agents that autonomously search the web, read sources, and synthesize answers to complex open-domain questions. But most of these agents share a fundamental flaw: **they decide the structure of research before it begins.**

Two failure patterns dominate. First, **step-by-step reactive agents** respond to each new observation with the next action, but when early search returns a plausible candidate, all subsequent queries converge around it. The candidate shifts from a hypothesis to be tested into an implicit premise of every further action — the system appears to conduct multi-round investigation while actually executing a linear confirmation loop for its first guess.

Second, **fixed-workflow agents** encode research as a predetermined pipeline of roles, tasks, or stages. Different questions demand different research shapes — some require broad candidate discovery, others need multi-hop chaining through intermediate entities, yet others must first expand a candidate pool and then verify each candidate independently. The same task's optimal shape also changes across stages. A fixed topology cannot express this diversity.

**BlockResearch** addresses this through a simple but fundamental change: the unit of research organization is not an action or a workflow, but a **semantic building block** — each with a clear epistemic function (expand candidates, retrieve sources, perform local inference, verify conditions). At every stage, the system reads the complete research state and constructs a fresh executable graph from these blocks. The graph may be a parallel search for breadth, a chain for multi-hop depth, or a branch-then-join structure for comparison — the shape is determined by the evidence, not by the framework.

> *The structure of research emerges as evidence accumulates. It should not be hard-coded before the task begins.*

This design brings three advantages:

- **Parallel candidate management.** Multiple hypotheses coexist within one stage, each handled by independent subgraphs. The planner manages branches at a higher level instead of generating actions around a single candidate.
- **Task-adaptive topology.** The same building blocks compose into different graph shapes depending on the question and the current stage of investigation.
- **Component-level self-evolution.** Each block's clear semantic boundary enables localized attribution — failures can be traced to specific components, enabling targeted selection, mutation, and recombination across tasks.

---

## Quick Start

```bash
git clone https://github.com/yuanchuangspring/BlockResearch.git
cd BlockResearch
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `.env`:

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_ENDPOINT=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-v4-flash
BRAVE_API_KEY=...
```

## Usage

**CLI**

```bash
.venv/bin/python -m src.main "Which song was described in 2017 as the most beautiful piece of music?"
```

**Web Demo**

```bash
.venv/bin/uvicorn src.web:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` — type a question and watch execution graphs build in real time via SSE.

**Tests**

```bash
.venv/bin/python -m unittest discover -s tests -v
```

---

## License

MIT
