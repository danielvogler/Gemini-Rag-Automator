"""ADK callbacks for the corpus-only RAG agent.

The model decides on each turn whether to call `retrieve_rag_documentation`
(per the system instruction). The callbacks here only do work when retrieval
actually happens, so conversational turns pass through untouched.

* ``capture_retrieval_after_tool`` (after_tool_callback) - stashes the
  retrieved chunks on session state for later citation rendering, and writes
  an explicit ``must_abstain`` sentinel into the tool result when retrieval
  is empty so the model can't hallucinate around it.
* ``validate_answer_after_model`` (after_model_callback) - runs ONLY when a
  retrieval call happened this turn. If the model's text answer is not
  supported by the retrieved chunks, rewrite it to the literal abstain
  string. Otherwise it appends the retrieved source excerpts to the answer
  (see ``rendering.py``) so they are part of the agent's own output.
  Conversational answers (no retrieval) pass through. Note: abstain answers
  get NO excerpts, which keeps out-of-corpus replies clean.

``temp:`` prefixed state keys are reset per invocation by ADK so turn N's
answer can't be validated against turn N-1's chunks.
"""

import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .prompts import ABSTAIN_MESSAGE
from .rendering import render_answer


_TOOL_NAME = "retrieve_rag_documentation"
_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
_STATE_RETRIEVAL_ATTEMPTED = "temp:retrieval_attempted"
_STATE_RETRIEVAL_EMPTY = "temp:retrieval_empty"
_STATE_CITATIONS = "temp:citations"


def capture_retrieval_after_tool(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: dict,
) -> dict | None:
    if tool.name != _TOOL_NAME:
        return None

    chunks = tool_response.get("chunks") if isinstance(tool_response, dict) else None
    tool_context.state[_STATE_RETRIEVAL_ATTEMPTED] = True

    if not chunks:
        tool_context.state[_STATE_CITATIONS] = []
        tool_context.state[_STATE_RETRIEVAL_EMPTY] = True
        return {
            "chunks": [],
            "empty": True,
            "must_abstain": True,
            "abstain_message": ABSTAIN_MESSAGE,
        }

    tool_context.state[_STATE_CITATIONS] = chunks
    tool_context.state[_STATE_RETRIEVAL_EMPTY] = False
    return None


def _answer_supported_by_chunks(answer: str, chunks: list[dict]) -> bool:
    if not chunks:
        return False
    cited_indices = {int(m) for m in _CITATION_PATTERN.findall(answer)}
    valid_indices = {c["index"] for c in chunks}
    if cited_indices and cited_indices.issubset(valid_indices):
        return True

    answer_tokens = {t for t in re.findall(r"[A-Za-z0-9]{4,}", answer.lower())}
    if len(answer_tokens) < 3:
        return False
    chunk_tokens: set[str] = set()
    for c in chunks:
        chunk_tokens.update(re.findall(r"[A-Za-z0-9]{4,}", c.get("text", "").lower()))
    overlap = answer_tokens & chunk_tokens
    return len(overlap) / max(len(answer_tokens), 1) >= 0.4


def validate_answer_after_model(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    if getattr(llm_response, "partial", False):
        return None
    if llm_response is None or llm_response.content is None:
        return None
    parts = list(llm_response.content.parts or [])
    if not parts:
        return None
    if any(getattr(p, "function_call", None) is not None for p in parts):
        return None

    text = "".join(getattr(p, "text", "") or "" for p in parts).strip()
    if not text:
        return None
    if text == ABSTAIN_MESSAGE:
        return None

    state = callback_context.state

    # Conversational turn (no retrieval this invocation) - let through.
    if not state.get(_STATE_RETRIEVAL_ATTEMPTED):
        return None

    if state.get(_STATE_RETRIEVAL_EMPTY):
        return _abstain_response()

    chunks = state.get(_STATE_CITATIONS) or []
    if not _answer_supported_by_chunks(text, chunks):
        return _abstain_response()

    # Grounded, supported answer: emit the retrieved source excerpts as part of
    # the agent's own response so they show up in any consumer (CLI *and* the
    # Agent Engine Playground UI, which only renders the agent's final message).
    rendered = render_answer(text, chunks)
    if rendered == text:
        return None
    return _text_response(rendered)


def _abstain_response() -> LlmResponse:
    return _text_response(ABSTAIN_MESSAGE)


def _text_response(text: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text=text)],
        )
    )
