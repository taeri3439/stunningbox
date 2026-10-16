"""Verify that the `deleted` tombstone field has been removed from text block classes.

The field was removed in T9 (deletion is now structural — absent from blocks list).
Old persisted JSON may still contain "deleted: true/false"; Pydantic ignores
unknown fields by default so those docs must still load without error.
"""
import pytest
from models import HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock, SemanticDocument


def test_text_blocks_have_no_deleted_field():
    """The deleted field no longer exists on text block classes."""
    assert not hasattr(HeadingBlock(text="h"), "deleted")
    assert not hasattr(ParagraphBlock(text="p"), "deleted")
    assert not hasattr(ListItemBlock(text="l"), "deleted")
    assert not hasattr(CaptionBlock(text="c"), "deleted")


def test_old_json_with_deleted_still_validates():
    """Persisted models that contain 'deleted' load without error (field is ignored)."""
    doc = SemanticDocument.model_validate(
        {"source_lang": "ko", "blocks": [{"type": "paragraph", "text": "x", "deleted": True}]}
    )
    assert doc.blocks[0].text == "x"
    assert not hasattr(doc.blocks[0], "deleted")


def test_old_json_without_deleted_still_validates():
    """Persisted models that predate the field load normally."""
    doc = SemanticDocument.model_validate(
        {"source_lang": "ko", "blocks": [{"type": "paragraph", "text": "x"}]}
    )
    assert doc.blocks[0].text == "x"
