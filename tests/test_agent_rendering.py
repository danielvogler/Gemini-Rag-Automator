import json

from agent.rendering import (
    EXCERPTS_HEADER,
    citation_label,
    excerpt_max_chars,
    render_answer,
    render_plain,
    render_structured,
    structured_output_enabled,
)


def _chunk(**overrides):
    base = {
        "index": 1,
        "text": "Geothermal energy is heat from the Earth.",
        "source_uri": "gs://bucket/paper.pdf",
        "source_display_name": "paper.pdf",
        "score": 0.42,
    }
    base.update(overrides)
    return base


def test_render_plain_appends_excerpts_section():
    out = render_plain("Heat from the Earth [1].", [_chunk()])
    assert out.startswith("Heat from the Earth [1].")
    assert EXCERPTS_HEADER in out
    assert "[1] paper.pdf vector_distance=0.420" in out
    assert "> Geothermal energy is heat from the Earth." in out


def test_render_plain_shows_only_cited_chunks():
    chunks = [
        _chunk(index=1, source_display_name="a.pdf"),
        _chunk(index=2, source_display_name="b.pdf"),
        _chunk(index=3, source_display_name="c.pdf"),
    ]
    out = render_plain("Only the first and third [1][3].", chunks)
    assert "[1] a.pdf" in out
    assert "[3] c.pdf" in out
    assert "b.pdf" not in out


def test_render_plain_falls_back_to_all_when_no_citations():
    chunks = [
        _chunk(index=1, source_display_name="a.pdf"),
        _chunk(index=2, source_display_name="b.pdf"),
    ]
    out = render_plain("An answer with no bracket citations.", chunks)
    assert "a.pdf" in out
    assert "b.pdf" in out


def test_render_plain_without_chunks_returns_answer_unchanged():
    assert render_plain("Just an answer.", []) == "Just an answer."


def test_excerpt_truncation_respects_env(monkeypatch):
    monkeypatch.setenv("AGENT_EXCERPT_MAX_CHARS", "10")
    assert excerpt_max_chars() == 10
    out = render_plain("a", [_chunk(text="x" * 50)])
    assert "..." in out
    assert "x" * 50 not in out


def test_excerpt_truncation_disabled_with_zero(monkeypatch):
    monkeypatch.setenv("AGENT_EXCERPT_MAX_CHARS", "0")
    long_text = "y" * 2000
    out = render_plain("a", [_chunk(text=long_text)])
    assert long_text in out


def test_excerpt_max_chars_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("AGENT_EXCERPT_MAX_CHARS", "not-a-number")
    assert excerpt_max_chars() == 600


def test_citation_label_prefers_paper_metadata():
    label = citation_label(
        _chunk(
            title="Geothermal Potential",
            authors=["A. One", "B. Two"],
            journal="J. Renewable Energy",
        )
    )
    assert label == "Geothermal Potential \u2014 A. One, B. Two (J. Renewable Energy)"


def test_citation_label_falls_back_to_display_name():
    assert citation_label(_chunk()) == "paper.pdf"


def test_structured_output_enabled_toggle(monkeypatch):
    monkeypatch.setenv("AGENT_STRUCTURED_OUTPUT", "1")
    assert structured_output_enabled() is True
    monkeypatch.setenv("AGENT_STRUCTURED_OUTPUT", "0")
    assert structured_output_enabled() is False


def test_render_structured_is_valid_json_with_citations():
    payload = json.loads(render_structured("answer", [_chunk()]))
    assert payload["answer"] == "answer"
    assert payload["citations"][0]["source_uri"] == "gs://bucket/paper.pdf"
    assert payload["citations"][0]["text"].startswith("Geothermal")


def test_render_answer_uses_structured_when_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_STRUCTURED_OUTPUT", "1")
    payload = json.loads(render_answer("answer", [_chunk()]))
    assert payload["answer"] == "answer"


def test_render_answer_plain_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_STRUCTURED_OUTPUT", raising=False)
    out = render_answer("answer [1]", [_chunk()])
    assert EXCERPTS_HEADER in out
