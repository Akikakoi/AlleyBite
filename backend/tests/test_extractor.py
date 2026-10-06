import json

import pytest

from app.core.config import Settings
from app.schemas import ExtractionResult, Mention
from app.services import Extractor
from app.services.extractor import ExtractionError


def make_settings(**overrides) -> Settings:
    base = dict(llm_mock=True, llm_api_key="", extract_json_retry=3, extract_confidence_min=0.6)
    base.update(overrides)
    return Settings(_env_file=None, **base)


class FakeClient:
    """按顺序返回预设字符串，模拟模型输出。"""

    is_mock = False

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls = 0

    def chat_json(self, messages):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


def test_mock_extract_single_shop():
    extractor = Extractor(make_settings())
    result = extractor.extract("去吃了明婷饭店，脑花豆腐太香了", city_hint="成都")

    assert result.source == "mock"
    assert result.city_hint == "成都"
    assert len(result.mentions) == 1
    assert result.mentions[0].shop_name == "明婷饭店"
    assert result.mentions[0].sentiment == "positive"
    assert "脑花豆腐" in result.mentions[0].dishes


def test_empty_text_returns_empty():
    extractor = Extractor(make_settings())
    result = extractor.extract("   ")
    assert result.mentions == []


def test_confidence_filter_drops_ad():
    extractor = Extractor(make_settings())
    raw = extractor.extract("【探店合作】成都新开的沸腾里火锅，团购链接见评论区")
    filtered = extractor.extract(
        "【探店合作】成都新开的沸腾里火锅，团购链接见评论区",
        apply_confidence_filter=True,
    )
    assert len(raw.mentions) == 1
    assert raw.mentions[0].confidence < 0.6
    assert filtered.mentions == []


def test_json_retry_recovers_from_bad_output():
    client = FakeClient(["不是 json", "```json\n{\"mentions\": []}\n```"])
    extractor = Extractor(make_settings(), client=client)
    result = extractor.extract("随便一段文本")
    assert result.mentions == []
    assert client.calls == 2


def test_json_retry_exhausted_raises():
    client = FakeClient(["坏输出"])
    extractor = Extractor(make_settings(extract_json_retry=2), client=client)
    with pytest.raises(ExtractionError):
        extractor.extract("随便一段文本")
    assert client.calls == 2


def test_mention_clamps_confidence_and_dedupes():
    mention = Mention(
        shop_name="X店",
        confidence=1.8,
        dishes=["兔头", "兔头", " "],
    )
    assert mention.confidence == 1.0
    assert mention.dishes == ["兔头"]


def test_extract_document_cleans_html_and_extracts():
    extractor = Extractor(make_settings(extract_max_tokens=64))
    raw = "<p>昨天去吃了明婷饭店😋，脑花豆腐太香了，锅气足。</p><script>x</script>"
    result = extractor.extract_document(raw, city_hint="成都")
    assert result.chunk_count >= 1
    assert [m.shop_name for m in result.mentions] == ["明婷饭店"]


def test_extract_document_skips_non_chinese():
    extractor = Extractor(make_settings())
    result = extractor.extract_document("this is a pure english review about food")
    assert result.mentions == []
    assert result.chunk_count == 0


def test_extract_document_marks_low_trust():
    extractor = Extractor(make_settings())
    result = extractor.extract_document("【探店合作】成都新开的沸腾里火锅，团购链接见评论区")
    assert result.low_trust is True
    assert "探店" in result.ad_hits


def test_result_dump_is_json_serializable():
    result = ExtractionResult(mentions=[Mention(shop_name="X店")])
    assert json.loads(json.dumps(result.model_dump()))["mentions"][0]["shop_name"] == "X店"