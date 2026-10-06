"""文本清洗（对应开发文档 5.2 的 (1)~(4) 步）。

顺序：去噪 → 文本规整 → 语言过滤 → 广告/软文初筛。
本模块为纯函数，不依赖配置对象，参数由调用方传入。
"""

import re

from opencc import OpenCC
from pydantic import BaseModel, Field

# 营销词库（用于广告/软文初筛，命中即标 low_trust）
DEFAULT_AD_WORDS: list[str] = [
    "探店",
    "商务合作",
    "商务推广",
    "团购链接",
    "合作私信",
    "点击链接",
    "优惠券",
    "限时抢购",
]

# 明显噪声模板（全文展开/引导关注等），非正文内容
_TEMPLATE_PATTERNS = [
    re.compile(r"(点击|戳)[^，。！？\n]{0,8}(查看|阅读)(全文|原文)"),
    re.compile(r"(展开|收起)全文"),
    re.compile(r"扫码(关注|点餐|下单)"),
    re.compile(r"关注(公众)?号"),
]

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"(https?://|www\.)\S+", re.I)
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f0ff"
    "\u2b00-\u2bff\ufe0f\u200d]+"
)
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\ufeff]")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

# 单位归一：把"块/块钱/人民币/RMB/元"统一成"元"，并规范为"数字 元"
_RMB_PREFIX_RE = re.compile(r"[￥¥]\s*(\d+(?:\.\d+)?)")
_RMB_SUFFIX_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:块钱|块|人民币|RMB|rmb|元)")
_RMB_PREFIX_WORD_RE = re.compile(r"(?:RMB|rmb)\s*(\d+(?:\.\d+)?)")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_opencc = OpenCC("t2s")


class CleanReport(BaseModel):
    """清洗结果与判定标签。"""

    text: str
    is_chinese: bool
    low_trust: bool
    ad_hits: list[str] = Field(default_factory=list)


def _to_halfwidth(text: str) -> str:
    """全角数字/字母/空格 → 半角（保留中文标点，避免破坏断句与引用）。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            out.append(" ")
        elif 0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _denoise(text: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    for pattern in _TEMPLATE_PATTERNS:
        text = pattern.sub(" ", text)
    text = _EMOJI_RE.sub(" ", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _to_halfwidth(text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    # 去噪会在标签/表情位置留下空格，收敛中文之间的多余空格（保留换行）
    text = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"[ \t]+(?=[，。！？；：、）】」』])", "", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text


def _normalize(text: str) -> str:
    text = _opencc.convert(text)  # 繁体 → 简体
    text = _RMB_PREFIX_RE.sub(r"\1 元", text)
    text = _RMB_PREFIX_WORD_RE.sub(r"\1 元", text)
    text = _RMB_SUFFIX_RE.sub(r"\1 元", text)
    return text


def _chinese_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    cjk = sum(1 for c in chars if _CJK_RE.match(c))
    return cjk / len(chars)


def _find_ad_hits(text: str, ad_words: list[str]) -> list[str]:
    return [word for word in ad_words if word in text]


def clean_text(
    raw: str,
    *,
    ad_words: list[str] | None = None,
    min_chinese_ratio: float = 0.5,
) -> CleanReport:
    """执行 5.2 的 (1)~(4) 步清洗。

    - ad_words: 营销词库，默认使用内置 DEFAULT_AD_WORDS
    - min_chinese_ratio: 中文占比阈值，低于该值判定为非中文内容
    """
    if not raw or not raw.strip():
        return CleanReport(text="", is_chinese=False, low_trust=False, ad_hits=[])

    cleaned = _denoise(raw)
    cleaned = _normalize(cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned).strip()

    # 广告初筛在规整后的文本上做，避免被去噪步骤清掉信号
    hits = _find_ad_hits(cleaned, ad_words if ad_words is not None else DEFAULT_AD_WORDS)

    return CleanReport(
        text=cleaned,
        is_chinese=_chinese_ratio(cleaned) >= min_chinese_ratio,
        low_trust=bool(hits),
        ad_hits=hits,
    )