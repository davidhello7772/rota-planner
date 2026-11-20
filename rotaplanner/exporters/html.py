"""A tiny, safe HTML builder: text is always escaped, markup is explicit."""

from __future__ import annotations

from html import escape
from typing import Any, Dict, Optional


class Markup(str):
    """HTML that is already safe (built by ``el``): inserted as it is."""


def _text(child: Any) -> str:
    if child is None or child is False:
        return ""
    if isinstance(child, Markup):
        return child
    if isinstance(child, (list, tuple)):
        return "".join(_text(c) for c in child)
    return escape(str(child), quote=False)


def _attrs(attrs: Optional[Dict[str, Any]]) -> str:
    parts = []
    for name, value in (attrs or {}).items():
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value if v)
        if value is None or value is False or value == "":
            continue
        name = name.rstrip("_").replace("_", "-")  # class_ -> class, data_x -> data-x
        parts.append(f" {name}" if value is True else f' {name}="{escape(str(value), quote=True)}"')
    return "".join(parts)


VOID = {"col", "br", "meta", "link", "img"}


def el(tag: str, attrs: Optional[Dict[str, Any]] = None, *children: Any) -> Markup:
    """``el("td", {"class_": ["cell", "off"]}, "Quinn J.")`` → ``<td class="cell off">Quinn J.</td>``."""
    if tag in VOID:
        return Markup(f"<{tag}{_attrs(attrs)}>")
    return Markup(f"<{tag}{_attrs(attrs)}>{_text(list(children))}</{tag}>")


def join(*parts: Any) -> Markup:
    return Markup(_text(list(parts)))
