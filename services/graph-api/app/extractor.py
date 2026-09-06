from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from datetime import date

from lxml import etree, html

from app.models import ExtractedEquation, ExtractedPaper, ExtractedSection
from app.security import arxiv_identity, normalize_arxiv_html_url

_SPACE = re.compile(r"\s+")
_WATERMARK = re.compile(
    r"arXiv:(?P<paper_id>\d{4}\.\d{4,5})(?:v(?P<version>\d+))?"
    r"(?:\s+\[[^\]]+\])?(?:\s+(?P<date>\d{1,2}\s+[A-Za-z]{3}\s+\d{4}))?",
    re.IGNORECASE,
)
_MONTHS = {month: index for index, month in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1
)}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_DISPLAY_CLASSES = {"ltx_equation", "ltx_equationgroup", "ltx_display_math"}


class PaperExtractionError(ValueError):
    """Source HTML is empty or inconsistent with the requested identity."""


def _classes(element: etree._Element) -> set[str]:
    return set((element.get("class") or "").split())


def _clean_text(value: str | None, *, limit: int | None = 600) -> str | None:
    if not value:
        return None
    cleaned = _SPACE.sub(" ", value).strip()
    return cleaned[:limit] if cleaned else None


def _meta_values(document: html.HtmlElement, name: str) -> list[str]:
    values = document.xpath(
        "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')=$name]/@content", name=name.lower(),
    )
    return [value for item in values if (value := _clean_text(item, limit=1000))]


def _dom_authors(document: html.HtmlElement) -> list[str]:
    authors: list[str] = []
    for person in document.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' ltx_personname ')]"
    ):
        # A superscript/note is an affiliation marker, not part of the author's name.
        text_parts = person.xpath(
            ".//text()[not(ancestor::sup) and not(ancestor::*[contains("
            "concat(' ', normalize-space(@class), ' '), ' ltx_note ')])]"
        )
        # LaTeXML sometimes places several authors in one personname with wide spaces.
        for part in re.split(r" {2,}|\t|\n", "".join(text_parts)):
            author = _clean_text(part, limit=300)
            if author and author not in authors:
                authors.append(author)
    return authors


def _watermark_metadata(
    document: html.HtmlElement, expected_paper_id: str,
) -> tuple[int | None, date | None]:
    watermark = document.xpath("string(//*[@id='watermark-tr'][1])")
    match = _WATERMARK.search(watermark)
    if match is None:
        return None, None
    if match.group("paper_id") != expected_paper_id:
        raise PaperExtractionError("The arXiv HTML watermark does not match the requested paper.")
    version = int(match.group("version")) if match.group("version") else None
    published_at = None
    if match.group("date"):
        day, month, year = match.group("date").split()
        # Missing/invalid optional metadata is explicit in metadata_warnings.
        with suppress(KeyError, ValueError):
            published_at = date(int(year), _MONTHS[month.lower()], int(day))
    return version, published_at


def _equation_container(math_element: etree._Element) -> etree._Element | None:
    current = math_element
    display_container = None
    while current is not None:
        if _DISPLAY_CLASSES.intersection(_classes(current)):
            if display_container is None:
                display_container = current
            if current.get("id"):
                return current
        # LaTeXML numbered aligned equations use tbody[@id], without a display class.
        if current.tag == "tbody" and current.get("id") and display_container is not None:
            return current
        current = current.getparent()
    if display_container is not None:
        return display_container
    return math_element if math_element.get("display") == "block" else None


def _latex_from_math(element: etree._Element) -> tuple[str | None, str, float]:
    annotations = element.xpath(
        ".//*[local-name()='annotation' and translate(@encoding, "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='application/x-tex']"
    )
    for annotation in annotations:
        latex = "".join(annotation.itertext()).strip()
        if latex:
            return latex, "tex_annotation", 0.99
    alttext = (element.get("alttext") or "").strip()
    if alttext:
        return alttext, "alttext", 0.92
    # Exclude annotations so a semantic fallback isn't repeated in presentation text.
    fallback = "".join(element.xpath(".//text()[not(ancestor::annotation)]")).strip()
    return fallback or None, "mathml_text", 0.6


