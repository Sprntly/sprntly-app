from anthropic import Anthropic
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings

router = APIRouter(tags=["chat"])

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    system: str | None = None
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024


@router.post("/chat")
def chat(req: ChatRequest):
    client = get_client()
    msg = client.messages.create(
        model=req.model,
        max_tokens=req.max_tokens,
        system=req.system or "You are Sprintly, an AI assistant for product managers.",
        messages=[m.model_dump() for m in req.messages],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    return {"text": text, "stop_reason": msg.stop_reason, "usage": msg.usage.model_dump()}


@router.post("/chat/stream")
def chat_stream(req: ChatRequest):
    client = get_client()

    def event_stream():
        with client.messages.stream(
            model=req.model,
            max_tokens=req.max_tokens,
            system=req.system or "You are Sprintly, an AI assistant for product managers.",
            messages=[m.model_dump() for m in req.messages],
        ) as stream:
            for text in stream.text_stream:
                yield f"data: {text}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
