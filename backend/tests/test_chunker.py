import pytest

from app.services.chunker import chunk_text, chunk_with_spans, estimate_tokens


def test_empty_input():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_short_text_single_chunk():
    text = "明婷饭店很好吃。脑花豆腐很香。"
    chunks = chunk_text(text, max_tokens=512)
    assert chunks == [text]


def test_invalid_max_tokens():
    with pytest.raises(ValueError):
        chunk_text("随便一段话。", max_tokens=0)


def test_chunks_respect_max_tokens():
    text = "这是一家很好吃的苍蝇馆子。" * 200
    chunks = chunk_text(text, max_tokens=100)
    assert len(chunks) > 1
    assert all(estimate_tokens(c) <= 100 for c in chunks)
    # 内容不丢失
    assert "".join(chunks) == text


def test_hard_split_single_long_sentence():
    text = "好" * 1000  # 无标点，单句超长
    chunks = chunk_text(text, max_tokens=100)
    assert len(chunks) == 10
    assert all(len(c) <= 100 for c in chunks)


def test_overlap_carries_tail_sentence():
    text = "句子一。句子二。句子三。句子四。"
    chunks = chunk_text(text, max_tokens=11, overlap_tokens=5)
    assert chunks == ["句子一。句子二。句子三。", "句子三。句子四。"]
    # 上一块的最后一句被带入下一块（重叠生效）
    assert chunks[1].startswith("句子三。")
    # 无重复复读，且每块不超限
    assert len(chunks) == len(set(chunks))
    assert all(estimate_tokens(c) <= 11 for c in chunks)


def test_chunk_spans_align_with_source_text():
    text = "好句子。" * 100
    chunks = chunk_with_spans(text, max_tokens=10)
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
        assert estimate_tokens(chunk.text) <= 10
    assert chunks[0].start == 0
    assert chunks[-1].end == len(text)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_estimate_tokens_cjk_vs_ascii():
    assert estimate_tokens("你好") == 2  # 2 个 CJK 字
    assert estimate_tokens("abcd") == 1  # 4 字符 / 4
    assert estimate_tokens("") == 0