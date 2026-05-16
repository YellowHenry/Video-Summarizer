import json
from pathlib import Path

from .vector_store import VectorStore


def populate() -> None:
    vs = VectorStore()
    vs.populate_from_storage(Path("storage"))


if __name__ == "__main__":
    populate()
