from __future__ import annotations

import hashlib
import re

from lxml import etree, html

from app.models import ExtractedEquation, ExtractedPaper
from app.security import arxiv_identity, normalize_arxiv_html_url


_SPACE = re.compile(r"\s+")


def _clean_text(value: str | None, *, limit: int = 600) -> str | None:
    if not value:
        return None
    cleaned = _SPACE.sub(" ", value).strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _meta_values(document: html.HtmlElement, name: str) -> list[str]:
    values = document.xpath(
        "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')=$name]/@content",
        name=name.lower(),
    )
    return [value for value in (_clean_text(item, limit=300) for item in values) if value]


_DISPLAY_CLASSES = {
    "ltx_equation",
    "ltx_equationgroup",
    "ltx_display_math",
}


def _equation_container(
    math_element: etree._Element,
) -> tuple[etree._Element, bool]:
    has_block_display = math_element.get("display") == "block"
    display_container: etree._Element | None = None
    current = math_element
    while current is not None:
        class_tokens = set((current.get("class") or "").split())
        if _DISPLAY_CLASSES.intersection(class_tokens):
            if display_container is None:
                display_container = current
            if current.get("id"):
                return current, True
        current = current.getparent()
    if display_container is not None:
        return display_container, True
    return math_element, has_block_display


def _latex_from_math(math_element: etree._Element) -> tuple[str | None, str, float]:
    annotations = math_element.xpath(
        ".//*[local-name()='annotation' and "
        "translate(@encoding, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')='application/x-tex']"
    )
    for annotation in annotations:
        latex = _clean_text("".join(annotation.itertext()), limit=4000)
        if latex:
            return latex, "tex_annotation", 0.99

    alttext = _clean_text(math_element.get("alttext"), limit=4000)
    if alttext:
        return alttext, "alttext", 0.92

    fallback = _clean_text("".join(math_element.itertext()), limit=4000)
    return fallback, "mathml_text", 0.6


def _nearest_axis_text(element: etree._Element, axis: str) -> str | None:
    candidates = element.xpath(
        f"{axis}::*[self::p or self::figcaption or self::li][normalize-space()][1]"
    )
    if not candidates:
        return None
    return _clean_text(" ".join(candidates[0].itertext()))


def extract_paper(html_text: str, source_url: str) -> ExtractedPaper:
    canonical_url = normalize_arxiv_html_url(source_url)
    paper_id, version = arxiv_identity(canonical_url)
    document = html.document_fromstring(html_text)

    title_values = _meta_values(document, "citation_title")
    title = (
        title_values[0]
        if title_values
        else _clean_text(document.xpath("string(//title)"), limit=300)
        or f"arXiv:{paper_id}"
    )
    authors = _meta_values(document, "citation_author")
    source_sha256 = hashlib.sha256(html_text.encode("utf-8")).hexdigest()

    equations: list[ExtractedEquation] = []
    seen: set[tuple[str, str]] = set()

    for index, math_element in enumerate(document.xpath("//*[local-name()='math']"), start=1):
        latex, method, confidence = _latex_from_math(math_element)
        if not latex:
            continue

        container, is_display = _equation_container(math_element)
        if not is_display:
            continue
        anchor = (
            container.get("id")
            or math_element.get("id")
            or f"generated-equation-{index}"
        )
        identity = (anchor, latex)
        if identity in seen:
            continue
        seen.add(identity)

        equation_number = _clean_text(
            container.xpath(
                "string(.//*[contains(concat(' ', normalize-space(@class), ' '), "
                "' ltx_tag_equation ')][1])"
            ),
            limit=80,
        )
        section = _clean_text(
            container.xpath(
                "string(preceding::*[self::h1 or self::h2 or self::h3 "
                "or self::h4 or self::h5 or self::h6][1])"
            ),
            limit=240,
        )
        digest_input = f"{paper_id}:{version}:{anchor}:{latex}".encode()
        equation_id = f"eq_{hashlib.sha256(digest_input).hexdigest()[:20]}"

        equations.append(
            ExtractedEquation(
                equation_id=equation_id,
                anchor=anchor,
                latex=latex,
                equation_number=equation_number,
                section=section,
                preceding_text=_nearest_axis_text(container, "preceding"),
                following_text=_nearest_axis_text(container, "following"),
                extraction_method=method,
                confidence=confidence,
            )
        )

    return ExtractedPaper(
        paper_id=paper_id,
        version=version,
        title=title,
        authors=authors,
        source_url=canonical_url,
        source_sha256=source_sha256,
        equations=equations,
    )
