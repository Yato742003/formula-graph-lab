"""Static LaTeX display extraction with bounded, cycle-safe preamble macro expansion.

Each aligned environment remains one source block. Raw ``source_sha256`` and
``source_fragments`` always capture the unmodified uploaded bytes, while safe
``\\newcommand`` / ``\\renewcommand`` / ``\\providecommand`` / ``\\DeclareMathOperator``
definitions are expanded deterministically under strict depth, step, and size ceilings.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_left
from dataclasses import dataclass

from app.models import ExtractedEquation, ExtractedPaper, ExtractedSection

MAX_MACRO_DEFS = 64
MAX_MACRO_ARITY = 4
MAX_MACRO_BODY_CHARS = 512
MAX_MACRO_EXPANSION_DEPTH = 4
MAX_MACRO_EXPANSIONS_PER_EQUATION = 128
MAX_EXPANDED_EQUATION_CHARS = 16 * 1024

_PROTECTED_COMMANDS = frozenset(
    {
        "begin",
        "end",
        "frac",
        "sqrt",
        "sum",
        "prod",
        "int",
        "left",
        "right",
        "section",
        "subsection",
        "subsubsection",
        "title",
        "author",
        "label",
        "input",
        "include",
        "newcommand",
        "renewcommand",
        "providecommand",
        "DeclareMathOperator",
        "def",
        "let",
        "catcode",
        "write",
        "openin",
        "openout",
        "immediate",
        "operatorname",
        "mathrm",
        "mathbf",
        "mathbb",
        "mathcal",
        "text",
        "log",
        "ln",
        "exp",
        "sin",
        "cos",
        "tan",
    }
)

_OPEN = re.compile(
    r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?)\}"
    r"|\\\[|\$\$|\\(subsubsection|subsection|section)\*?\s*\{|\\(title|author)\s*\{"
)
_MACRO_DECL = re.compile(
    r"\\(newcommand|renewcommand|providecommand|DeclareMathOperator)(\*)?\s*"
)
_CMD_RE = re.compile(r"\\([A-Za-z]+)")
_CALL_CMD_RE = re.compile(r"\\(?:[A-Za-z]+|[^A-Za-z\s])")
_TRAILING_CMD_RE = re.compile(r"\\[A-Za-z]+$")
_COMMENTS = re.compile(r"\\[^\n]|%[^\n]*")
_LEVEL = {"section": 1, "subsection": 2, "subsubsection": 3}


@dataclass(frozen=True)
class LatexMacro:
    """A single statically parsed, non-Turing-complete LaTeX macro definition."""

    name: str
    arity: int
    body: str
    default_arg: str | None = None


def _argument(text: str, pos: int) -> tuple[str, int]:
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


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _read_optional_bracket(text: str, pos: int) -> tuple[str | None, int]:
    pos = _skip_ws(text, pos)
    if pos >= len(text) or text[pos] != "[":
        return None, pos
    pos += 1
    start = pos
    depth = 1
    brace_depth = 0
    while pos < len(text):
        ch = text[pos]
        if ch == "\\":
            pos += 2
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth = max(0, brace_depth - 1)
        elif brace_depth == 0:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start:pos], pos + 1
        pos += 1
    raise ValueError("Unclosed LaTeX optional argument.")


def _read_macro_token(text: str, pos: int) -> tuple[str | None, int]:
    """Read either ``{\\foo}`` or bare ``\\foo`` right after a macro declaration keyword."""
    pos = _skip_ws(text, pos)
    if pos >= len(text):
        return None, pos
    if text[pos] == "{":
        try:
            inner, next_pos = _argument(text, pos + 1)
        except ValueError:
            return None, pos
        m = re.fullmatch(r"\s*\\([A-Za-z]+)\s*", inner)
        if not m:
            return None, next_pos
        return m.group(1), next_pos
    if text[pos] == "\\":
        m = _CMD_RE.match(text, pos)
        if not m:
            return None, pos + 1
        return m.group(1), m.end()
    return None, pos


def _read_call_arg(text: str, pos: int) -> tuple[str, int] | None:
    """Read one macro invocation argument (``{...}``, ``\\cmd``, or single non-brace char)."""
    pos = _skip_ws(text, pos)
    if pos >= len(text):
        return None
    if text[pos] == "{":
        try:
            return _argument(text, pos + 1)
        except ValueError:
            return None
    if text[pos] == "\\":
        m = _CALL_CMD_RE.match(text, pos)
        if m:
            return m.group(0), m.end()
        return None
    if text[pos] in {"}", "$", "&"}:
        return None
    return text[pos], pos + 1


def extract_tex_macros(text: str) -> tuple[dict[str, LatexMacro], list[str]]:
    """Parse safe ``\\newcommand`` and ``\\DeclareMathOperator`` definitions from TeX."""
    macros: dict[str, LatexMacro] = {}
    warnings: set[str] = set()
    pos = 0
    while match := _MACRO_DECL.search(text, pos):
        # Ignore escaped double-backslash before declaration (e.g. \\newcommand).
        if match.start() > 0 and text[match.start() - 1] == "\\":
            pos = match.end()
            continue
        kind = match.group(1)
        cur = match.end()
        name, cur = _read_macro_token(text, cur)
        if not name:
            pos = max(cur, match.end())
            continue

        if kind == "DeclareMathOperator":
            cur = _skip_ws(text, cur)
            if cur >= len(text) or text[cur] != "{":
                pos = max(cur, match.end())
                continue
            try:
                op_name, cur = _argument(text, cur + 1)
            except ValueError:
                pos = match.end()
                continue
            op_clean = op_name.strip()
            if (
                name in _PROTECTED_COMMANDS
                or not op_clean
                or len(op_clean) > MAX_MACRO_BODY_CHARS
            ):
                pos = cur
                continue
            if name not in macros and len(macros) >= MAX_MACRO_DEFS:
                warnings.add("macro_definition_limit_exceeded")
                pos = cur
                continue
            macros[name] = LatexMacro(name=name, arity=0, body=rf"\operatorname{{{op_clean}}}")
            pos = cur
            continue

        # \newcommand / \renewcommand / \providecommand
        arity = 0
        default_arg: str | None = None
        try:
            arity_raw, cur = _read_optional_bracket(text, cur)
        except ValueError:
            pos = match.end()
            continue
        if arity_raw is not None:
            if not arity_raw.strip().isdigit():
                pos = cur
                continue
            arity = int(arity_raw.strip())
            if arity > MAX_MACRO_ARITY:
                warnings.add("macro_arity_limit_exceeded")
                pos = cur
                continue
            if arity >= 1:
                try:
                    default_arg, cur = _read_optional_bracket(text, cur)
                except ValueError:
                    pos = cur
                    continue

        cur = _skip_ws(text, cur)
        if cur >= len(text) or text[cur] != "{":
            pos = max(cur, match.end())
            continue
        try:
            body, cur = _argument(text, cur + 1)
        except ValueError:
            pos = match.end()
            continue

        pos = cur
        if name in _PROTECTED_COMMANDS:
            continue
        if len(body) > MAX_MACRO_BODY_CHARS:
            warnings.add("macro_body_limit_exceeded")
            continue
        if kind == "providecommand" and name in macros:
            continue
        if name not in macros and len(macros) >= MAX_MACRO_DEFS:
            warnings.add("macro_definition_limit_exceeded")
            continue
        macros[name] = LatexMacro(name=name, arity=arity, body=body, default_arg=default_arg)

    return macros, sorted(warnings)


def _substitute_params(body: str, args: list[str]) -> str:
    """Replace ``#1``..``#n`` placeholders without cascading substitution across args."""
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        if body[i] == "\\" and i + 1 < n and body[i + 1] == "#":
            out.append(r"\#")
            i += 2
            continue
        if body[i] == "#" and i + 1 < n and body[i + 1].isdigit():
            idx = int(body[i + 1]) - 1
            if 0 <= idx < len(args):
                out.append(args[idx])
            else:
                out.append(body[i : i + 2])
            i += 2
            continue
        out.append(body[i])
        i += 1
    return "".join(out)


