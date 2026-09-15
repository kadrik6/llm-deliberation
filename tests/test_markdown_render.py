"""Unit tests for the browser-presentation-only Markdown renderer.

No DB, no HTTP, no network -- these test render_markdown_safe() in
isolation against known Markdown and known attack payloads.
"""

from __future__ import annotations

from markupsafe import Markup

from llm_deliberation.web.markdown_render import render_markdown_safe


def test_empty_and_none_render_to_empty_markup():
    assert render_markdown_safe("") == Markup("")
    assert render_markdown_safe(None) == Markup("")


def test_returns_a_markup_instance_not_a_plain_string():
    # This is what lets templates interpolate it without a |safe filter.
    assert isinstance(render_markdown_safe("hello"), Markup)


def test_headings_bold_italic_and_lists_render_as_html():
    text = (
        "## Heading\n\n"
        "Some **bold** and *italic* text.\n\n"
        "- item one\n- item two\n\n"
        "1. first\n2. second\n"
    )
    html = render_markdown_safe(text)
    assert "<h2>Heading</h2>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<ul>" in html and "<li>item one</li>" in html
    assert "<ol>" in html and "<li>first</li>" in html
    # The raw Markdown syntax must not leak through unrendered.
    assert "##" not in html
    assert "**" not in html


def test_blockquote_inline_code_and_fenced_code_render():
    text = "> a quote\n\nUse `pip install x`.\n\n```python\nprint('hi')\n```\n"
    html = render_markdown_safe(text)
    assert "<blockquote>" in html
    assert "<code>pip install x</code>" in html
    assert "<pre><code>" in html
    assert "print(&#39;hi&#39;)" in html or "print('hi')" in html


def test_links_render_with_href_and_safe_rel():
    html = render_markdown_safe("[OpenAI](https://openai.com)")
    assert 'href="https://openai.com"' in html
    # noopener/noreferrer: a linked page can't reach back via window.opener
    # or learn which page it was reached from.
    assert 'rel="noopener noreferrer"' in html


def test_tables_render():
    text = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    html = render_markdown_safe(text)
    assert "<table>" in html
    assert "<th>A</th>" in html
    assert "<td>1</td>" in html


def test_underscores_inside_stage_output_are_not_mangled():
    # Regression guard: fake test fixtures elsewhere use text like
    # "analysis_a-output" / "critique_a_of_b-output" -- Python-Markdown
    # must not treat mid-word underscores as emphasis markers.
    html = render_markdown_safe("analysis_a-output")
    assert "analysis_a-output" in html
    assert "<em>" not in html


def test_script_tag_and_its_content_are_removed():
    html = render_markdown_safe("Hello <script>alert(1)</script> world")
    assert "<script" not in html.lower()
    # Unlike a strip-only sanitizer, nh3's clean_content_tags removes the
    # tag *and* its text -- "alert(1)" must not survive as leftover text.
    assert "alert(1)" not in html
    assert "Hello" in html and "world" in html


def test_style_tag_and_its_content_are_removed():
    html = render_markdown_safe("<style>body{display:none}</style>ok")
    assert "<style" not in html.lower()
    assert "display:none" not in html
    assert "ok" in html


def test_form_and_input_are_removed():
    html = render_markdown_safe('<form action="https://evil.example"><input name=x></form>ok')
    assert "<form" not in html.lower()
    assert "<input" not in html.lower()
    assert "evil.example" not in html
    assert "ok" in html


def test_event_handler_attribute_is_stripped():
    html = render_markdown_safe('<p onclick="alert(1)">hi</p>')
    assert "onclick" not in html


def test_img_is_removed_entirely():
    # No product need for model-supplied images -- img stays off the
    # allowlist, so both the tag and its onerror payload are gone.
    html = render_markdown_safe('See <img src=x onerror="alert(1)"> here')
    assert "<img" not in html
    assert "onerror" not in html


def test_javascript_protocol_link_has_href_stripped():
    html = render_markdown_safe("[click me](javascript:alert(1))")
    assert "javascript:" not in html
    # The link text is preserved even though the dangerous href is gone.
    assert "click me" in html


def test_data_protocol_link_has_href_stripped():
    html = render_markdown_safe("[x](data:text/html,<script>alert(1)</script>)")
    assert "data:" not in html
    assert "alert(1)" not in html


def test_iframe_and_its_content_are_removed():
    html = render_markdown_safe('before <iframe src="javascript:alert(1)"></iframe> after')
    assert "<iframe" not in html.lower()
    assert "javascript:" not in html
    assert "before" in html and "after" in html


def test_attr_list_extension_cannot_smuggle_attributes():
    # Markdown's "extra" bundle includes attr_list ({: #id .class ...}).
    # The sanitizer must still strip anything not on the allowlist.
    html = render_markdown_safe('# Heading {: #evil-id .evil-class onclick="alert(1)"}')
    assert "onclick" not in html
    assert "evil-class" not in html
    assert "evil-id" not in html
    assert "<h1>Heading</h1>" in html


def test_disallowed_style_and_class_attributes_never_appear():
    html = render_markdown_safe("| Left | Right |\n|:---|---:|\n| a | b |\n")
    assert "style=" not in html
    assert "class=" not in html


def test_id_attribute_never_appears():
    html = render_markdown_safe('<h2 id="injected">Section</h2>')
    assert "id=" not in html
    assert "<h2>Section</h2>" in html
