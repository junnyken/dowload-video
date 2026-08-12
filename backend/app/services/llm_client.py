"""
Shared LLM client helper
=========================
Small, provider-agnostic wrapper around the Gemini -> OpenAI fallback chain
used across the codebase (originally inlined in smart_summary.py's
_optional_llm_summary). Centralised here so new features (e.g. transcript
translation) don't duplicate the same provider-fallback logic a third time.

Env vars:
  GEMINI_API_KEY — primary provider (gemini-1.5-flash)
  OPENAI_API_KEY — fallback provider (gpt-4o-mini)
"""
from __future__ import annotations

import os


def call_llm(prompt: str, max_output_tokens: int = 2048) -> str | None:
    """
    Call Gemini (gemini-1.5-flash) first, falling back to OpenAI (gpt-4o-mini)
    if Gemini is unavailable or fails. Returns None if both are unavailable
    or fail (never raises).
    """
    # --- Try Gemini ---
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            import google.generativeai as genai  # type: ignore

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                # NOTE: "thinking_budget" is not a valid key for this SDK — that
                # only exists in the newer unified google-genai SDK's
                # ThinkingConfig (Gemini 2.x+). This is the older
                # google-generativeai package, and gemini-1.5-flash has no
                # thinking mode to budget in the first place.
                generation_config={"max_output_tokens": max_output_tokens},
            )
            response = model.generate_content(prompt)
            text = response.text.strip() if response.text else ""
            if text:
                return text
            print(f"[DIAG-llm] Gemini returned empty text. response={response!r}")
        except Exception as exc:
            import traceback as _tb
            print(f"[DIAG-llm] Gemini call failed: {type(exc).__name__}: {exc}")
            print(_tb.format_exc())

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
