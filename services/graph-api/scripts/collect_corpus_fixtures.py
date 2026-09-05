"""Print reduced source excerpts as JSON; does not write files or update golden values.

Run from services/graph-api with PYTHONPATH=. . Review the output before applying
it as fixtures. Presentation MathML is omitted only where a TeX representation
already exists; equation grouping, source IDs and numbers are retained.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

from lxml import etree, html

from app.fetcher import fetch_paper_html


async def collect() -> None:
    manifest = json.loads(
        (Path(__file__).parents[1] / "tests/live_corpus.json").read_text(encoding="utf-8")
    )
    results = []
    for case in manifest:
        await asyncio.sleep(3)
        pid = case["paper_id"]
        body, url = await fetch_paper_html(f"https://arxiv.org/html/{pid}")
        document = html.document_fromstring(body)
        candidates = document.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), ' ltx_equation ') "
            "or contains(concat(' ', normalize-space(@class), ' '), ' ltx_equationgroup ')]"
        )
        source = next((node for node in candidates if node.get("id")), None)
        fixture = html.fromstring("<html><head></head><body></body></html>")
        head, target = fixture.find("head"), fixture.find("body")
        title = etree.SubElement(head, "title")
        title.text = document.xpath("string(//title)")
        watermark = document.xpath("//*[@id='watermark-tr']")
        if watermark:
            target.append(copy.deepcopy(watermark[0]))
        section = etree.SubElement(target, "section", id="fixture-section")
        heading = etree.SubElement(section, "h2")
        heading.text = "Source equation excerpt"
        expected = []
        if source is not None:
            numbered = source.xpath(".//tbody[@id]")
            units = numbered if numbered else [source]
            for unit in units:
                maths = unit.xpath(".//*[local-name()='math']")
                fragments = []
                for math in maths:
                    tex = math.xpath(
                        ".//*[local-name()='annotation' and @encoding='application/x-tex']"
                    )
                    fragments.append(
                        "".join(tex[0].itertext()).strip() if tex
                        else math.get("alttext") or "".join(math.itertext()).strip()
                    )
                number = unit.xpath(
                    "string(.//*[contains(concat(' ',normalize-space(@class),' '),"
                    "' ltx_tag_equation ')][1])"
                ).strip() or None
                expected.append({
                    "anchor": unit.get("id"), "equation_number": number,
                    "source_fragments": fragments,
                })
            reduced = copy.deepcopy(source)
            for math in reduced.xpath(".//*[local-name()='math']"):
                annotations = math.xpath(
                    ".//*[local-name()='annotation' and @encoding='application/x-tex']"
                )
                preserved = [copy.deepcopy(a) for a in annotations]
                if math.get("alttext") or preserved:
                    for child in list(math):
                        math.remove(child)
                    math.text = None
                    for annotation in preserved:
                        math.append(annotation)
            for node in reduced.iter():
                node.tail = None
                if node.tag not in {"math", "annotation", "span"}:
                    node.text = None
                for key in list(node.attrib):
                    if key not in {"id", "class", "alttext", "display", "encoding", "rowspan"}:
                        del node.attrib[key]
            section.append(reduced)
        results.append({
            "paper_id": pid,
            "source_url": url,
            "html": etree.tostring(fixture, encoding="unicode", method="html"),
            "equations": expected,
        })
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(collect())
