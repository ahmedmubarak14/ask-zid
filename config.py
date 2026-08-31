"""Where the OpenAI key comes from, in one place.

Three sources, most explicit first: a key handed in with the request (the
test UI's key field), the environment, then a .env file next to this one.

The UI path exists because this is a local tool and exporting a variable
before every run is friction that stops people testing. It carries an
obligation: the key is a credential passing through a web request, so it is
never logged, never written to disk by the server, and never included in a
response.
"""

import os
import pathlib

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


def resolve_key(supplied: str | None = None) -> str:
    """The key to use, or a message explaining that there isn't one."""
    if supplied and supplied.strip():
        return supplied.strip()
    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise KeyError(
            "No OpenAI API key. Paste one into the key field, set "
            "OPENAI_API_KEY, or put it in a .env file."
        )
    return key
