"""BlockResearch CLI + 公共接口。"""
import asyncio, re, sys
from .research import research

def extract_answer(text: str, question: str = "") -> str:
    m = re.search(r'(?:FINAL\s*)?(?:ANSWER|答案)\s*[：:]\s*(.+)', text, re.IGNORECASE)
    if m: return m.group(1).strip().rstrip('.').strip()[:200]
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return lines[-1][:200] if lines else text.strip()[:200]

async def main():
    q = sys.argv[1] if len(sys.argv) > 1 else "2025年诺贝尔物理学奖获得者是谁？"
    result = await research(q)
    print(f"\n答案: {extract_answer(result['answer'], q)}")

if __name__ == "__main__":
    asyncio.run(main())
