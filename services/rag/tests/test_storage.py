from __future__ import annotations

from pathlib import Path

import pytest

from app.storage.local import LocalFilesystemStorage


@pytest.fixture
def storage(tmp_path: Path) -> LocalFilesystemStorage:
    return LocalFilesystemStorage(tmp_path / "uploads")


class TestLocalFilesystemStorage:
    async def test_round_trips_content(
        self, storage: LocalFilesystemStorage
    ) -> None:
        await storage.write("user-1/doc.pdf", b"%PDF-1.7 content")
        assert await storage.read("user-1/doc.pdf") == b"%PDF-1.7 content"

    async def test_creates_nested_directories(
        self, storage: LocalFilesystemStorage
    ) -> None:
        await storage.write("a/b/c/deep.pdf", b"x")
        assert await storage.exists("a/b/c/deep.pdf")

    async def test_reports_absence(self, storage: LocalFilesystemStorage) -> None:
        assert not await storage.exists("nothing.pdf")

    async def test_delete_is_idempotent(
        self, storage: LocalFilesystemStorage
    ) -> None:
        await storage.write("gone.pdf", b"x")
        await storage.delete("gone.pdf")
        # Deleting twice must not raise: a retried job should not fail on a
        # cleanup step that already succeeded.
        await storage.delete("gone.pdf")
        assert not await storage.exists("gone.pdf")

    async def test_overwrites_atomically(
        self, storage: LocalFilesystemStorage
    ) -> None:
        await storage.write("doc.pdf", b"first")
        await storage.write("doc.pdf", b"second")
        assert await storage.read("doc.pdf") == b"second"

        # The temp file used for the atomic rename must not be left behind.
        root = storage.local_path("doc.pdf")
        assert root is not None
        assert not list(root.parent.glob("*.tmp"))

    def test_exposes_a_real_path_for_parsers(
        self, storage: LocalFilesystemStorage
    ) -> None:
        # Parsers stream from disk rather than buffering whole files, so the
        # local backend must surface a usable path.
        path = storage.local_path("user-1/doc.pdf")
        assert isinstance(path, Path)
        assert path.name == "doc.pdf"


class TestKeyValidation:
    """A storage key arrives from a database row, but treating it as trusted
    input is how a path traversal gets in."""

    @pytest.mark.parametrize(
        "key",
        [
            "../outside.pdf",
            "user-1/../../outside.pdf",
            "/absolute/path.pdf",
            "..",
            "",
        ],
    )
    def test_rejects_keys_that_escape_the_root(
        self, storage: LocalFilesystemStorage, key: str
    ) -> None:
        with pytest.raises(ValueError):
            storage.local_path(key)

    def test_rejects_backslashes(
        self, storage: LocalFilesystemStorage
    ) -> None:
        # On Windows a backslash is a separator, so allowing it would let
        # "..\\.." escape a check written for forward slashes only.
        with pytest.raises(ValueError):
            storage.local_path("user-1\\..\\..\\outside.pdf")

    async def test_traversal_is_blocked_on_write_too(
        self, storage: LocalFilesystemStorage
    ) -> None:
        with pytest.raises(ValueError):
            await storage.write("../escape.pdf", b"x")

    def test_accepts_ordinary_nested_keys(
        self, storage: LocalFilesystemStorage
    ) -> None:
        assert storage.local_path("users/abc-123/lecture-3.pdf") is not None
