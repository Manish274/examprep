from __future__ import annotations

import pytest

from app.core.registry import Registry


class TestRegistry:
    def test_creates_a_registered_implementation(self) -> None:
        registry: Registry[str] = Registry("thing")

        @registry.register("alpha")
        def _factory(value: str = "default") -> str:
            return f"alpha:{value}"

        assert registry.create("alpha") == "alpha:default"
        assert registry.create("alpha", value="x") == "alpha:x"

    def test_lists_available_names_sorted(self) -> None:
        registry: Registry[str] = Registry("thing")
        registry.register("zulu")(lambda: "z")
        registry.register("alpha")(lambda: "a")
        assert registry.available() == ["alpha", "zulu"]

    def test_unknown_name_names_the_alternatives(self) -> None:
        registry: Registry[str] = Registry("reranker")
        registry.register("noop")(lambda: "n")

        with pytest.raises(KeyError, match="noop"):
            registry.create("does-not-exist")

    def test_duplicate_registration_is_rejected(self) -> None:
        # Silent overwrites would make the active implementation depend on
        # import order, which is exactly the bug this guards against.
        registry: Registry[str] = Registry("thing")
        registry.register("alpha")(lambda: "a")

        with pytest.raises(ValueError, match="already registered"):
            registry.register("alpha")(lambda: "b")
