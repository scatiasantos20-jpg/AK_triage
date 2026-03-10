from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MailProfile:
    name: str
    credentials_path: str = "credentials.json"
    token_path: str = "token.json"
    gmail_query: str | None = None
    batch_limit: int | None = None
    draft_signature: str | None = None


def load_profiles(path: str) -> dict[str, MailProfile]:
    p = Path(path)
    if not p.exists():
        return {}

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid profiles JSON in {path}: {exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid profiles format in {path}: expected top-level object/dict.")

    out: dict[str, MailProfile] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        out[str(name)] = MailProfile(
            name=str(name),
            credentials_path=str(cfg.get("credentials_path") or "credentials.json"),
            token_path=str(cfg.get("token_path") or "token.json"),
            gmail_query=cfg.get("gmail_query"),
            batch_limit=cfg.get("batch_limit"),
            draft_signature=cfg.get("draft_signature"),
        )
    return out


def get_profile(path: str, name: str) -> MailProfile | None:
    return load_profiles(path).get(name)


def get_profile_or_raise(path: str, name: str) -> MailProfile:
    profile = get_profile(path, name)
    if profile is None:
        raise SystemExit(f"Profile '{name}' not found in {path}.")
    return profile
