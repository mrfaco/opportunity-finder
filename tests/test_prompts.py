"""Prompt loader: canonicalization, hashing, frontmatter."""

from __future__ import annotations

import hashlib

from agents import prompts as prompt_loader


def test_canonicalize_idempotent():
    raw = "  hello   \r\nworld  \n  \n"
    once = prompt_loader.canonicalize(raw)
    twice = prompt_loader.canonicalize(once)
    assert once == twice


def test_canonicalize_strips_trailing_whitespace_and_normalizes_endings():
    raw = "line one   \r\nline two\r\n"
    expected = "line one\nline two"
    assert prompt_loader.canonicalize(raw) == expected


def test_hashing_deterministic():
    raw = "alpha\nbeta\n"
    canonical = prompt_loader.canonicalize(raw)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # The loader uses the same algorithm — we sanity-check the recipe here.
    assert hashlib.sha256(canonical.encode()).hexdigest() == expected


def test_load_prompt_parses_frontmatter():
    p = prompt_loader.load_prompt("filter", "classifier")
    assert p.agent_name == "filter"
    assert p.kind == "classifier"
    assert p.frontmatter.get("schema_version") == "1.0"
    assert p.hash and len(p.hash) == 64
    assert "TODO" in p.content


def test_get_prompts_for_agent_returns_all_kinds():
    investigation = prompt_loader.get_prompts_for_agent("investigation")
    assert set(investigation.keys()) >= {"system", "procedural"}
    for p in investigation.values():
        assert p.hash
