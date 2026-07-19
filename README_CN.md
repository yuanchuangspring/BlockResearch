<p align="right">
  <a href="README.md">English</a>
</p>

<p align="center">
  <img src="static/title.png" alt="BlockResearch" height="48">
</p>

<p align="center">
  <strong>边研究，边搭建</strong><br>
  <sub>逐阶段动态图构造，证据驱动的深度研究框架</sub>
</p>

<p align="center">
  <img src="static/%E5%9B%BE%E7%89%871.png" alt="BlockResearch 概念图" width="680">
</p>

---

**BlockResearch** 是一个不依赖固定计划的深度研究 Agent。每个阶段，它读取当前全部研究状态——已验证的事实、未验证的线索、候选假设、矛盾与被否决的答案——从零构造一张新的可执行图。研究的形状随证据积累而逐渐成形。

> 研究的形状，在过程中成形。

---

## 安装

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

在 `.env` 中配置 API 密钥：

```dotenv
OPENAI_API_KEY=...
OPENAI_ENDPOINT=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-v4-flash
BRAVE_API_KEY=...
```

## 使用

**命令行研究**

```bash
.venv/bin/python -m src.main "你的研究问题"
```

**可视化 Demo**

```bash
.venv/bin/uvicorn src.web:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000` 体验实时执行图可视化。

**测试**

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## License

MIT
