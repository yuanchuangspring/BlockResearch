<p align="right"><a href="README.md">English</a></p>

<p align="center">
  <img src="static/title.png" alt="BlockResearch" height="48">
</p>

<h1 align="center">BlockResearch</h1>

<p align="center">
  <strong>边研究，边搭建</strong><br>
  <sub>逐阶段动态图构造，证据驱动的深度研究框架</sub>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License"></a>
</p>

<p align="center">
  <img src="static/%E5%9B%BE%E7%89%871.png" alt="BlockResearch 概念图" width="680">
</p>

---

## 这是什么？

BlockResearch 是一个深度研究 Agent，它**不依赖固定计划**。每个阶段，系统读取当前全部研究状态——已验证的事实、未验证的线索、候选假设、矛盾与被否决的答案——从零构造一张**新的可执行图**。研究的形状随证据积累而逐渐成形，不由预设流程决定。

> *研究的形状，在过程中成形。*

---

## 快速开始

```bash
git clone https://github.com/yuanchuangspring/BlockResearch.git
cd BlockResearch
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

创建 `.env` 文件，填入 API 密钥：

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

打开 `http://localhost:8000`，输入问题，观察执行图实时构建。

**测试**

```bash
.venv/bin/python -m unittest discover -s tests -v
```

---

## License

MIT
