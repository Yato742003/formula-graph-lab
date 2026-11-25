"""Static LaTeX display extraction. Each aligned environment is one source block."""

import hashlib
import re
from bisect import bisect_left

from app.models import ExtractedEquation, ExtractedPaper, ExtractedSection

_OPEN = re.compile(
    r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?)\}"
    r"|\\\[|\$\$|\\(subsubsection|subsection|section)\*?\s*\{|\\(title|author)\s*\{"
)
_COMMENTS = re.compile(r"\\[^\n]|%[^\n]*")
_LEVEL = {"section": 1, "subsection": 2, "subsubsection": 3}


def _argument(text, pos):
    depth = 1
    start = pos
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        depth += (char == "{") - (char == "}")
        if not depth:
            return text[start:pos], pos + 1
        pos += 1
    raise ValueError("Unclosed LaTeX argument.")


def extract_tex(tex_source: str, title: str | None = None, filename: str | None = None):
    raw = tex_source.encode("utf-8")
    if not 1 <= len(raw) <= 512 * 1024:
        raise ValueError("LaTeX source must be 1 to 524288 bytes.")
    digest = hashlib.sha256(raw).hexdigest()
    text = _COMMENTS.sub(
        lambda m: "" if m[0].startswith("%") else m[0],
        tex_source.replace("\r\n", "\n").replace("\r", "\n"),
    )
    lines = [m.start() for m in re.finditer("\n", text)]
    sections, equations, parents, metadata = [], [], [], {}
    pos = 0
    while match := _OPEN.search(text, pos):
        pos = match.end()
        line = 1 + bisect_left(lines, match.start())
        env, heading, field = match.groups()
        if field or heading:
            value, pos = _argument(text, pos)
            if field:
                metadata.setdefault(field, value.strip())
            else:
                while parents and parents[-1][0] >= _LEVEL[heading]:
                    parents.pop()
                order = len(sections) + 1
                section = ExtractedSection(
                    section_id=f"S{order}",
                    anchor=f"L{line}",
                    title=value,
                    order=order,
                    parent_section_id=parents[-1][1] if parents else None,
                )
                sections.append(section)
                parents.append((_LEVEL[heading], section.section_id))
            continue
        close = rf"\end{{{env}}}" if env else ("$$" if match[0] == "$$" else r"\]")
        end = text.find(close, pos)
        if end < 0:
            raise ValueError("Unclosed display math.")
        fragment = text[pos:end]
        pos = end + len(close)
        labels = re.findall(r"\\label\{([^{}]*)\}", fragment)
        latex = re.sub(r"\\label\{[^{}]*\}", "", fragment).strip()
        if not latex:
            continue
        anchor = f"L{line}-L{1 + bisect_left(lines, pos - 1)}"
        eq_id = f"E{len(equations) + 1}"
        section = sections[-1] if sections else None
        if section:
            section.equation_ids.append(eq_id)
        equations.append(
            ExtractedEquation(
                equation_id=eq_id,
                anchor=anchor,
                latex=latex,
                source_fragments=[fragment],
                equation_number=labels[0] if labels else None,
                section=section.title if section else None,
                section_id=section.section_id if section else None,
                preceding_text=text[max(0, match.start() - 200) : match.start()],
                following_text=text[pos : pos + 200],
                extraction_method="assembled_tex",
                confidence=0.95,
            )
        )
    if not equations:
        raise ValueError("No display equations found.")
    return ExtractedPaper(
        paper_id=f"tex_{digest[:12]}",
        version=1,
        source_kind="local_tex",
        source_sha256=digest,
        source_url=f"urn:fgl:local-tex:{digest}",
        title=metadata.get("title") or title or filename or "Uploaded LaTeX Document",
        authors=[
            a.strip() for a in re.split(r"\\and\b|,", metadata.get("author", "")) if a.strip()
        ],
        metadata_warnings=["external_tex_includes_ignored"]
        if re.search(r"\\(?:input|include)\b", text)
        else [],
        sections=sections,
        equations=equations,
    )