def _assemble_math(
    container: etree._Element, math_elements: list[etree._Element],
) -> tuple[str, list[str], str, float] | None:
    rows: dict[etree._Element, list[tuple[str, str, float]]] = {}
    for element in math_elements:
        latex, method, confidence = _latex_from_math(element)
        if not latex:
            continue
        row_nodes = element.xpath("ancestor::tr[1]")
        row = row_nodes[0] if row_nodes else container
        fragment = (latex, method, confidence)
        values = rows.setdefault(row, [])
        # Duplicate alternate renderings outside alignment cells must not double count.
        if not row_nodes and fragment in values:
            continue
        values.append(fragment)
    fragments = [fragment for row in rows.values() for fragment in row]
    if not fragments:
        return None
    if len(fragments) == 1:
        latex, method, confidence = fragments[0]
        return latex, [latex], method, confidence
    row_texts = [" ".join(fragment[0] for fragment in row) for row in rows.values()]
    latex = row_texts[0] if len(row_texts) == 1 else (
        r"\begin{gathered}" + "\n" + " \\\\\n".join(row_texts) + "\n" + r"\end{gathered}"
    )
    method = "mathml_text" if any(f[1] == "mathml_text" for f in fragments) else "assembled_tex"
    return latex, [f[0] for f in fragments], method, min(f[2] for f in fragments)


def _sections(
    root: etree._Element, paper_identity: str,
) -> tuple[list[ExtractedSection], dict[etree._Element, ExtractedSection]]:
    sections: list[ExtractedSection] = []
    heading_sections: dict[etree._Element, ExtractedSection] = {}
    container_sections: dict[etree._Element, ExtractedSection] = {}
    for heading in root.iter():
        if heading.tag not in _HEADINGS or "ltx_title_document" in _classes(heading):
            continue
        if any(a.tag in {"nav", "header", "dialog", "footer"} for a in heading.iterancestors()):
            continue
        title = _clean_text(" ".join(heading.itertext()), limit=1000)
        if not title:
            continue
        parent = heading.getparent()
        structural_parent = parent is not None and parent.tag == "section"
        anchor = heading.get("id")
        if not anchor and structural_parent and parent not in container_sections:
            anchor = parent.get("id")
        order = len(sections) + 1
        source_anchor = bool(anchor)
        anchor = anchor or f"generated-section-{order}"
        parent_section = next(
            (container_sections[a] for a in heading.iterancestors() if a in container_sections),
            None,
        )
        section = ExtractedSection(
            section_id="section_" + hashlib.sha256(
                f"{paper_identity}:{anchor}:{order}".encode()
            ).hexdigest()[:20],
            anchor=anchor, anchor_is_source=source_anchor, title=title, order=order,
            parent_section_id=parent_section.section_id if parent_section else None,
        )
        sections.append(section)
        heading_sections[heading] = section
        if structural_parent and parent not in container_sections:
            container_sections[parent] = section

    owners: dict[etree._Element, ExtractedSection] = {}
    section_stack: list[ExtractedSection | None] = []
    active = None
    for event, node in etree.iterwalk(root, events=("start", "end")):
        if event == "end":
            if node.tag == "section":
                active = section_stack.pop()
            continue
        if node.tag == "section":
            section_stack.append(active)
            active = container_sections.get(node, active)
        if node in heading_sections:
            active = heading_sections[node]
        if active is not None:
            owners[node] = active
        if node.tag == "p" and active is not None:
            paragraph = _clean_text(" ".join(node.itertext()), limit=None)
            if paragraph:
                active.text += ("\n\n" if active.text else "") + paragraph
    return sections, owners


def _context(element: etree._Element, axis: str) -> str | None:
    candidates = element.xpath(
        f"{axis}::*[self::p or self::figcaption][normalize-space()][1]"
    )
    return _clean_text(" ".join(candidates[0].itertext())) if candidates else None


