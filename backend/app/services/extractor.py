"""LLM 抽取服务：文本 chunk → 结构化 mentions。"""

import json
import re

from pydantic import ValidationError

from ..core.config import Settings, get_settings
from ..prompts import build_messages
from ..schemas import ExtractionResult, Mention
from .chunker import chunk_text
from .cleaner import clean_text
from .llm_client import LLMClient


class ExtractionError(RuntimeError):
    pass


_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_code_fence(text: str) -> str:
    return _CODE_FENCE.sub("", text.strip()).strip()


def _dedupe_mentions(mentions: list[Mention]) -> list[Mention]:
    """切块与重叠会导致同一提及重复，按 (店名, 证据片段) 去重。"""
    seen: set[tuple[str, str]] = set()
    unique: list[Mention] = []
    for m in mentions:
        key = (m.shop_name, m.evidence_span or "")
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return unique


class Extractor:
    def __init__(
        self,
        settings: Settings | None = None,
        client: LLMClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.client = client or LLMClient(self.settings)

    def extract(
        self,
        text: str,
        city_hint: str | None = None,
        apply_confidence_filter: bool = False,
    ) -> ExtractionResult:
        if not text or not text.strip():
            return ExtractionResult(mentions=[], city_hint=city_hint)

        messages = build_messages(text, city_hint)
        payload = self._call_with_retry(messages)
        result = ExtractionResult(
            mentions=payload.get("mentions", []),
            city_hint=city_hint,
            source=self._source,
            chunk_count=1,
        )
        if apply_confidence_filter:
            result = result.filter_by_confidence(self.settings.extract_confidence_min)
        return result

    def extract_document(
        self,
        raw_text: str,
        city_hint: str | None = None,
        apply_confidence_filter: bool = False,
    ) -> ExtractionResult:
        """完整流水线（文档 5.2）：清洗 → 语言过滤 → 广告初筛 → 切块 → 逐块抽取。"""
        report = clean_text(raw_text, min_chinese_ratio=self.settings.min_chinese_ratio)

        if not report.text or not report.is_chinese:
            return ExtractionResult(
                mentions=[],
                city_hint=city_hint,
                source=self._source,
                low_trust=report.low_trust,
                ad_hits=report.ad_hits,
            )

        chunks = chunk_text(
            report.text,
            max_tokens=self.settings.extract_max_tokens,
            overlap_tokens=self.settings.chunk_overlap_tokens,
        )

        mentions: list[Mention] = []
        for chunk in chunks:
            mentions.extend(self.extract(chunk, city_hint=city_hint).mentions)

        result = ExtractionResult(
            mentions=_dedupe_mentions(mentions),
            city_hint=city_hint,
            source=self._source,
            low_trust=report.low_trust,
            ad_hits=report.ad_hits,
            chunk_count=len(chunks),
        )
        if apply_confidence_filter:
            result = result.filter_by_confidence(self.settings.extract_confidence_min)
        return result

    @property
    def _source(self) -> str:
        return "mock" if self.client.is_mock else "llm"

    def _call_with_retry(self, messages: list[dict]) -> dict:
        last_error: Exception | None = None
        for _ in range(max(1, self.settings.extract_json_retry)):
            raw = self.client.chat_json(messages)
            try:
                data = json.loads(_strip_code_fence(raw))
                if not isinstance(data, dict):
                    raise ValueError("LLM 输出不是 JSON 对象")
                # 用 pydantic 校验 mentions 字段，失败则重试
                ExtractionResult(mentions=data.get("mentions", []))
                return data
            except (json.JSONDecodeError, ValueError, ValidationError) as exc:
                last_error = exc
        raise ExtractionError(f"抽取失败：{self.settings.extract_json_retry} 次重试后仍无法解析 LLM 输出（{last_error}）")