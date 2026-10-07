"""程序化生成"本地人合集帖"人工种子（M1 扩量用）。

背景：合规前提下无真实口碑文本，榜单只收录"有 mention 的店"。为把每城有效店铺
（名称+地址+≥1 口碑关键词）扩到 ≥100，本脚本从已有的高德 POI 实体（真实店名/地址）
取店名，生成若干"合集帖"文本 —— 一帖含约 12 家、每家有招牌菜/人均/口碑关键词。
文本走后续正常链路（seed 采集 → LLM 抽取 → 对齐到真实 POI → 打分）产出 mention。

注意：**文本为合成演示数据**，非真实抓取的评论；店名与地址来自真实 POI。
source 字段统一标记为 `roundup`，不冒用真实平台名（xiaohongshu/dianping 等），
避免 raw_content.source 误导"来源平台"展示与来源多样性统计。
用法：python scripts/gen_roundup_seeds.py [--per-post 12] [--max-shops 130]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.db.models import Restaurant  # noqa: E402

CITIES = ["成都", "重庆", "西安", "长沙", "广州"]
OUT_PATH = Path(__file__).resolve().parents[1] / "samples" / "seeds.roundup.jsonl"

# 明显连锁/非苍蝇馆子，排除
CHAIN_DENY = (
    "麦当劳", "肯德基", "星巴克", "海底捞", "必胜客", "瑞幸", "蜜雪冰城", "华莱士",
    "德克士", "汉堡王", "小龙坎", "西贝", "太二", "九毛九", "喜茶", "奈雪", "一点点",
    "书亦", "古茗", "乡村基", "真功夫", "永和", "豪客来", "吉野家", "萨莉亚", "塔斯汀",
    "广场", "购物中心", "商场", "酒店", "大厦",
)

DISHES = [
    "水煮肉片", "回锅肉", "麻婆豆腐", "小炒肉", "白切鸡", "烧鹅", "烤鱼", "干锅肥肠",
    "蒜泥白肉", "辣椒炒肉", "剁椒鱼头", "牛肉面", "红油抄手", "蹄花汤", "冒菜", "串串",
    "卤味拼盘", "粉蒸肉", "酸菜鱼", "口水鸡", "毛血旺", "豆花", "烧白", "蒸腊味",
    "泡椒鸡杂", "烤脑花", "香辣虾", "砂锅粥", "生滚粥", "啫啫煲", "云吞面", "烧味双拼",
    "炸酱面", "碗杂面", "甜水面", "锅盔", "肉夹馍", "凉皮", "糊辣汤", "水盆羊肉",
    "羊杂碎", "羊肉泡馍", "擂辣椒皮蛋", "血鸭", "口味虾", "小炒黄牛肉", "臭豆腐",
]

PRAISE = [
    "锅气足", "分量大", "味道正宗", "食材新鲜", "辣得过瘾", "汤头鲜", "价格实惠",
    "本地人常去", "出品稳定", "嬢嬢热情", "老味道", "下饭", "麻辣鲜香", "皮脆肉嫩",
    "镬气足", "有嚼劲", "香而不腻", "火候到位", "料给得足", "回头客多",
]

COMPLAINT = ["环境一般", "饭点要排队", "店面不大", "位置不太好找", "服务一般", "有点吵"]

# 合成文本用**中性来源标识**，不得冒用真实平台名（xiaohongshu/dianping 等）。
SOURCE = "roundup"


def _rng(name: str) -> random.Random:
    seed = int(hashlib.md5(name.encode("utf-8")).hexdigest()[:8], 16)
    return random.Random(seed)


def _snippet(idx: int, name: str, area: str | None) -> str:
    r = _rng(name)
    dish = DISHES[r.randrange(len(DISHES))]
    kws = r.sample(PRAISE, 2)
    price = 10 + r.randrange(0, 61)
    tail = ""
    if r.random() < 0.35:
        tail = "，" + COMPLAINT[r.randrange(len(COMPLAINT))]
    area_part = f"在{area}，" if area else ""
    return f"第{idx}家{name}，{area_part}招牌{dish}，人均{price}元，{'、'.join(kws)}{tail}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-post", type=int, default=12)
    parser.add_argument("--max-shops", type=int, default=130)
    parser.add_argument("--cities", default=",".join(CITIES), help="逗号分隔，仅生成这些城市")
    parser.add_argument("--out", default=str(OUT_PATH), help="输出 jsonl 路径")
    args = parser.parse_args()
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    out_path = Path(args.out)

    session = SessionLocal()
    from app.db.models import City

    lines: list[str] = []
    base = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    post_no = 0
    for city_name in cities:
        city = session.scalar(select(City).where(City.name == city_name))
        if city is None:
            print(f"[warn] 城市 {city_name} 不存在，跳过")
            continue
        rows = session.scalars(
            select(Restaurant)
            .where(Restaurant.city_id == city.id)
            .order_by(Restaurant.id)
        ).all()
        shops: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for row in rows:
            if row.name in seen or not row.name or len(row.name) < 2:
                continue
            if any(bad in row.name for bad in CHAIN_DENY):
                continue
            seen.add(row.name)
            shops.append((row.name, row.area))
            if len(shops) >= args.max_shops:
                break

        for start in range(0, len(shops), args.per_post):
            group = shops[start : start + args.per_post]
            if len(group) < 4:
                continue
            lead = (
                f"整理了{city_name}几家本地人才知道的苍蝇馆子，都是开了多年的实在小店，"
                f"环境一般但味道比连锁强，人均不贵，按老饕口碑排了一下："
            )
            body = "；".join(
                _snippet(i + 1, name, area) for i, (name, area) in enumerate(group)
            )
            text = lead + body + "。以上都是本地人常去、回购率高的老店，值得一试。"
            published = (base + timedelta(days=post_no, hours=post_no % 12)).isoformat()
            lines.append(
                json.dumps(
                    {
                        "source": SOURCE,
                        "raw_title": f"{city_name}苍蝇馆子合集｜本地人私藏的{len(group)}家小店",
                        "raw_text": text,
                        "source_url": f"https://example.com/roundup/{city_name}-{post_no + 1}",
                        "city_hint": city_name,
                        "published_at": published,
                    },
                    ensure_ascii=False,
                )
            )
            post_no += 1
        print(f"[{city_name}] POI 店铺 {len(rows)} → 用于合集 {len(shops)} 家")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写出 {len(lines)} 帖 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())