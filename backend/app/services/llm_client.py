"""
Shared LLM client helper
=========================
Small, provider-agnostic wrapper around the Gemini -> OpenAI fallback chain
used across the codebase (originally inlined in smart_summary.py's
_optional_llm_summary). Centralised here so new features (e.g. transcript
translation) don't duplicate the same provider-fallback logic a third time.

Env vars:
  GEMINI_API_KEY — primary provider (gemini-flash-latest)
  OPENAI_API_KEY — fallback provider (gpt-4o-mini)
"""
from __future__ import annotations

import os

# "-latest" alias, not a pinned version: Google retires specific dated/numbered
# Gemini models over time (gemini-1.5-flash returned 404 "not found for
# generateContent" once retired), and this alias always resolves to whatever
# the current recommended flash model is, so this stops breaking every time
# Google rotates versions.
_GEMINI_MODEL = "gemini-flash-latest"


def call_llm(prompt: str, max_output_tokens: int = 2048) -> str | None:
    """
    Call Gemini (gemini-flash-latest) first, falling back to OpenAI
    (gpt-4o-mini) if Gemini is unavailable or fails. Returns None if both are
    unavailable or fail (never raises).
    """
    # --- Try Gemini ---
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            import google.generativeai as genai  # type: ignore

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel(
                model_name=_GEMINI_MODEL,
                generation_config={"max_output_tokens": max_output_tokens},
            )
            response = model.generate_content(prompt)
            text = response.text.strip() if response.text else ""
            if text:
                return text
        except Exception:
            pass

    # --- Try OpenAI ---
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        try:
            import openai as _openai  # type: ignore

            client = _openai.OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_output_tokens,
            )
            text = (
                response.choices[0].message.content.strip()
                if response.choices and response.choices[0].message.content
                else ""
            )
            if text:
                return text
        except Exception:
            pass

    return None
