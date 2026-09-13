# Agent Guidelines & Ponytail Skills

All AI agents working on this codebase must follow the **Ponytail (lazy senior dev)** discipline.

The best code is the code never written. Lazy means efficient, not careless.

## The Ladder

Before writing any code, stop at the first rung that holds:

1. **Does this need to exist at all?** (YAGNI) — Skip speculative abstractions.
2. **Already in this codebase?** Reuse existing helpers, types, and patterns.
3. **Standard library does it?** Use it.
4. **Native platform feature covers it?** Use HTML/CSS/browser/OS capabilities.
5. **Already-installed dependency solves it?** Use it; avoid adding new packages.
6. **Can it be one line?** Make it one line.
7. **Only then:** Write the minimum code that works.

### Non-Negotiable Boundaries
- Never simplify away: security, trust boundary input validation, data loss prevention, accessibility.
- Bug fixes: Fix the root cause in the shared function, not just the symptom in the caller.
- Every non-trivial change must leave behind ONE runnable check.
- Deliberate simplifications must include a comment:
  `# ponytail: <what was simplified>, ceiling: <limit>, upgrade: <trigger to revisit>`

---

## Project Skills (.agents/skills/)

Skills are available in the `.agents/skills/` directory:

| Skill | Path | Description |
| :--- | :--- | :--- |
| **`ponytail`** | [`.agents/skills/ponytail/SKILL.md`](.agents/skills/ponytail/SKILL.md) | Enforces lazy senior dev mode (`lite`, `full`, `ultra`). |
| **`ponytail-audit`** | [`.agents/skills/ponytail-audit/SKILL.md`](.agents/skills/ponytail-audit/SKILL.md) | Whole-repo audit for over-engineering and bloat. |
| **`ponytail-debt`** | [`.agents/skills/ponytail-debt/SKILL.md`](.agents/skills/ponytail-debt/SKILL.md) | Scans all `ponytail:` comments into a structured debt ledger. |
| **`ponytail-gain`** | [`.agents/skills/ponytail-gain/SKILL.md`](.agents/skills/ponytail-gain/SKILL.md) | Scoreboard of code/speed/cost impact. |
| **`ponytail-help`** | [`.agents/skills/ponytail-help/SKILL.md`](.agents/skills/ponytail-help/SKILL.md) | Quick reference card for commands and options. |
| **`ponytail-review`** | [`.agents/skills/ponytail-review/SKILL.md`](.agents/skills/ponytail-review/SKILL.md) | Code review focusing on complexity and deletion opportunities. |
