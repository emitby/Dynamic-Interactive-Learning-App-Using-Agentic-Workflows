"""The SVG sanitizer: agent-drawn diagrams are untrusted code until checked."""

import pytest

from studysprint.svg_sanitize import MAX_BYTES, check_svg

GOOD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200">'
        '<defs><marker id="a" markerWidth="5" markerHeight="5" refX="2" refY="2" orient="auto">'
        '<path d="M0,0 L4,2 L0,4 z"/></marker></defs>'
        '<rect x="0" y="0" width="300" height="200" fill="#eef"/>'
        '<line x1="10" y1="10" x2="100" y2="100" stroke="#000" marker-end="url(#a)"/>'
        '<text x="10" y="20" font-size="14">Roots</text><text x="10" y="60">Stem</text>'
        '<text x="10" y="100"><tspan>Leaves</tspan></text></svg>')


def test_good_svg_passes_and_is_canonicalized(fixture_lesson):
    r = check_svg(GOOD)
    assert r.ok, r.problems
    assert r.labels == ["Roots", "Stem", "Leaves"]
    assert r.svg.startswith("<svg") and 'xmlns="http://www.w3.org/2000/svg"' in r.svg
    assert check_svg(fixture_lesson["diagram"]["svg"]).ok


def test_comments_are_dropped():
    r = check_svg(GOOD.replace("<rect", "<!-- secret --><rect"))
    assert r.ok and "secret" not in r.svg


def test_svg_without_namespace_gets_one():
    r = check_svg(GOOD.replace(' xmlns="http://www.w3.org/2000/svg"', ""))
    assert r.ok and "http://www.w3.org/2000/svg" in r.svg


@pytest.mark.parametrize("bad, why", [
    (GOOD.replace("</svg>", "<script>alert(1)</script></svg>"), "script"),
    (GOOD.replace("<rect ", '<rect onclick="alert(1)" '), "event handler"),
    (GOOD.replace("<rect ", '<rect style="fill:url(http://evil/x)" '), "style attribute"),
    (GOOD.replace("</svg>", '<foreignObject><div>hi</div></foreignObject></svg>'), "foreignObject"),
    (GOOD.replace("</svg>", '<image href="http://evil/x.png"/></svg>'), "image"),
    (GOOD.replace("<svg ", '<svg xmlns:xlink="http://www.w3.org/1999/xlink" ').replace(
        "</svg>", '<use xlink:href="#a"/></svg>'), "use + xlink"),
    (GOOD.replace('marker-end="url(#a)"', 'marker-end="url(http://evil/m.svg#a)"'), "external url()"),
    (GOOD.replace('fill="#eef"', 'fill="javascript:alert(1)"'), "javascript: value"),
    ('<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "lol">]>' + GOOD, "DTD/entity"),
    (GOOD.replace("<svg", "<svg").replace("</svg>", "</svgg>"), "malformed"),
    (GOOD.replace(' viewBox="0 0 300 200"', ""), "missing viewBox"),
    (GOOD.replace("<text x=\"10\" y=\"60\">Stem</text>", "").replace("<tspan>Leaves</tspan>", ""), "too few labels"),
    (GOOD.replace("Roots", "Visit www.evil.com"), "unsafe label"),
    ('<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>', "not svg"),
    (GOOD.replace("</svg>", "<rect/>" * 500 + "</svg>"), "too many elements"),
    ("", "empty"),
])
def test_malicious_or_invalid_svg_rejected(bad, why):
    r = check_svg(bad)
    assert not r.ok and r.svg is None, why


def test_oversized_svg_rejected():
    assert not check_svg(GOOD.replace("</svg>", "<desc>" + "x" * MAX_BYTES + "</desc></svg>")).ok


def test_repeated_problem_reported_once():
    many = GOOD.replace("</svg>", '<circle class="c" r="1"/>' * 30 + "</svg>")
    assert len(check_svg(many).problems) == 1
