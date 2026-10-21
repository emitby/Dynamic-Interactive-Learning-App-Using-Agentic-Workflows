"""Validate and canonicalize agent-drawn SVG diagrams before they reach a browser.

An SVG is XML that can carry scripts, event handlers, external links and CSS, so it's
treated like untrusted code. Defense in depth:

1. **Validate by allowlist.** Only known drawing elements and presentation attributes are
   allowed. Anything else (``<script>``, ``<foreignObject>``, ``onload=``, ``style=``,
   ``href``, external ``url(...)``, DOCTYPE/ENTITY declarations) is rejected with a
   message the Diagram Agent can act on.
2. **Re-serialize.** The SVG served to the browser is rebuilt from the parsed, checked
   tree, so comments and processing instructions are dropped and the output is canonical.
3. **Render as an image.** The front end shows the diagram with
   ``<img src="data:image/svg+xml,...">``. Browsers never run scripts in SVG loaded as
   an image, so even a sanitizer bug couldn't execute code.

Content rules (so the diagram teaches):
    * at most 40 KB and 400 elements (keeps diagrams simple and pages fast)
    * a ``viewBox`` with 4 numbers (so it scales to any screen)
    * at least 3 non-empty text labels, each passing the same content checks as lesson text
"""

import re
import xml.etree.ElementTree as ET

from studysprint.safety import content_problems

SVG_NS = "http://www.w3.org/2000/svg"
MAX_BYTES = 40_000
MAX_ELEMENTS = 400
MIN_LABELS = 3

ALLOWED_ELEMENTS = {
    "svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan",
    "title", "desc", "defs", "marker", "linearGradient", "radialGradient", "stop",
}

ALLOWED_ATTRIBUTES = {
    # geometry
    "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width", "height", "d", "points",
    "dx", "dy", "transform", "viewBox", "preserveAspectRatio", "version", "id",
    # paint
    "fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap", "stroke-linejoin",
    "opacity", "fill-opacity", "stroke-opacity",
    # text
    "font-size", "font-family", "font-weight", "font-style", "text-anchor", "dominant-baseline",
    "letter-spacing",
    # arrowheads and gradients (referenced only by internal url(#id), checked below)
    "marker-start", "marker-end", "marker-mid", "markerWidth", "markerHeight", "refX", "refY", "orient",
    "markerUnits", "offset", "stop-color", "stop-opacity", "gradientUnits",
    # accessibility
    "role", "aria-label",
}

# Any url(...) must point inside the document (url(#arrow)). External or data: URLs could
# fetch remote content or smuggle another document.
_URL_VALUE_RE = re.compile(r"url\(\s*([^)]*)\)", re.IGNORECASE)
_INTERNAL_URL_RE = re.compile(r"^#[A-Za-z][\w-]*$")
_DANGEROUS_VALUE_RE = re.compile(r"javascript:|data:|expression\(|@import", re.IGNORECASE)
_VIEWBOX_RE = re.compile(r"^\s*-?[\d.]+([\s,]+-?[\d.]+){3}\s*$")


class SvgResult:
    """Outcome of :func:`check_svg`.

    Attributes:
        problems: Human-readable problems written for the agent to fix. Empty means valid.
        svg: Canonical re-serialized SVG when valid, else ``None``.
        labels: The diagram's text labels, used by evals to check the diagram is on topic.
    """

    def __init__(self, problems, svg=None, labels=()):
        """Store the check outcome. See the class docstring for the fields."""
        self.problems = problems
        self.svg = svg
        self.labels = list(labels)

    @property
    def ok(self):
        """``True`` when the SVG passed every check."""
        return not self.problems


def _local(tag):
    """Split an ElementTree tag like ``{ns}rect`` into ``(namespace, "rect")``."""
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


def check_svg(svg_text):
    """Validate an SVG string against the allowlists and content rules.

    Args:
        svg_text: The SVG markup an agent wrote.

    Returns:
        An :class:`SvgResult`. When it's ``ok``, ``svg`` holds the canonical markup to
        store and serve.
    """
    problems = []
    if not isinstance(svg_text, str) or not svg_text.strip():
        return SvgResult(["The SVG is empty."])
    if len(svg_text.encode("utf-8")) > MAX_BYTES:
        return SvgResult([f"The SVG is larger than {MAX_BYTES // 1000} KB. Simplify the drawing."])
    # Reject DTDs before parsing: they enable entity-expansion ("billion laughs") and
    # external-entity attacks, and a diagram never needs one.
    if re.search(r"<!(DOCTYPE|ENTITY)", svg_text, re.IGNORECASE):
        return SvgResult(["Remove the DOCTYPE/ENTITY declaration; plain <svg> markup only."])
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        return SvgResult([f"The SVG is not well-formed XML ({exc}). Check that every tag is closed."])

    ns, name = _local(root.tag)
    if name != "svg":
        problems.append("The root element must be <svg>.")
    if ns not in ("", SVG_NS):
        problems.append(f"Unexpected XML namespace {ns!r}; use xmlns=\"{SVG_NS}\".")
    if not _VIEWBOX_RE.match(root.get("viewBox", "")):
        problems.append('Add a viewBox with four numbers, e.g. viewBox="0 0 600 400".')

    elements = list(root.iter())
    if len(elements) > MAX_ELEMENTS:
        problems.append(f"Too many elements ({len(elements)} > {MAX_ELEMENTS}). Simplify the drawing.")

    labels = []
    for el in elements:
        el_ns, el_name = _local(el.tag)
        if el_ns not in ("", SVG_NS) or el_name not in ALLOWED_ELEMENTS:
            problems.append(f"Element <{el_name}> is not allowed. Use basic shapes, paths and text only.")
            continue
        for attr, value in el.attrib.items():
            a_ns, a_name = _local(attr)
            # Any namespaced attribute (xlink:href, xml:base, ...) is refused outright.
            if a_ns or a_name not in ALLOWED_ATTRIBUTES:
                problems.append(f"Attribute {a_name!r} on <{el_name}> is not allowed "
                                "(no style, class, href or event handlers; use presentation attributes).")
                continue
            if _DANGEROUS_VALUE_RE.search(value):
                problems.append(f"Attribute {a_name!r} has a disallowed value {value[:40]!r}.")
            for ref in _URL_VALUE_RE.findall(value):
                if not _INTERNAL_URL_RE.match(ref.strip().strip("'\"")):
                    problems.append(f"url({ref}) must reference an element in this SVG, like url(#arrow).")
        if el_name in ("text", "tspan"):
            # Only direct text, so a <text> with <tspan> children isn't counted twice.
            label = (el.text or "").strip()
            if label:
                labels.append(label)
                problems += [f"Label {label!r}: {p}" for p in content_problems(label)]

    if len(labels) < MIN_LABELS:
        problems.append(f"Add at least {MIN_LABELS} text labels that name the parts of the diagram "
                        f"(found {len(labels)}).")
    if problems:
        # De-duplicate while keeping order: one bad attribute repeated on 50 shapes
        # should produce one message, not 50.
        return SvgResult(list(dict.fromkeys(problems)), labels=labels)

    # Canonical output: default namespace, no comments or processing instructions
    # (ElementTree drops both when parsing).
    ET.register_namespace("", SVG_NS)
    if not ns:
        root.set("xmlns", SVG_NS)
    return SvgResult([], ET.tostring(root, encoding="unicode"), labels)
