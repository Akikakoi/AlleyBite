"""切块（对应开发文档 5.2 的第 (5) 步）：长文按段落/句子切成 ≤ max_tokens 的片段。

切块以「原文切片」为单位，因此每个块都带有在清洗后文本中的 start/end 偏移，
便于落库（content_chunk）与 evidence_span 回溯。

Token 估算说明：不同厂商分词器不同，这里用轻量启发式——
CJK 字符按 1 token/字，其余字符按 4 字符/token。估算值恒 ≤ 字符数，
因此"按字符数硬切"可以保证不超限。
"""

import math
import re
from dataclasses import dataclass

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 句子边界：中文标点 / 换行 / 英文感叹疑问分号（不含英文句点，避免切坏小数与网址）
_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")


@dataclass(frozen=True)
class Chunk:
    """一个文本块及其在「清洗后文本」中的位置。"""

    index: int
    text: str
    start: int
    end: int
    token_estimate: int


def estimate_tokens(text: str) -> int:
    cjk = len(_CJK_RE.findall(text))
    others = len(text) - cjk
    return cjk + math.ceil(others / 4)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _SENTENCE_RE.finditer(text) if m.group().strip()]


def _tokens_of(text: str, spans: list[tuple[int, int]]) -> int:
    if not spans:
        return 0
    return estimate_tokens(text[spans[0][0] : spans[-1][1]])


def _tail_overlap(
    text: str, spans: list[tuple[int, int]], budget: int
) -> list[tuple[int, int]]:
    if budget <= 0:
        return []
    seed: list[tuple[int, int]] = []
    total = 0
    for span in reversed(spans):
        tokens = estimate_tokens(text[span[0] : span[1]])
        if total + tokens > budget:
            break
        seed.insert(0, span)
        total += tokens
    return seed


def chunk_with_spans(
    text: str,
    max_tokens: int = 512,
    overlap_tokens: int = 0,
) -> list[Chunk]:
    """切块并保留偏移；每个块的估算 token 数不超过 max_tokens。"""
    if max_tokens <= 0:
        raise ValueError("max_tokens 必须为正整数")
    if not text or not text.strip():
        return []

    chunks: list[Chunk] = []

    def emit(spans: list[tuple[int, int]]) -> None:
        if not spans:
            return
        start, end = spans[0][0], spans[-1][1]
        piece = text[start:end]
        chunks.append(Chunk(len(chunks), piece, start, end, estimate_tokens(piece)))

    current: list[tuple[int, int]] = []
    for start, end in _sentence_spans(text):
        sentence_tokens = estimate_tokens(text[start:end])

        if sentence_tokens > max_tokens:  # 单句超长：先落盘，再按字符硬切
            emit(current)
            current = []
            cursor = start
            while cursor < end:
                stop = min(cursor + max_tokens, end)
                piece = text[cursor:stop]
                chunks.append(
                    Chunk(len(chunks), piece, cursor, stop, estimate_tokens(piece))
                )
                cursor = stop
            continue

        if current and _tokens_of(text, [*current, (start, end)]) > max_tokens:
            emit(current)
            # 重叠预算需给即将加入的句子留位置，否则会复读上一块且无法推进
            budget = min(overlap_tokens, max_tokens - sentence_tokens)
            current = _tail_overlap(text, current, budget)

        current.append((start, end))

    emit(current)
    return chunks


def chunk_text(
    text: str,
    max_tokens: int = 512,
    overlap_tokens: int = 0,
) -> list[str]:
    """把文本切成若干片段（只返回文本，不含偏移）。"""
    return [c.text for c in chunk_with_spans(text, max_tokens, overlap_tokens)]