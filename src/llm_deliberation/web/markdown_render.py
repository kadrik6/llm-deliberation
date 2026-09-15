"""Render model-generated Markdown into sanitized display HTML.

Used only by Jinja templates, for browser presentation. It never touches
the canonical stored artifact (SQLite) or the Markdown export
(report.py) -- both keep the original raw text untouched.

Two independent, mandatory steps:

1. Markdown -> HTML via `markdown` (Python-Markdown), a maintained parser.
   By itself this is NOT safe to display: Python-Markdown passes raw
   inline HTML straight through untouched (a model echoing
   `<script>...</script>` or `<img onerror=...>` in its output would come
   out the other end unchanged).
2. HTML -> allowlisted HTML via `nh3` (Python bindings for Mozilla's
   Ammonia sanitizer -- actively maintained, unlike the now-archived
   `bleach`). Every tag/attribute not on the explicit allowlist below is
   stripped -- including attributes a model could try to smuggle in
   through Markdown's own `attr_list` syntax (e.g.
   `# Heading {: onclick="..."}`) -- so there is no path from adversarial
   model output to executable markup. Tags in `_CLEAN_CONTENT_TAGS`
   (script/style/iframe/form/...) have their *contents* removed too, not
   just the tag itself, so e.g. `<script>alert(1)</script>` disappears
   completely instead of leaving "alert(1)" behind as inert text.

The result is wrapped in `markupsafe.Markup` *here*, once, right after
sanitization -- never via a template `|safe` filter. That keeps the only
"this HTML is trusted" decision in one reviewable place, on output that
has actually been through both steps, instead of scattered across
templates where a future edit could accidentally apply `|safe` to raw
model text.
"""

from __future__ import annotations

import re

import markdown
import nh3
from markupsafe import Markup

# extra: fenced code blocks, tables, footnotes, def_list, abbr (bundle of
# common non-controversial extensions). sane_lists: a change from "- " to
# "1. " (or vice versa) starts a new list instead of continuing one.
_MARKDOWN_EXTENSIONS = ["extra", "sane_lists"]

_ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "del",
    "ul", "ol", "li",
    "blockquote",
    "code", "pre",
    "a",
    "table", "thead", "tbody", "tr", "th", "td",
}

# Not just stripped-and-unwrapped (which would leave their text content
# behind) -- removed along with everything inside them. These are either
# executable (script), capable of loading/submitting to another origin
# (iframe/object/embed/form and its controls), or pure presentation hazard
# (style). None of them are things Markdown from an LLM legitimately needs.
_CLEAN_CONTENT_TAGS = {
    "script", "style", "iframe", "object", "embed",
    "form", "button", "input", "textarea", "select", "option", "noscript",
}

# Deliberately no "style"/"class"/"id" anywhere (drops e.g. Markdown table
# column alignment and fenced-code language hints) -- keeping the
# attribute allowlist this narrow means there is no CSS-injection surface
# or event-handler attribute to reason about, at the small cost of some
# cosmetic detail. No "img": nothing in this pipeline currently has a
# legitimate use for model-supplied images, so it stays off the allowlist
# rather than adding a URL-scheme surface for no product reason.
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}

_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

# nh3 defaults to this already; set explicitly so the safety property (no
# window.opener access, no referrer leak to a linked page) doesn't quietly
# depend on an upstream default.
_LINK_REL = "noopener noreferrer"


def render_markdown_safe(text: str | None) -> Markup:
    """Convert one stage's raw Markdown text into sanitized display HTML."""
    if not text:
        return Markup("")
    raw_html = markdown.markdown(text, extensions=_MARKDOWN_EXTENSIONS)
    clean_html = nh3.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel=_LINK_REL,
    )
    return Markup(clean_html)


_HEADING_RE = re.compile(r"<h([1-6])>(.*?)</h\1>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")

# Below this many same-level top-level headings, splitting isn't confident
# enough to be worth it -- see split_synthesis_sections' docstring.
_MIN_HEADINGS_TO_SPLIT = 2


def split_synthesis_sections(html: Markup | str) -> list[tuple[str, Markup]] | None:
    """Best-effort split of already-sanitized synthesis HTML into sections,
    for a prominent "headline" plus collapsible detail sections.

    Deliberately conservative: this never guesses at section *meaning* (no
    hardcoded "Strongest counterargument" style labels -- that would be
    presentation-layer inference, and would misdescribe content if the
    model's actual structure differs). It only looks for repeated heading
    tags of the same level in output already sanitized to a small, fixed
    tag vocabulary (only h1-h6, no attributes possible), which makes a
    regex split safe here in a way generic HTML parsing wouldn't be.

    Returns None -- "don't split" -- whenever the structure isn't clearly
    present (fewer than _MIN_HEADINGS_TO_SPLIT headings at the first level
    seen), so the caller falls back to rendering the full block exactly as
    before. This is intentionally the common case for weaker models or
    unusual questions that don't follow the prompt's suggested structure;
    only a confidently-detected structure changes the layout.

    Returns a list of (heading_text, section_html) tuples on success. The
    first tuple is meant to be shown prominently; the rest as collapsible
    sections labeled with their own (real, unmodified) heading text.
    """
    html_str = str(html)
    matches = list(_HEADING_RE.finditer(html_str))
    if not matches:
        return None

    top_level = matches[0].group(1)
    top_matches = [m for m in matches if m.group(1) == top_level]
    if len(top_matches) < _MIN_HEADINGS_TO_SPLIT:
        return None

    sections: list[tuple[str, Markup]] = []

    lead = html_str[: top_matches[0].start()].strip()
    if lead:
        sections.append(("", Markup(lead)))

    for i, match in enumerate(top_matches):
        heading_text = _TAG_RE.sub("", match.group(2)).strip()
        start = match.end()
        end = top_matches[i + 1].start() if i + 1 < len(top_matches) else len(html_str)
        body = html_str[start:end].strip()
        sections.append((heading_text, Markup(body)))

    return sections
