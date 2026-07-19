"""LLM client with explicit reasoning, answer and JSON boundaries."""
import os, json, asyncio, re
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_client = None
_new_client = None

def _alternate(model: str) -> bool:
    """Route non-DeepSeek model families through the optional second endpoint."""
    return bool(os.environ.get("NEW_API_KEY") and os.environ.get("NEW_BASE_URL")
                and model.startswith(("gpt-", "claude-", "gemini-")))

def _get(model: str):
    global _client, _new_client
    alternate = _alternate(model)
    name = "_new_client" if alternate else "_client"
    client = globals()[name]
    if client is None:
        client = AsyncOpenAI(
            api_key=os.environ["NEW_API_KEY" if alternate else "OPENAI_API_KEY"],
            base_url=os.environ.get(
                "NEW_BASE_URL" if alternate else "OPENAI_ENDPOINT",
                "https://api.openai.com/v1"),
            timeout=float(os.environ.get("LLM_TIMEOUT", 90)),
            # Retries are owned by ask(); SDK defaults would silently turn one
            # 90-second request into three attempts.
            max_retries=0)
        globals()[name] = client
    return client

def _reset(model: str):
    global _client, _new_client
    if _alternate(model): _new_client = None
    else: _client = None


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
    retries = 1 if _alternate(model) else retries
    last_err, budget = None, max_tokens
    for attempt in range(retries):
        try:
            params = {
                "model": model, "max_tokens": budget,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            }
            if _alternate(model):
                params["reasoning_effort"] = os.environ.get("REASONING_EFFORT", "low")
            request = _get(model).chat.completions.create(**params)
            resp = await (asyncio.wait_for(request, float(os.environ.get("LLM_HARD_TIMEOUT", 120)))
                          if _alternate(model) else request)
            choice = resp.choices[0]
            msg = choice.message
            content = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", None) or ""
            finish = choice.finish_reason or "unknown"
            if content:
                embedded, answer = _parse_think_answer(content)
                if answer:
                    thinking = "\n".join(x for x in (reasoning, embedded) if x)
                    return thinking, answer
            last_err = RuntimeError(f"model produced no final answer (finish_reason={finish})")
            if finish == "length":
                budget = min(max(budget * 2, 1024), 32768)
            raise last_err
        except (APIConnectionError, APITimeoutError) as e:
            last_err = e; _reset(model)
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
