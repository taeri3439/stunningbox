from pipeline.text_replace import ReplaceRule, apply_to_text, compile_rule, count_in_text


def _rule(find, replace, match="smart"):
    return ReplaceRule(find=find, replace=replace, match=match)


def test_smart_word_boundary_en():
    # "load" must not match inside "download"; smart is case-insensitive.
    text = "Download the load and LOAD it."
    out, counts = apply_to_text(text, [_rule("load", "cargo")], "en")
    assert out == "Download the cargo and cargo it."
    assert counts == {"load": 2}


def test_smart_substring_cjk():
    # Korean has no word boundaries → substring match even in smart mode.
    out, counts = apply_to_text("정격하중과 정격하중", [_rule("정격하중", "정격")], "ko")
    assert out == "정격과 정격"
    assert counts == {"정격하중": 2}


def test_exact_is_case_sensitive_substring():
    out, counts = apply_to_text("Load load", [_rule("Load", "X", "exact")], "en")
    assert out == "X load"
    assert counts == {"Load": 1}


def test_loose_is_case_insensitive_substring():
    # substring (hits inside "download") + case-insensitive.
    out, counts = apply_to_text("download LOAD", [_rule("load", "x", "loose")], "en")
    assert out == "downx x"
    assert counts == {"load": 2}


def test_rules_apply_in_order():
    rules = [_rule("A", "B", "exact"), _rule("B", "C", "exact")]
    out, counts = apply_to_text("A", rules, "en")
    assert out == "C"
    assert counts == {"A": 1, "B": 1}


def test_noop_rules_skipped():
    rules = [_rule("", "x"), _rule("same", "same"), _rule("hit", "done", "exact")]
    out, counts = apply_to_text("same hit", rules, "en")
    assert out == "same done"
    assert counts == {"hit": 1}
    assert compile_rule(_rule("", "x"), "en") is None
    assert compile_rule(_rule("a", "a"), "en") is None


def test_replacement_with_backslash_is_literal():
    out, _ = apply_to_text("see X", [_rule("X", r"\1 path", "exact")], "en")
    assert out == r"see \1 path"


def test_count_in_text():
    pat = compile_rule(_rule("load", "x"), "en")
    assert count_in_text("load the load", pat) == 2
    assert count_in_text("anything", None) == 0


def test_smart_non_boundary_lang_is_substring():
    # A language not in _WORD_BOUNDARY_LANGS (e.g. "ja") and not needing boundaries falls
    # back to case-insensitive substring under smart mode.
    out, counts = apply_to_text("download LOAD", [_rule("load", "x", "smart")], "ja")
    assert out == "downx x"
    assert counts == {"load": 2}
