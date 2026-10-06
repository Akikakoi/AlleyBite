from typing import Literal

from pydantic import BaseModel, Field, field_validator

Sentiment = Literal["positive", "neutral", "negative", "mixed"]


class Mention(BaseModel):
    """一条对某店铺的提及证据，对应文档 5.3 的抽取三元组。"""

    shop_name: str
    address_text: str | None = None
    area: str | None = None
    dishes: list[str] = Field(default_factory=list)
    avg_price: float | None = None
    cuisine: str | None = None
    sentiment: Sentiment = "neutral"
    praise_keywords: list[str] = Field(default_factory=list)
    complaints: list[str] = Field(default_factory=list)
    is_recommendation: bool = False
    confidence: float = 0.0
    evidence_span: str | None = None

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("dishes", "praise_keywords", "complaints")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for item in v:
            text = str(item).strip()
            if text and text not in seen:
                seen.append(text)
        return seen


class ExtractionResult(BaseModel):
    """一次抽取的完整结果。"""

    mentions: list[Mention] = Field(default_factory=list)
    city_hint: str | None = None
    source: str = "llm"  # llm | mock
    # 以下字段由 extract_document（清洗 + 切块流水线）填充
    low_trust: bool = False
    ad_hits: list[str] = Field(default_factory=list)
    chunk_count: int = 0

    def filter_by_confidence(self, min_confidence: float) -> "ExtractionResult":
        kept = [m for m in self.mentions if m.confidence >= min_confidence]
        return self.model_copy(update={"mentions": kept})