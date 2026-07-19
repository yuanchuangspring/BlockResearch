<p align="right"><a href="README_CN.md">中文</a></p>

<p align="center">
  <img src="static/title.png" alt="BlockResearch" height="48">
</p>

<h1 align="center">BlockResearch</h1>

<p align="center">
  <strong>Research While Building</strong><br>
  <sub>Stage-wise dynamic graph construction for evidence-grounded deep research</sub>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License"></a>
</p>

<p align="center">
  <img src="static/%E5%9B%BE%E7%89%871.png" alt="BlockResearch Concept" width="680">
</p>

---

## What is BlockResearch?

BlockResearch is a deep research agent that does **not** follow a fixed plan. At each stage, it reads the complete research state — verified claims, unverified leads, candidate hypotheses, contradictions, and rejected answers — and constructs a **fresh executable graph from scratch**. The shape of the research adapts to what has been learned, not to a predetermined workflow.

> *The structure of research emerges as evidence accumulates — it should not be hard-coded before the task begins.*

---

## Quick Start

```bash
git clone https://github.com/yuanchuangspring/BlockResearch.git
cd BlockResearch
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env` file with your API keys:

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_ENDPOINT=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-v4-flash
BRAVE_API_KEY=...
```

## Usage

**Command Line**

```bash
.venv/bin/python -m src.main "Which song was described in 2017 as the most beautiful piece of music?"
```

**Web Demo**

```bash
.venv/bin/uvicorn src.web:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` — type a question and watch the execution graph build in real time.

**Tests**

```bash
.venv/bin/python -m unittest discover -s tests -v
```

---

## License

MIT
