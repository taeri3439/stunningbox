from typing import Literal
import unicodedata

from pydantic import BaseModel, Field


class RenderRequest(BaseModel):
    preserve_spacing: bool = True
    harmonize_repeats: bool = True
    mode: Literal["replay", "fit"] = "replay"
    replacements: dict[str, str] = Field(default_factory=dict)
    manual_regions: list[str] = Field(default_factory=list)
    apply_region_id: str | None = None
    apply_region_ids: list[str] = Field(default_factory=list, max_length=500)
    base_run: str | None = None
    min_scale: float = Field(default=0.9, ge=0.4, le=1.0)
    min_font_size: float = Field(default=8, ge=3, le=24)
    line_height: float = Field(default=1.2, ge=1.0, le=1.6)


class CompareRequest(BaseModel):
    preserve_spacing: bool = True
    harmonize_repeats: bool = True
    include_baseline: bool = True
    source_lang: Literal["ko", "en", "ja", "zh", "es", "fr", "de", "vi"] = "ko"
    target_lang: Literal["ko", "en", "ja", "zh", "es", "fr", "de", "vi"] = "en"
    min_scale: float = Field(default=0.4, ge=0.4, le=1.0)
    min_font_size: float = Field(default=3, ge=3, le=24)
    line_height: float = Field(default=1.1, ge=1.0, le=1.6)


def normalized(text: str) -> str:
    return "".join(unicodedata.normalize('NFKC', text).split())
