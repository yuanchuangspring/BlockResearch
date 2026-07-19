<p align="right">
  <a href="README_CN.md">中文</a>
</p>

<p align="center">
  <img src="static/title.png" alt="BlockResearch" height="48">
</p>

<p align="center">
  <strong>Research While Building</strong><br>
  <sub>Stage-wise dynamic graph construction for evidence-grounded deep research</sub>
</p>

<p align="center">
  <img src="static/%E5%9B%BE%E7%89%871.png" alt="BlockResearch Concept" width="680">
</p>

---

**BlockResearch** is a deep research agent that does not follow a fixed plan. At each stage, it reads the complete research state — verified claims, unverified leads, candidate hypotheses, contradictions, and rejected answers — and constructs a fresh executable graph from scratch. The shape of the research adapts to what has been learned.

> The structure of research should not be determined before it begins — it emerges as evidence accumulates.

---

## Installation

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Configure your API keys in `.env`:

```dotenv
OPENAI_API_KEY=...
OPENAI_ENDPOINT=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-v4-flash
BRAVE_API_KEY=...
```

## Usage

**CLI Research**

```bash
.venv/bin/python -m src.main "your research question"
```

**Web Demo**

```bash
.venv/bin/uvicorn src.web:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` for an interactive demo with real-time execution graph visualization.

**Tests**

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## License

MIT