def _needs_space_separator(expanded: str, next_char: str) -> bool:
    if not expanded or not next_char or not next_char.isalpha():
        return False
    return bool(_TRAILING_CMD_RE.search(expanded))


def expand_tex_macros(
    latex: str,
    macros: dict[str, LatexMacro],
    *,
    max_depth: int = MAX_MACRO_EXPANSION_DEPTH,
    max_expansions: int = MAX_MACRO_EXPANSIONS_PER_EQUATION,
    max_chars: int = MAX_EXPANDED_EQUATION_CHARS,
) -> tuple[str, list[str]]:
    """Expand ``macros`` inside one equation under strict cycle, depth, step, and size ceilings."""
    if not macros or "\\" not in latex:
        return latex, []
    if len(latex) > max_chars:
        return latex, ["macro_expansion_limit_reached"]

    warnings: set[str] = set()
    expansions_used = 0
    limit_hit = False

    def expand_segment(segment: str, depth: int, active: frozenset[str], budget: int) -> str:
        nonlocal expansions_used, limit_hit
        if limit_hit or "\\" not in segment:
            return segment

        out: list[str] = []
        out_len = 0
        i = 0
        n = len(segment)
        while i < n:
            if segment[i] != "\\":
                out.append(segment[i])
                out_len += 1
                if out_len > budget:
                    warnings.add("macro_expansion_limit_reached")
                    limit_hit = True
                    return "".join(out)[:budget]
                i += 1
                continue

            # Preserve escaped backslash (\\) verbatim.
            if i + 1 < n and segment[i + 1] == "\\":
                out.append(r"\\")
                out_len += 2
                i += 2
                continue

            m = _CMD_RE.match(segment, i)
            if not m:
                token = segment[i : i + 2]
                out.append(token)
                out_len += len(token)
                i += len(token)
                continue

            cmd = m.group(1)
            macro = macros.get(cmd)
            if macro is None:
                token = m.group(0)
                out.append(token)
                out_len += len(token)
                i += len(token)
                continue

            if cmd in active:
                warnings.add("macro_cycle_detected")
                token = m.group(0)
                out.append(token)
                out_len += len(token)
                i += len(token)
                continue

            if depth >= max_depth or expansions_used >= max_expansions:
                warnings.add("macro_expansion_limit_reached")
                limit_hit = True
                remaining = segment[i:]
                out.append(remaining[: max(0, budget - out_len)])
                return "".join(out)

            cur = m.end()
            args: list[str] = []
            args_ok = True
            if macro.arity > 0:
                if macro.default_arg is not None:
                    try:
                        opt, cur = _read_optional_bracket(segment, cur)
                    except ValueError:
                        opt = None
                    args.append(opt if opt is not None else macro.default_arg)
                while len(args) < macro.arity:
                    parsed_arg = _read_call_arg(segment, cur)
                    if parsed_arg is None:
                        args_ok = False
                        break
                    arg_val, cur = parsed_arg
                    args.append(arg_val)

            if not args_ok:
                token = m.group(0)
                out.append(token)
                out_len += len(token)
                i += len(token)
                continue

            expansions_used += 1
            rem_budget = max(0, budget - out_len)
            if macro.arity > 0:
                # Expand caller arguments under the caller's active set so nested calls like
                # \norm{\norm{x}} work cleanly without triggering false cycle warnings.
                expanded_args = [
                    expand_segment(arg, depth + 1, active, rem_budget) for arg in args
                ]
                substituted = _substitute_params(macro.body, expanded_args)
            else:
                substituted = macro.body

            if len(substituted) > rem_budget:
                warnings.add("macro_expansion_limit_reached")
                limit_hit = True
                out.append(segment[i : i + rem_budget])
                return "".join(out)

            expanded = expand_segment(substituted, depth + 1, active | {cmd}, rem_budget)
            next_char = segment[cur] if cur < n else ""
            if _needs_space_separator(expanded, next_char):
                expanded = expanded + " "
            if out_len + len(expanded) > budget:
                warnings.add("macro_expansion_limit_reached")
                limit_hit = True
                out.append(expanded[: max(0, budget - out_len)])
                return "".join(out)

            out.append(expanded)
            out_len += len(expanded)
            i = cur

        return "".join(out)

    result = expand_segment(latex, 0, frozenset(), max_chars)
    return result.strip(), sorted(warnings)


def extract_tex(tex_source: str, title: str | None = None, filename: str | None = None):
    raw = tex_source.encode("utf-8")
    if not 1 <= len(raw) <= 512 * 1024:
        raise ValueError("LaTeX source must be 1 to 524288 bytes.")
    digest = hashlib.sha256(raw).hexdigest()
    text = _COMMENTS.sub(
        lambda m: "" if m[0].startswith("%") else m[0],
        tex_source.replace("\r\n", "\n").replace("\r", "\n"),
    )
    macros, macro_warnings = extract_tex_macros(text)
    all_warnings: set[str] = set(macro_warnings)
    if re.search(r"\\(?:input|include)\b", text):
        all_warnings.add("external_tex_includes_ignored")

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
        raw_latex = re.sub(r"\\label\{[^{}]*\}", "", fragment).strip()
        if not raw_latex:
            continue
        latex, expand_warnings = expand_tex_macros(raw_latex, macros)
        all_warnings.update(expand_warnings)
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
        metadata_warnings=sorted(all_warnings),
        sections=sections,
        equations=equations,
    )
