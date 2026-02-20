from __future__ import annotations


def __getattr__(name: str):
    if name == "app":
        from .api import app

        return app
    raise AttributeError(name)


__all__ = ["app"]
