from __future__ import annotations

from dataclasses import dataclass


DEFAULT_MASTER_PROMPT = """You are an executive assistant drafting email replies.

Rules:
- Reply in ENGLISH.
- Write only the email body (no subject line).
- Match the sender's intent and the thread context.
- Be concise, specific, and helpful.
- If details are missing, ask 1-3 targeted questions.
- Do NOT invent facts. If unsure, say so briefly.
"""


@dataclass(frozen=True)
class PromptParts:
    master: str
    thread_context: str
    latest_email: str


def build_prompt(parts: PromptParts) -> str:
    blocks = [
        parts.master.strip(),
        "",
        "THREAD CONTEXT (most recent last):",
        (parts.thread_context or "(none)").strip(),
        "",
        "LATEST EMAIL (reply to this):",
        parts.latest_email.strip(),
        "",
        "DRAFT REPLY:",
    ]
    return "\n".join(blocks).strip() + "\n"
