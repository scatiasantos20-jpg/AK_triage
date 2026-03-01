"""Compatibility package for running from a source checkout.

This repository uses a ``src/`` layout. When running modules directly from the
project root without installing the package (for example,
``python -m email_triage_bot.gm_list``), Python cannot find ``src/email_triage_bot``
by default.

This shim extends the package search path so submodules resolve both from an
installed package and from a local checkout.
"""

from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

# Keep namespace/package behavior compatible if another distribution contributes
# the same top-level package name.
__path__ = extend_path(__path__, __name__)  # type: ignore[name-defined]

_src_pkg = Path(__file__).resolve().parent.parent / "src" / __name__
if _src_pkg.is_dir():
    __path__.append(str(_src_pkg))
