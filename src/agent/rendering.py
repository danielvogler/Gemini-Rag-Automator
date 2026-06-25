"""Render a grounded answer together with the retrieved source excerpts.

These helpers run inside the deployed agent (via ``after_model_callback``), so
the excerpts become part of the agent's own response text. That way every
consumer sees them — the CLI (``scripts/query_agent.py``) *and* the Vertex AI
Agent Engine Playground UI, which only ever shows the agent's final message.

Kept deliberately free of ``google.adk`` imports so the rendering logic is
unit-testable on its own.

Output format is controlled at deploy time:
* ``AGENT_STRUCTURED_OUTPUT=0`` (default) -> plain text answer + a
  ``Source excerpts:`` section.
* ``AGENT_STRUCTURED_OUTPUT=1`` -> a JSON envelope ``{answer, citations[]}``.

Per-excerpt length is capped by ``AGENT_EXCERPT_MAX_CHARS`` (default 600; set
to 0 to disable truncation).
"""

import json
import os

_DEFAULT_EXCERPT_MAX_CHARS = 600

# Marker used to separate the model's answer from the appended excerpts. The CLI
# relies on this to recover the clean answer when building structured output.
EXCERPTS_HEADER = "Source excerpts:"


def excerpt_max_chars() -> int:
    """Per-excerpt character cap from ``AGENT_EXCERPT_MAX_CHARS`` (0 = no cap)."""
    raw = os.environ.get("AGENT_EXCERPT_MAX_CHARS", str(_DEFAULT_EXCERPT_MAX_CHARS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_EXCERPT_MAX_CHARS
    return max(value, 0)


def structured_output_enabled() -> bool:
    """Whether the agent should emit a JSON envelope instead of plain text."""
    return os.environ.get("AGENT_STRUCTURED_OUTPUT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def citation_label(chunk: dict) -> str:
    """Human-readable source label for a chunk.

    Prefers extracted paper metadata (title/authors/journal) when present and
    falls back to the display name or URI. The metadata fields are populated by
    the paper-metadata lookup (see ``src/agent/tools.py``); when absent this
    degrades gracefully to the filename.
    """
    title = chunk.get("title")
    if title:
        label = title
        authors = chunk.get("authors") or []
        if authors:
            label += f" \u2014 {', '.join(authors)}"
        journal = chunk.get("journal")
        if journal:
            label += f" ({journal})"
        return label
    return chunk.get("source_display_name") or chunk.get("source_uri") or "(unknown)"


def render_plain(answer: str, chunks: list[dict]) -> str:
    """Append a ``Source excerpts:`` section to ``answer`` for the given chunks."""
    if not chunks:
        return answer

    max_chars = excerpt_max_chars()
    lines = [answer, "", EXCERPTS_HEADER]
    for c in chunks:
        idx = c.get("index")
        label = citation_label(c)
        uri = c.get("source_uri") or ""
        score = c.get("score")
        score_str = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
        text = (c.get("text") or "").strip()
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        lines.append("")
        lines.append(f"[{idx}] {label}{score_str}")
        if uri and uri != label:
            lines.append(f"    {uri}")
        if text:
            quoted = "\n".join(f"    > {ln}" for ln in text.split("\n") if ln)
            lines.append(quoted)
    return "\n".join(lines)


def render_structured(answer: str, chunks: list[dict]) -> str:
    """JSON envelope with the answer and full (untruncated) citation chunks."""
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


def render_answer(answer: str, chunks: list[dict]) -> str:
    """Render ``answer`` + ``chunks`` per the deploy-time output format."""
    if structured_output_enabled():
        return render_structured(answer, chunks)
    return render_plain(answer, chunks)
