"""M0 抽检：跑通「文本 → 清洗切块 → LLM 抽取 → 结构化 mentions」，统计店名召回/精度。

用法（在 backend 目录下）：
    python scripts/demo_extract.py

无 LLM_API_KEY 时自动走 mock 模式，可离线验证链路是否打通。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services import Extractor  # noqa: E402


def _match(expected: str, got: str) -> bool:
    """容错匹配：双向包含即视为命中（如「耍酒馆」↔「耍酒馆·冒菜」）。"""
    return expected in got or got in expected


def main() -> None:
    settings = get_settings()
    extractor = Extractor(settings)
    mode = "mock（离线）" if settings.use_mock else f"live：{settings.llm_model}"
    print(f"LLM 模式：{mode}\n" + "=" * 60)

    samples_path = ROOT / "samples" / "samples.jsonl"
    lines = [ln for ln in samples_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    total_expected = 0
    total_hit = 0
    total_got = 0
    total_correct = 0
    misses: list[str] = []
    hallucinations: list[str] = []

    for line in lines:
        sample = json.loads(line)
        result = extractor.extract_document(sample["text"], city_hint=sample.get("city_hint"))
        got = [m.shop_name for m in result.mentions]
        expected = sample.get("expected_shops", [])

        hits = [name for name in expected if any(_match(name, g) for g in got)]
        correct = [g for g in got if any(_match(name, g) for name in expected)]
        extra = [g for g in got if not any(_match(name, g) for name in expected)]

        total_expected += len(expected)
        total_hit += len(hits)
        total_got += len(got)
        total_correct += len(correct)
        misses.extend(name for name in expected if name not in hits)
        hallucinations.extend(extra)

        status = "OK" if len(hits) == len(expected) and not extra else "CHECK"
        print(f"[{sample['id']}] 期望 {expected} → 抽取 {got}  {status}")
        for m in result.mentions:
            print(
                f"    - {m.shop_name} | {m.sentiment} | 人均 {m.avg_price} | "
                f"标签 {m.praise_keywords} | conf {m.confidence}"
            )
        print()

    recall = total_hit / total_expected if total_expected else 0.0
    precision = total_correct / total_got if total_got else 0.0
    print("=" * 60)
    print(f"样本数：{len(lines)}")
    print(f"店名召回 recall：{total_hit}/{total_expected} = {recall:.0%}")
    print(f"店名精度 precision：{total_correct}/{total_got} = {precision:.0%}")
    if misses:
        print(f"漏抽：{misses}")
    if hallucinations:
        print(f"疑似幻觉/多抽：{hallucinations}")
    if settings.use_mock:
        print("提示：mock 模式返回预置结果，仅验证链路；真实准确率请配置 API Key 后重跑。")


if __name__ == "__main__":
    main()