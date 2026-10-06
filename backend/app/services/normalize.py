"""名称 / 地址归一化工具（文档 5.5 实体对齐的基础步骤）。

纯函数，不依赖数据库与配置，供 alignment 与 scoring 复用。
"""

import difflib
import re

from opencc import OpenCC

_MODIFIER_PREFIX = ("老字号", "正宗", "老")
_MODIFIER_SUFFIX = ("旗舰店", "总店", "分店", "新店", "老店")
_BRACKET_RE = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")
_SPACE_RE = re.compile(r"[\s·・]+")
_ADDR_SPLIT_RE = re.compile(r"[\s,，。.、;；:：/\\\-—()（）【】\[\]]+")

_opencc = OpenCC("t2s")


def normalize_shop_name(name: str) -> str:
    """店名归一化，用作实体键。

    去括号 → 简繁统一 → 去修饰词前后缀 → 去空格/间隔符。
    """
    text = _BRACKET_RE.sub("", name or "")
    text = _opencc.convert(text)
    text = _SPACE_RE.sub("", text)
    for prefix in _MODIFIER_PREFIX:
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix):]
            break
    for suffix in _MODIFIER_SUFFIX:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text or (name or "").strip()


def address_tokens(address: str | None) -> set[str]:
    """地址 token 集合，用于重合度计算；中文用 2-gram 近似分词。"""
    if not address:
        return set()
    text = _opencc.convert(_BRACKET_RE.sub("", address))
    text = _SPACE_RE.sub("", text)
    if not text:
        return set()

    tokens: set[str] = set()
    for part in _ADDR_SPLIT_RE.split(text):
        if not part:
            continue
        if len(part) <= 2:
            tokens.add(part)
        else:
            tokens.update(part[i : i + 2] for i in range(len(part) - 1))
    return tokens


def name_similarity(a: str, b: str) -> float:
    """名称相似度 [0,1]；包含关系视为强匹配（处理"明婷 / 明婷饭店"）。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def address_similarity(a: str | None, b: str | None) -> float | None:
    """地址 token 重合度（Jaccard）；任一侧缺失返回 None（视为未知）。"""
    ta, tb = address_tokens(a), address_tokens(b)
    if not ta or not tb:
        return None
    return len(ta & tb) / len(ta | tb)