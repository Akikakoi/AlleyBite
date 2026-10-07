"""M0 抽检：跑通「文本 → 清洗切块 → LLM 抽取 → 结构化 mentions」，统计店名召回/精度。

用法（在 backend 目录下）：
    python scripts/demo_extract.py                                   # 默认合成样本
    python scripts/demo_extract.py --file samples/seeds.reports.jsonl --city 成都

无 LLM_API_KEY 时自动走 mock 模式，可离线验证链路是否打通。传入 --file 可对
真实公开报道种子（seeds.reports.jsonl）做 M0 抽检，按 --city 限定单城口径。
"""

import argparse
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


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AlleyBite M0 抽取抽检")
    parser.add_argument(
        "--file",
        default=str(ROOT / "samples" / "samples.jsonl"),
        help="抽检样本 JSONL（默认 samples/samples.jsonl）",
    )
    parser.add_argument("--city", default=None, help="仅抽检指定城市的样本（默认全部）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    extractor = Extractor(settings)
    mode = "mock（离线）" if settings.use_mock else f"live：{settings.llm_model}"
    print(f"LLM 模式：{mode}\n" + "=" * 60)

    samples_path = Path(args.file)
    lines = [ln for ln in samples_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    samples = [json.loads(ln) for ln in lines]
    if args.city:
        samples = [s for s in samples if s.get("city_hint") == args.city]
    print(f"样本文件：{samples_path}　城市：{args.city or '全部'}　样本数：{len(samples)}")

    total_expected = 0
    total_hit = 0
    total_got = 0
    total_correct = 0
    misses: list[str] = []
    hallucinations: list[str] = []

    for idx, sample in enumerate(samples, 1):
        text = sample.get("text") or sample.get("raw_text") or ""
        result = extractor.extract_document(text, city_hint=sample.get("city_hint"))
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
        label = sample.get("id") or sample.get("raw_title") or f"#{idx}"
        print(f"[{label}] 期望 {expected} → 抽取 {got}  {status}")
        for m in result.mentions:
            print(
                f"    - {m.shop_name} | {m.sentiment} | 人均 {m.avg_price} | "
                f"标签 {m.praise_keywords} | conf {m.confidence}"
            )
        print()

    recall = total_hit / total_expected if total_expected else 0.0
    precision = total_correct / total_got if total_got else 0.0
    print("=" * 60)
    print(f"样本数：{len(samples)}")
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