"""LLM client with explicit reasoning, answer and JSON boundaries."""
import os, json, asyncio, re
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_client = None

def _get():
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_ENDPOINT", "https://api.openai.com/v1"),
            timeout=float(os.environ.get("LLM_TIMEOUT", 90)))
    return _client

def _reset():
    global _client; _client = None

def _parse_think_answer(text: str) -> tuple[str, str]:
    """从 <think>...</think><answer>...</answer> 中提取思维链和答案。"""
    think = ""
    m_think = re.search(r'<think>(.*?)</think>', text, re.DOTALL)
    if m_think:
        think = m_think.group(1).strip()
    m_ans = re.search(r'<answer>(.*?)</answer>', text, re.DOTALL)
    ans = m_ans.group(1).strip() if m_ans else re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    return think, ans

def _json_object(text: str):
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[i:])
                if isinstance(value, dict): return value
            except json.JSONDecodeError:
                pass
    return None

async def ask(system: str, user: str, model=None, max_tokens=8192, retries=2) -> tuple[str, str]:
    """返回 (thinking, answer)。"""
    model = model or os.environ.get("OPENAI_MODEL", "deepseek-v4-flash")
    last_err, budget = None, max_tokens
    for attempt in range(retries):
        try:
            resp = await _get().chat.completions.create(
                model=model, max_tokens=budget,
                messages=[{"role": "system", "content": system},
                         {"role": "user", "content": user}])
            choice = resp.choices[0]
            msg = choice.message
            content = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", None) or ""
            if content:
                embedded, answer = _parse_think_answer(content)
                if answer:
                    thinking = "\n".join(x for x in (reasoning, embedded) if x)
                    return thinking, answer
            finish = choice.finish_reason or "unknown"
            last_err = RuntimeError(f"model produced no final answer (finish_reason={finish})")
            if finish == "length":
                budget = min(max(budget * 2, 1024), 32768)
            raise last_err
        except (APIConnectionError, APITimeoutError) as e:
            last_err = e; _reset()
            if attempt < retries - 1: await asyncio.sleep(1 + attempt)
        except Exception as e:
            last_err = e
            if attempt < retries - 1: await asyncio.sleep(1 + attempt)
    raise last_err

async def ask_json(system: str, user: str, model=None, max_tokens=4096) -> dict:
    """Return a JSON object; retry malformed output instead of hiding it as {}."""
    suffix = "请只在 <answer> 标签中输出一个 JSON 对象。"
    for attempt in range(2):
        instruction = suffix if not attempt else suffix + " 上次格式无效；不要输出解释或 Markdown。"
        _, answer = await ask(system, f"{user}\n\n{instruction}", model, max_tokens)
        value = _json_object(answer)
        if value:
            return value
    raise ValueError("model returned no valid JSON object")
