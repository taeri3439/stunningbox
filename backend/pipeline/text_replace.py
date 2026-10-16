"""Post-translation bulk term replacement — pure matching/apply over plain strings.

The viewer's 용어 일괄변경 lets a user replace a term across the translated document.
This module is the shared, side-effect-free core: compile a rule into a regex, count
matches, and apply a list of rules to a string. docx_edit (the .docx), process (the model
mirror + saved-rule re-apply on re-translation), and the API preview all call it so the
matching semantics stay identical everywhere.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

# Target languages written with spaces → "smart" matching uses word boundaries so
# replacing "load" doesn't hit "download". CJK has no word separators → substring.
_WORD_BOUNDARY_LANGS = {"en", "es", "fr", "de", "vi"}


class ReplaceRule(BaseModel):
    find: str
    replace: str
    match: Literal["smart", "exact", "loose"] = "smart"
    save: bool = True


def compile_rule(rule: ReplaceRule, target_lang: str) -> re.Pattern | None:
    """A compiled pattern for the rule, or None if the rule is a no-op (empty find, or
    find == replace). `smart`/`loose` are case-insensitive; `smart` adds word boundaries
    for space-separated target languages; `exact` is literal + case-sensitive substring."""
    if not rule.find or rule.find == rule.replace:
        return None
    # A case-only difference under smart/loose (e.g. find="Cabin", replace="cabin") is an
    # INTENTIONAL replacement (the user is normalising casing), so it is NOT treated as a
    # no-op — only an exact find==replace is skipped above.
    body = re.escape(rule.find)
    flags = re.IGNORECASE if rule.match in ("smart", "loose") else 0
    if rule.match == "smart" and target_lang in _WORD_BOUNDARY_LANGS:
        # \w is Unicode-aware in Python 3, so these boundaries work for accented Latin
        # (e.g. Vietnamese) too — do not "simplify" to \b.
        body = rf"(?<!\w){body}(?!\w)"
    return re.compile(body, flags)


def count_in_text(text: str, pattern: re.Pattern | None) -> int:
    """Count matches in `text` as-is. This is a per-pattern count for previews; it does
    NOT account for chained-rule interactions (apply_to_text runs rules in sequence, so a
    later rule may see text an earlier rule produced)."""
    return len(pattern.findall(text)) if pattern else 0


def compile_terms(terms: list[str], lang: str) -> "re.Pattern | None":
    """One case-insensitive alternation matching ANY of `terms` (longest first so a longer
    term wins over a contained shorter one). Word boundaries for space-separated langs,
    substring for CJK — same rule as compile_rule. Returns None if no usable terms."""
    usable = [t for t in terms if t]
    if not usable:
        return None
    body = "|".join(re.escape(t) for t in sorted(set(usable), key=len, reverse=True))
    if lang in _WORD_BOUNDARY_LANGS:
        body = rf"(?<!\w)(?:{body})(?!\w)"
    return re.compile(body, re.IGNORECASE)


def apply_to_text(
    text: str, rules: list[ReplaceRule], target_lang: str
) -> tuple[str, dict[str, int]]:
    """Apply rules in order (a later rule sees earlier rules' output). Returns the new
    text and {find: replaced_count}. The replacement is treated literally (a `\\1` or
    backslash in the replacement is NOT interpreted as a regex backreference)."""
    counts: dict[str, int] = {}
    for rule in rules:
        pattern = compile_rule(rule, target_lang)
        if pattern is None:
            continue
        text, n = pattern.subn(lambda _m, r=rule: r.replace, text)
        if n:
            counts[rule.find] = counts.get(rule.find, 0) + n
    return text, counts
