"""Keep extracted/placement coordinates separate from rotated display coordinates."""
from contextlib import contextmanager

import fitz


@contextmanager
def unrotated(page):
    """Rasterization and table detection must use the same frame as PDF glyphs.

    Only the in-memory handle changes, including when analysis fails. Never save
    the input document from this context.
    """
    rotation = page.rotation
    if rotation:
        page.set_rotation(0)
    try:
        yield page
    finally:
        if rotation:
            page.set_rotation(rotation)


def content_size(model):
    width, height = model['width'], model['height']
    return (height, width) if model.get('rotation', 0) % 180 else (width, height)


def sync_display(page, model):
    for region in model['regions']:
        region['display_bbox'] = list(fitz.Rect(region['bbox']) * page.rotation_matrix)


def display_rect(model, box):
    rotation = model.get('rotation', 0) % 360
    if not rotation:
        return list(box)
    width, height = content_size(model)
    matrices = {90: (0, 1, -1, 0, height, 0),
                180: (-1, 0, 0, -1, width, height),
                270: (0, -1, 1, 0, 0, width)}
    return list(fitz.Rect(box) * fitz.Matrix(*matrices[rotation]))

