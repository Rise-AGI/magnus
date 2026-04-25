# back_end/library/fundamental/sql_tools.py


__all__ = [
    "escape_like",
]


def escape_like(
    s: str,
)-> str:
    """Escape SQL LIKE wildcards (% and _) so user search input is treated literally.
    Pair with `.ilike(pattern, escape="\\\\")` at the call site."""

    return s.replace("%", r"\%").replace("_", r"\_")
