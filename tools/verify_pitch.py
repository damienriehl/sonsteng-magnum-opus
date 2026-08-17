#!/usr/bin/env python3
"""Verify hand-authored HTML pages directly under ``site/``.

The generated-site checks are intentionally scoped to ``site/platform``.  This
gate covers the separate pitch surfaces: ``site/index.html`` and any additional
top-level HTML page (including the planned cost-per-credit page).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PAGE_SIZE_CEILING = 250_000
AUTHOR_SURNAMES = ("Sonsteng", "Riehl", "Haydock")

_AUTHOR_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(name) for name in AUTHOR_SURNAMES) + r")\b",
    re.IGNORECASE,
)
_STATISTIC_RE = re.compile(r"(?<!\w)\d")
_PROOF_RE = re.compile(r"\bTHE\s+PROOF\b", re.IGNORECASE)
_HOST_RE = re.compile(r"(?i)(?:https?:)?//([^/\s'\"),]+)")
_CSS_URL_RE = re.compile(r"(?is)(?:url\(\s*(['\"]?)(.*?)\1\s*\)|@import\s+(['\"])(.*?)\3)")
_BASE64_DATA_URI_RE = re.compile(
    rb"data:[^,\s\"'<>]*?;base64,[a-z0-9+/]*={0,2}",
    re.IGNORECASE,
)

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_HIDDEN_TAGS = {"script", "style", "template", "noscript"}
_NON_CONTENT_TAGS = {"header", "footer", "nav", "title"}
_ATTRIBUTION_CLASSES = {"by", "byline", "ribbon"}
_ASSET_ATTRIBUTES = {
    "audio": ("src",),
    "base": ("href",),
    "embed": ("src",),
    "iframe": ("src",),
    "img": ("src", "srcset"),
    "input": ("src",),
    "object": ("data",),
    "script": ("src",),
    "source": ("src", "srcset"),
    "track": ("src",),
    "video": ("src", "poster"),
    "image": ("href", "xlink:href"),
    "use": ("href", "xlink:href"),
}
_ASSET_LINK_RELS = {
    "dns-prefetch", "icon", "manifest", "modulepreload", "prefetch",
    "preconnect", "preload", "stylesheet",
}


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    parent: Element | None
    line: int
    children: list[Element | Text] = field(default_factory=list)


@dataclass
class Text:
    value: str
    parent: Element
    line: int


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("[document]", {}, None, 1)
        self.stack = [self.root]
        self.elements: list[Element] = []
        self.text_nodes: list[Text] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = Element(
            tag,
            {name.lower(): value or "" for name, value in attrs},
            self.stack[-1],
            self.getpos()[0],
        )
        self.stack[-1].children.append(node)
        self.elements.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        node = Text(data, self.stack[-1], self.getpos()[0])
        self.stack[-1].children.append(node)
        self.text_nodes.append(node)


def _ancestors(element: Element | None):
    while element is not None:
        yield element
        element = element.parent


def _descendant_text(element: Element) -> str:
    parts: list[str] = []
    pending: list[Element | Text] = list(reversed(element.children))
    while pending:
        child = pending.pop()
        if isinstance(child, Text):
            parts.append(child.value)
        else:
            pending.extend(reversed(child.children))
    return " ".join(parts)


def _is_content_text(node: Text, has_main: bool) -> bool:
    ancestors = tuple(_ancestors(node.parent))
    if any(element.tag in _HIDDEN_TAGS | _NON_CONTENT_TAGS for element in ancestors):
        return False
    if any(
        set(element.attrs.get("class", "").split()) & _ATTRIBUTION_CLASSES
        for element in ancestors
    ):
        return False
    if has_main:
        return any(element.tag == "main" for element in ancestors)
    # HTML5 permits omitted html/body tags.  The current hand-authored pitch
    # uses that form, so the document root is the content fallback.
    return True


def _inside_proof(node: Text, proof_blocks: set[int]) -> bool:
    return any(id(element) in proof_blocks for element in _ancestors(node.parent))


def _external_hosts(value: str) -> list[str]:
    if value.lstrip().lower().startswith(("data:", "blob:", "about:")):
        return []
    return [match.group(1) for match in _HOST_RE.finditer(value)]


def _site_root(page: Path) -> Path:
    for candidate in (page.parent, *page.parents):
        if candidate.name == "site":
            return candidate
    return page.parent


def _parse(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def _page_weights(path: Path) -> tuple[int, int]:
    content = path.read_bytes()
    inlined_data_uri_bytes = sum(
        len(match.group(0)) for match in _BASE64_DATA_URI_RE.finditer(content)
    )
    return len(content), inlined_data_uri_bytes


def _resolved_target(page: Path, href: str) -> tuple[Path | None, str]:
    parsed = urlsplit(href)
    fragment = unquote(parsed.fragment)
    if not parsed.path:
        return page, fragment

    path_text = unquote(parsed.path)
    if path_text.startswith("/"):
        target = _site_root(page) / path_text.lstrip("/")
    else:
        target = page.parent / path_text
    target = target.resolve()
    if path_text.endswith("/") or target.is_dir():
        target /= "index.html"
    return target, fragment


def _link_errors(page: Path, parser: PageParser) -> list[str]:
    errors: list[str] = []
    ids = {
        value
        for element in parser.elements
        for value in (element.attrs.get("id"), element.attrs.get("name") if element.tag == "a" else None)
        if value
    }
    target_ids: dict[Path, set[str]] = {page.resolve(): ids}

    for element in parser.elements:
        href = element.attrs.get("href")
        if href is None or not href.strip():
            continue
        href = href.strip()
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or href.startswith("//"):
            continue
        target, fragment = _resolved_target(page, href)
        if target is None:
            continue
        resolved = target.resolve()
        if not resolved.exists():
            errors.append(f"line {element.line}: unresolved internal link {href}")
            continue
        if not fragment:
            continue
        if resolved not in target_ids:
            if resolved.suffix.lower() not in {".html", ".htm"}:
                errors.append(
                    f"line {element.line}: internal link {href} has a fragment on a non-HTML target"
                )
                continue
            try:
                target_parser = _parse(resolved)
            except (OSError, UnicodeError) as exc:
                errors.append(f"line {element.line}: cannot read internal link target {href}: {exc}")
                continue
            target_ids[resolved] = {
                value
                for target_element in target_parser.elements
                for value in (
                    target_element.attrs.get("id"),
                    target_element.attrs.get("name") if target_element.tag == "a" else None,
                )
                if value
            }
        if fragment not in target_ids[resolved]:
            errors.append(f"line {element.line}: unresolved internal anchor #{fragment}")
    return errors


def _asset_errors(parser: PageParser) -> list[str]:
    errors: list[str] = []
    for element in parser.elements:
        attributes = _ASSET_ATTRIBUTES.get(element.tag, ())
        if element.tag == "link":
            rels = set(element.attrs.get("rel", "").lower().split())
            attributes = ("href",) if rels & _ASSET_LINK_RELS else ()
        for attribute in attributes:
            value = element.attrs.get(attribute, "")
            for host in _external_hosts(value):
                errors.append(
                    f"line {element.line}: external asset host {host} in {element.tag}[{attribute}]"
                )
        style = element.attrs.get("style", "")
        for match in _CSS_URL_RE.finditer(style):
            value = match.group(2) or match.group(4) or ""
            for host in _external_hosts(value):
                errors.append(f"line {element.line}: external asset host {host} in inline CSS")

    for node in parser.text_nodes:
        if node.parent.tag != "style":
            continue
        for match in _CSS_URL_RE.finditer(node.value):
            value = match.group(2) or match.group(4) or ""
            for host in _external_hosts(value):
                errors.append(f"line {node.line}: external asset host {host} in CSS")
    return errors


def _content_errors(parser: PageParser) -> list[str]:
    errors: list[str] = []
    has_main = any(element.tag == "main" for element in parser.elements)
    proof_blocks: set[int] = set()
    for element in parser.elements:
        if element.tag != "details":
            continue
        summaries = [
            child
            for child in element.children
            if isinstance(child, Element) and child.tag == "summary"
        ]
        if any(_PROOF_RE.search(_descendant_text(summary)) for summary in summaries):
            proof_blocks.add(id(element))

    for node in parser.text_nodes:
        if not _is_content_text(node, has_main) or not node.value.strip():
            continue
        # Bibliographic attribution belongs inside THE PROOF and is not body
        # prose.  Names elsewhere in the page's content remain prohibited.
        if not any(element.tag == "cite" for element in _ancestors(node.parent)):
            for match in _AUTHOR_RE.finditer(node.value):
                surname = next(
                    name
                    for name in AUTHOR_SURNAMES
                    if name.casefold() == match.group(0).casefold()
                )
                errors.append(f"line {node.line}: author surname in body prose: {surname}")
        if not _inside_proof(node, proof_blocks) and _STATISTIC_RE.search(node.value):
            excerpt = " ".join(node.value.split())
            if len(excerpt) > 80:
                excerpt = excerpt[:77] + "..."
            errors.append(
                f"line {node.line}: statistic outside a THE PROOF block: {excerpt}"
            )
    return errors


def verify_page(path: str | Path) -> list[str]:
    """Return human-readable contract violations for one HTML page."""
    page = Path(path)
    errors: list[str] = []
    try:
        total_bytes, inlined_data_uri_bytes = _page_weights(page)
    except OSError as exc:
        return [f"cannot read page: {exc}"]
    authored_payload_bytes = total_bytes - inlined_data_uri_bytes
    if authored_payload_bytes > PAGE_SIZE_CEILING:
        errors.append(
            f"authored payload is {authored_payload_bytes:,} bytes and exceeds "
            f"{PAGE_SIZE_CEILING:,}-byte ceiling"
        )
    try:
        parser = _parse(page)
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot parse page as UTF-8 HTML: {exc}")
        return errors

    errors.extend(_link_errors(page, parser))
    errors.extend(_asset_errors(parser))
    errors.extend(_content_errors(parser))
    return errors


def default_pages() -> list[Path]:
    """Pitch plus future hand-authored HTML pages outside site/platform/."""
    return sorted(
        page
        for page in SITE.rglob("*.html")
        if "platform" not in page.relative_to(SITE).parts
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pages",
        nargs="*",
        type=Path,
        help="HTML pages to verify (default: hand-authored pages outside site/platform)",
    )
    args = parser.parse_args(argv)
    pages = args.pages or default_pages()
    if not pages:
        print("verify_pitch: no pitch pages found", file=sys.stderr)
        return 1

    failures = 0
    for page in pages:
        try:
            total_bytes, inlined_data_uri_bytes = _page_weights(page)
        except OSError:
            pass
        else:
            print(
                f"{page}: transfer weight: {total_bytes:,} bytes total; "
                f"{inlined_data_uri_bytes:,} bytes in inlined base64 data URIs"
            )
        errors = verify_page(page)
        if errors:
            failures += len(errors)
            for error in errors:
                print(f"{page}: {error}", file=sys.stderr)
        else:
            print(f"PASS {page}")
    if failures:
        print(f"verify_pitch: {failures} violation(s)", file=sys.stderr)
        return 1
    print(f"verify_pitch: {len(pages)} page(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
