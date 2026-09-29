"""Local filesystem storage.

Keys are relative POSIX paths under a configured root. Every key is resolved
and checked to stay inside that root, so a crafted key cannot escape it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


class LocalFilesystemStorage:
    name = "local"

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise ValueError(f"Invalid storage key: {key!r}")
        candidate = (self._root / key).resolve()
        # Path.is_relative_to is 3.9+, and this is the whole point of the check.
        if not candidate.is_relative_to(self._root):
            raise ValueError(f"Storage key escapes root: {key!r}")
        return candidate

    async def read(self, key: str) -> bytes:
        path = self._resolve(key)
        return await asyncio.to_thread(path.read_bytes)

    async def write(self, key: str, data: bytes) -> str:
        path = self._resolve(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a sibling temp file then rename, so a reader never sees
            # a partially written document.
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(_write)
        return key

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        await asyncio.to_thread(path.unlink, True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._resolve(key).is_file)

    def local_path(self, key: str) -> Path | None:
        return self._resolve(key)
