"""FastAPI demo server with request-local credentials and SSE progress."""
import asyncio, json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .recorder import reset_sink, set_sink
from .research import research
from .runtime import configure, reset

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
app = FastAPI(title="BlockResearch Demo")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=12000)
    max_stages: int = Field(default=6, ge=1, le=12)
    config: dict[str, str] = Field(default_factory=dict)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/research")
async def run_research(body: ResearchRequest):
    queue = asyncio.Queue()

    async def runner():
        config_token = configure(body.config)
        sink_token = set_sink(queue.put_nowait)
        try:
            result = await research(body.question, body.max_stages)
            await queue.put({"kind": "final", "result": result})
        except Exception as exc:
            await queue.put({"kind": "fatal", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            reset_sink(sink_token)
            reset(config_token)
            await queue.put(None)

    async def stream():
        task = asyncio.create_task(runner())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

