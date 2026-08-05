"""Public legal pages.

These are served from the API rather than a separate site because they need URLs
before launch — App Store Connect requires a reachable privacy policy URL, and
the app links to both documents from the sign-up screen.

Deliberately unauthenticated: a reviewer, a regulator, or someone deciding
whether to sign up must be able to read them without an account.
"""

from __future__ import annotations

import functools
import html
import pathlib
import re

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/legal", tags=["legal"])

_DOCS = pathlib.Path(__file__).resolve().parent.parent / "legal"

_PAGES = {
    "privacy": ("privacy.md", "Privacy Policy"),
    "terms": ("terms.md", "Terms of Use"),
}

_STYLE = """
:root { color-scheme: light dark; }
body {
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  max-width: 42rem; margin: 0 auto; padding: 2.5rem 1.25rem 6rem;
  background: #ffffff; color: #16191c;
}
@media (prefers-color-scheme: dark) {
  body { background: #0a0c0e; color: #e8edf1; }
  td, th { border-color: #2a3138 !important; }
  code { background: #1a1f24 !important; }
}
h1 { font-size: 1.9rem; line-height: 1.2; margin: 0 0 .35rem; }
h2 { font-size: 1.15rem; margin: 2.4rem 0 .6rem; }
h2 + p { margin-top: 0; }
strong { font-weight: 650; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .95rem; }
td, th { border: 1px solid #d8dee4; padding: .5rem .7rem; text-align: left; }
code { background: #f2f4f6; padding: .1rem .3rem; border-radius: 3px; font-size: .9em; }
ul { padding-left: 1.2rem; }
li { margin: .3rem 0; }
hr { border: 0; border-top: 1px solid #d8dee4; margin: 2rem 0; }
.updated { color: #6b7680; font-size: .9rem; margin-bottom: 2rem; }
"""


def _render(md: str) -> str:
    """Minimal Markdown to HTML.

    A dependency-free subset covering exactly what these two documents use:
    headings, bold, tables, lists, rules and paragraphs. Pulling in a Markdown
    library to render two static files that change a few times a year is not a
    trade worth making.

    Everything is HTML-escaped before any tag is introduced, so document content
    can never inject markup.
    """
    out: list[str] = []
    in_list = False
    in_table = False

    for line in md.splitlines():
        raw = line.rstrip()
        text = html.escape(raw)
        # Inline: **bold** then `code`.
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)

        def close_blocks() -> None:
            nonlocal in_list, in_table
            if in_list:
                out.append("</ul>")
                in_list = False
            if in_table:
                out.append("</table>")
                in_table = False

        if not raw.strip():
            close_blocks()
            continue

        if raw.startswith("## "):
            close_blocks()
            out.append(f"<h2>{text[3:]}</h2>")
        elif raw.startswith("# "):
            close_blocks()
            out.append(f"<h1>{text[2:]}</h1>")
        elif raw.startswith("---"):
            close_blocks()
            out.append("<hr>")
        elif raw.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{text[2:]}</li>")
        elif raw.startswith("|"):
            cells = [c.strip() for c in text.strip("|").split("|")]
            # The |---|---| separator row carries no content.
            if all(set(c) <= {"-", ":", " "} for c in cells if c):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        else:
            close_blocks()
            css = ' class="updated"' if raw.startswith("**Effective") else ""
            out.append(f"<p{css}>{text}</p>")

    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    return "\n".join(out)


@functools.lru_cache(maxsize=8)
def _page(slug: str) -> str:
    filename, title = _PAGES[slug]
    body = _render((_DOCS / filename).read_text())
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)} — Coach Will</title>"
        f"<style>{_STYLE}</style>"
        "</head><body>"
        f"{body}"
        "</body></html>"
    )


@router.get("/{slug}", response_class=HTMLResponse)
def legal_page(slug: str) -> HTMLResponse:
    if slug not in _PAGES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such document")
    return HTMLResponse(_page(slug))
