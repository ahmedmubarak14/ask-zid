"""Where the OpenAI key comes from, in one place.

Three sources, most explicit first: a key handed in with the request (the
test UI's key field), the environment, then a .env file next to this one.

The UI path exists because this is a local tool and exporting a variable
before every run is friction that stops people testing. It carries an
obligation: the key is a credential passing through a web request, so it is
never logged, never written to disk by the server, and never included in a
response.
"""

from __future__ import annotations

import os
import pathlib
import sys

if sys.version_info < (3, 9):                     # macOS ships 3.9; support it
    raise SystemExit(
        f"ask-zid needs Python 3.9 or newer; this is "
        f"{sys.version_info.major}.{sys.version_info.minor}."
    )

ROOT = pathlib.Path(__file__).resolve().parent


def load_env(path: pathlib.Path | None = None) -> None:
    """Read a .env file into os.environ without overwriting what is set."""
    env_path = path or (ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip().strip("'\""))


def _usable(key: str) -> bool:
    """A real key, as opposed to the placeholder from a copied instruction.

    Worth checking: a .env holding "sk-your-new-key" otherwise reaches the
    API and comes back as an authentication failure, which sends people
    looking for the wrong problem.
    """
    return key.startswith("sk-") and "your" not in key.lower() and len(key) > 20


def resolve_key(supplied: str | None = None) -> str:
    """The key to use, or a message explaining that there isn't one."""
    if supplied and supplied.strip():
        return supplied.strip()
    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and not _usable(key):
        raise KeyError(
            "OPENAI_API_KEY looks like a placeholder, not a real key "
            f"({key[:12]}…). Fix or delete your .env, or paste a key into "
            "the field on the page."
        )
    if not key:
        raise KeyError(
            "No OpenAI API key. Paste one into the key field, set "
            "OPENAI_API_KEY, or put it in a .env file."
        )
    return key
