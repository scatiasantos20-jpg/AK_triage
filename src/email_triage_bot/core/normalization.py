from __future__ import annotations

import re
from bs4 import BeautifulSoup


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return normalize_whitespace(text)


_QUOTED_PATTERNS = [
    r"^On .+ wrote:$",
    r"^From:.+$",
    r"^Sent:.+$",
    r"^To:.+$",
    r"^Subject:.+$",
    r"^-----Original Message-----$",
]


def strip_quoted_replies(text: str, max_lines: int = 220) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    out = []
    for ln in lines:
        s = ln.rstrip()
        if s.strip().startswith(">"):
            break
        if any(re.match(p, s.strip(), flags=re.IGNORECASE) for p in _QUOTED_PATTERNS):
            break
        out.append(s)
    out = out[:max_lines]
    return normalize_whitespace("\n".join(out))


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join([ln.rstrip() for ln in text.split("\n")])
    return text.strip()
