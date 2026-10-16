"""XML 1.0 text safety for extracted PDF glyphs (including embedded NUL)."""
import re

INVALID = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


def clean(text):
    # Make undecodable glyphs visible for review instead of silently dropping them.
    return INVALID.sub('\ufffd', text)
