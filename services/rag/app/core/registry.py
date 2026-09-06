"""Component registry.

Implementations register themselves under a name; configuration selects one by
name at startup. This is what lets the eval harness build a pipeline variant
from a plain dict of names and compare it against another, without any
conditional branching inside the pipeline itself.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._factories: dict[str, Callable[..., T]] = {}

    def register(self, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator registering a factory under a name."""

        def decorator(factory: Callable[..., T]) -> Callable[..., T]:
            if name in self._factories:
                raise ValueError(f"{self._kind} '{name}' is already registered")
            self._factories[name] = factory
            return factory

        return decorator

    def create(self, name: str, **kwargs: Any) -> T:
        try:
            factory = self._factories[name]
        except KeyError:
            raise KeyError(
                f"Unknown {self._kind} '{name}'. Available: {self.available()}"
            ) from None
        return factory(**kwargs)

    def available(self) -> list[str]:
        return sorted(self._factories)

    def __contains__(self, name: object) -> bool:
        return name in self._factories


# One registry per swappable stage. Adapters import these and decorate.
parsers: Registry[Any] = Registry("parser")
chunkers: Registry[Any] = Registry("chunker")
embedders: Registry[Any] = Registry("embedding provider")
sparse_encoders: Registry[Any] = Registry("sparse encoder")
dense_retrievers: Registry[Any] = Registry("dense retriever")
sparse_retrievers: Registry[Any] = Registry("sparse retriever")
fusers: Registry[Any] = Registry("fuser")
rerankers: Registry[Any] = Registry("reranker")
llms: Registry[Any] = Registry("llm provider")
storages: Registry[Any] = Registry("storage provider")
tracers: Registry[Any] = Registry("tracer")
