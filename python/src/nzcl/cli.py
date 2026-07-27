"""Console entry points declared in pyproject.toml."""

from __future__ import annotations


def discover_main() -> int:
    from .discover import main
    return main()


def ingest_main() -> int:
    from .ingest import main
    return main()


def qa_main() -> int:
    from .qa import main
    return main()


def batch_main() -> int:
    from .batch import main
    return main()


def export_main() -> int:
    from .export import main
    return main()