def extract_paper(html_text: str, source_url: str) -> ExtractedPaper:
    canonical_url = normalize_arxiv_html_url(source_url)
    paper_id, requested_version = arxiv_identity(canonical_url)
    try:
        document = html.document_fromstring(html_text, parser=html.HTMLParser(no_network=True))
    except (etree.ParserError, ValueError) as exc:
        raise PaperExtractionError("The source does not contain parseable HTML.") from exc
    watermark_version, published_at = _watermark_metadata(document, paper_id)
    if (
        requested_version is not None
        and watermark_version is not None
        and requested_version != watermark_version
    ):
        raise PaperExtractionError("The arXiv HTML version does not match the requested URL.")
    version = watermark_version if watermark_version is not None else requested_version
    if version is not None:
        canonical_url = canonical_url.rsplit("/", 1)[0] + f"/{paper_id}v{version}"
    source_hash = hashlib.sha256(html_text.encode("utf-8")).hexdigest()
    # Unknown versions must not accidentally share equation identity across changed HTML.
    identity = f"{paper_id}:v{version}" if version is not None else f"{paper_id}:{source_hash}"
    articles = document.xpath("//article[contains(concat(' ', normalize-space(@class), ' '), "
                              "' ltx_document ')]")
    root = articles[0] if articles else document
    sections, owners = _sections(root, identity)
    containers: dict[etree._Element, list[etree._Element]] = {}
    for math_element in root.xpath(".//*[local-name()='math']"):
        container = _equation_container(math_element)
        if container is not None:
            containers.setdefault(container, []).append(math_element)

    equations = []
    for index, (container, math_elements) in enumerate(containers.items(), start=1):
        assembled = _assemble_math(container, math_elements)
        if assembled is None:
            continue
        latex, fragments, method, confidence = assembled
        anchor = container.get("id") or (
            math_elements[0].get("id") if len(math_elements) == 1 else None
        )
        source_anchor = bool(anchor)
        anchor = anchor or f"generated-equation-{index}"
        section = owners.get(container)
        warnings = []
        if container.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '),"
            "' ltx_markedasmath ')][not(ancestor::math)]"
        ):
            warnings.append("non_mathml_content_requires_review")
        if method == "mathml_text":
            warnings.append("tex_unavailable")
        if not source_anchor:
            warnings.append("source_anchor_unavailable")
        equation = ExtractedEquation(
            equation_id="eq_" + hashlib.sha256(
                f"{identity}:{anchor}:{latex}".encode()
            ).hexdigest()[:20],
            anchor=anchor, anchor_is_source=source_anchor, latex=latex,
            source_fragments=fragments,
            warnings=warnings,
            equation_number=_clean_text(container.xpath(
                "string(.//*[contains(concat(' ', normalize-space(@class), ' '), "
                "' ltx_tag_equation ')][1])"), limit=80),
            section=section.title if section else None,
            section_id=section.section_id if section else None,
            preceding_text=_context(container, "preceding"),
            following_text=_context(container, "following"),
            extraction_method=method, confidence=confidence,
        )
        equations.append(equation)
        if section is not None:
            section.equation_ids.append(equation.equation_id)

    titles = _meta_values(document, "citation_title")
    title = (titles[0] if titles else
             _clean_text(document.xpath("string(//title)"), limit=1000) or f"arXiv:{paper_id}")
    authors = _meta_values(document, "citation_author") or _dom_authors(root)
    warnings = []
    if version is None:
        warnings.append("version_unresolved")
    if published_at is None:
        warnings.append("version_date_unavailable")
    if not authors:
        warnings.append("authors_unavailable")
    return ExtractedPaper(
        paper_id=paper_id, version=version, title=title, authors=authors,
        version_published_at=published_at, metadata_warnings=warnings,
        source_url=canonical_url, source_sha256=source_hash,
        sections=sections, equations=equations,
    )
