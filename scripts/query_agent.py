"""Query the deployed corpus-only ADK agent (or a local one) and render citations.

Reads ``AGENT_ENGINE_ID`` from ``.env`` and calls ``stream_query`` against the
deployed agent. The agent now appends a "Source excerpts:" section to its own
answer (see ``src/agent/rendering.py``), so for plain output this script just
prints what the agent returned. ``--structured`` rebuilds a JSON envelope from
the retrieve tool's function_response (with the answer stripped of the excerpt
block). A fallback re-renders excerpts client-side if the deployed agent
predates the server-side change.

Usage:
    python scripts/query_agent.py "What is X?"
    python scripts/query_agent.py "What is X?" --structured

Required env vars: GOOGLE_CLOUD_PROJECT, AGENT_ENGINE_LOCATION, AGENT_ENGINE_ID.
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import vertexai
from dotenv import load_dotenv
from vertexai import agent_engines

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / ".env"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        logger.error(f"Missing required env var: {name}")
        sys.exit(1)
    return value


def _extract_function_response_chunks(event: dict[str, Any]) -> list[dict] | None:
    content = event.get("content") if isinstance(event, dict) else None
    if not isinstance(content, dict):
        return None
    parts = content.get("parts") or []
    for part in parts:
        if not isinstance(part, dict):
            continue
        fr = part.get("function_response")
        if not isinstance(fr, dict):
            continue
        if fr.get("name") != "retrieve_rag_documentation":
            continue
        response = fr.get("response")
        if isinstance(response, dict) and "chunks" in response:
            return response.get("chunks") or []
    return None


def _extract_text(event: dict[str, Any]) -> str:
    content = event.get("content") if isinstance(event, dict) else None
    if not isinstance(content, dict):
        return ""
    if content.get("role") not in (None, "model"):
        return ""
    parts = content.get("parts") or []
    fragments = []
    for part in parts:
        if isinstance(part, dict):
            text = part.get("text")
            if text:
                fragments.append(text)
    return "".join(fragments)


_EXCERPT_MAX_CHARS = 600
# Must match rendering.EXCERPTS_HEADER in the agent. Used to detect/strip the
# excerpts the deployed agent now appends itself, so the CLI neither
# double-renders them (plain) nor leaks them into the JSON answer (structured).
_EXCERPTS_HEADER = "Source excerpts:"


_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


def _strip_excerpts(answer: str) -> str:
    """Return just the model's answer, dropping any agent-appended excerpts."""
    marker = f"\n\n{_EXCERPTS_HEADER}"
    idx = answer.find(marker)
    return answer[:idx].rstrip() if idx != -1 else answer


def _cited_indices(answer: str) -> set[int]:
    return {int(m) for m in _CITATION_PATTERN.findall(answer)}


def _render_plain(answer: str, chunks: list[dict]) -> str:
    if not chunks:
        return answer
    cited = _cited_indices(answer)
    shown = [c for c in chunks if c.get("index") in cited] if cited else chunks
    if not shown:
        shown = chunks
    lines = [answer, "", _EXCERPTS_HEADER]
    for c in shown:
        idx = c.get("index")
        title = c.get("source_display_name") or c.get("source_uri") or "(unknown)"
        uri = c.get("source_uri") or ""
        score = c.get("score")
        score_str = (
            f" vector_distance={score:.3f}" if isinstance(score, (int, float)) else ""
        )
        text = (c.get("text") or "").strip()
        if len(text) > _EXCERPT_MAX_CHARS:
            text = text[:_EXCERPT_MAX_CHARS].rstrip() + "..."
        lines.append("")
        lines.append(f"[{idx}] {title}{score_str}")
        if uri and uri != title:
            lines.append(f"    {uri}")
        if text:
            quoted = "\n".join(f"    > {ln}" for ln in text.split("\n") if ln)
            lines.append(quoted)
    return "\n".join(lines)


def _render_structured(answer: str, chunks: list[dict]) -> str:
    return json.dumps(
        {
            "answer": answer,
            "citations": [
                {
                    "index": c.get("index"),
                    "source_uri": c.get("source_uri"),
                    "source_display_name": c.get("source_display_name"),
                    "score": c.get("score"),
                    "text": c.get("text"),
                }
                for c in chunks
            ],
        },
        indent=2,
    )


def main() -> None:
    load_dotenv(_ENV_FILE)

    parser = argparse.ArgumentParser(
        description="Query the deployed corpus-only ADK agent."
    )
    parser.add_argument("query", help="The question to ask the agent.")
    parser.add_argument(
        "--structured",
        action="store_true",
        help="Emit JSON with {answer, citations[]} instead of plain text.",
    )
    parser.add_argument(
        "--user-id",
        default="cli-user",
        help="User identifier for session tracking (default: cli-user).",
    )
    args = parser.parse_args()

    project_id = _require_env("GOOGLE_CLOUD_PROJECT")
    location = _require_env("AGENT_ENGINE_LOCATION")
    engine_id = _require_env("AGENT_ENGINE_ID").strip().strip("'\"")

    # SDK quirk: agent_engines.get() runs the input through validate_id which
    # rejects full resource paths even though the docstring says it accepts them.
    # Strip to the trailing numeric ID.
    if "/" in engine_id:
        engine_id = engine_id.rsplit("/", 1)[-1]

    vertexai.init(project=project_id, location=location)
    remote_app = agent_engines.get(engine_id)

    # Session must exist server-side before stream_query can use it; the SDK
    # doesn't auto-create. Create one for this run.
    session = remote_app.create_session(user_id=args.user_id)  # type: ignore[attr-defined]
    session_id = session["id"] if isinstance(session, dict) else session.id

    final_chunks: list[dict] = []
    answer_fragments: list[str] = []

    for event in remote_app.stream_query(  # type: ignore[attr-defined]
        message=args.query,
        user_id=args.user_id,
        session_id=session_id,
    ):
        chunks = _extract_function_response_chunks(event)
        if chunks is not None:
            final_chunks = chunks

        text = _extract_text(event)
        if text and not event.get("partial"):
            answer_fragments.append(text)

    answer = "".join(answer_fragments).strip()

    if args.structured:
        # Build JSON from the captured chunks; strip any excerpt block the agent
        # appended so it doesn't end up duplicated inside the "answer" field.
        print(_render_structured(_strip_excerpts(answer), final_chunks))
    elif _EXCERPTS_HEADER in answer or not final_chunks:
        # The deployed agent already rendered excerpts (or there are none) —
        # print its output verbatim.
        print(answer)
    else:
        # Fallback for an agent deployed before excerpts moved server-side.
        print(_render_plain(answer, final_chunks))


if __name__ == "__main__":
    main()
