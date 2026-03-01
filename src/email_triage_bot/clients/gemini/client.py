from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from google import genai
from google.genai import types, errors
import time


class GeminiRateLimitExceeded(RuntimeError):
    """Raised when Gemini free-tier rate limit/quota is exceeded."""


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str
    model: str = "gemini-2.5-flash"
    api_version: str = "v1beta"
    timeout_s: int = 60
    max_retries: int = 2
    retry_backoff_s: float = 1.5


class GeminiClient:
    def __init__(self, cfg: GeminiConfig):
        self.cfg = cfg
        self._client: Optional[genai.Client] = None

    def __enter__(self) -> "GeminiClient":
        timeout_s = max(10, int(self.cfg.timeout_s or 60))
        # python-genai HttpOptions.timeout is milliseconds.
        timeout_ms = max(10_000, timeout_s * 1000)
        http = types.HttpOptions(api_version=self.cfg.api_version, timeout=timeout_ms)
        self._client = genai.Client(api_key=self.cfg.api_key, http_options=http)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._client:
                self._client.close()
        finally:
            self._client = None

    @property
    def client(self) -> genai.Client:
        assert self._client is not None
        return self._client

    def generate(self, prompt: str) -> str:
        prompt = (prompt or '').strip()
        if not prompt:
            raise ValueError('Prompt is empty.')

        attempts = 0
        while True:
            try:
                resp = self.client.models.generate_content(model=self.cfg.model, contents=prompt)
                txt = getattr(resp, 'text', None)
                if txt:
                    return txt.strip() + '\n'
                return ''
            except errors.APIError as e:
                msg = (getattr(e, 'message', '') or '').upper()
                code = getattr(e, 'code', None)
                if code == 429 or 'RESOURCE_EXHAUSTED' in msg or 'RATE LIMIT' in msg or 'QUOTA' in msg:
                    raise GeminiRateLimitExceeded(f"{code} {getattr(e, 'message', '')}") from e
                raise
            except Exception as e:
                name = type(e).__name__
                m = str(e).lower()
                is_timeout = ('timeout' in m) or (name in {'ReadTimeout','ConnectTimeout','TimeoutException'})
                if not is_timeout:
                    raise
                if attempts >= int(self.cfg.max_retries):
                    raise
                attempts += 1
                sleep_s = float(self.cfg.retry_backoff_s) ** attempts
                time.sleep(sleep_s)
                continue
