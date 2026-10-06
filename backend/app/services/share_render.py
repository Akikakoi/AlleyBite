"""分享预览 HTML（文档 9.4 预渲染 / 14 章"分享链接有标题/摘要预览"）。

nginx 识别爬虫 UA 后把 `/detail/:id`、`/rank` 分流到这里，返回带动态
`<title>` 与 `og:*` 元信息的最小 HTML：爬虫拿到预览，真人则被脚本送回 SPA
路由（避免 UA 误判把人留在裸页面上）。

所有注入值均经 HTML 转义，店铺名/摘要里的特殊字符不会破坏文档结构。
"""

from __future__ import annotations

import json
from html import escape

from ..core.config import Settings

_DESCRIPTION_LIMIT = 110


def resolve_base_url(request, settings: Settings) -> str:
    """对外基址：优先配置，其次按反代头（X-Forwarded-Proto/Host）推导。"""
    if settings.share_base_url:
        return settings.share_base_url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def absolute_url(base_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def resolve_og_image(base_url: str, settings: Settings) -> str | None:
    if not settings.share_og_image:
        return None
    return absolute_url(base_url, settings.share_og_image)


def _clip(text: str, limit: int = _DESCRIPTION_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _format_price(value) -> str | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return f"¥{num:.0f}" if num.is_integer() else f"¥{num:.1f}"


def restaurant_share_text(detail: dict) -> tuple[str, str]:
    """店铺详情 → (标题, 摘要)。"""
    name = detail.get("name") or "苍蝇馆子"
    title = f"{name}｜本地人都在吃的苍蝇馆子"

    facts = [str(x) for x in (detail.get("area"), detail.get("cuisine")) if x]
    price = _format_price(detail.get("avg_price"))
    if price:
        facts.append(f"人均 {price}")
    score = detail.get("score")
    if score:
        facts.append(f"综合分 {float(score):.1f}")

    description = " · ".join(facts)
    dishes = [d for d in (detail.get("recommended_dishes") or []) if d][:3]
    if dishes:
        description = f"{description}；推荐菜 {'、'.join(dishes)}" if description else f"推荐菜 {'、'.join(dishes)}"
    keywords = [k for k in (detail.get("praise_keywords") or []) if k][:3]
    if keywords:
        description = f"{description}；口碑 {'、'.join(keywords)}" if description else f"口碑 {'、'.join(keywords)}"
    return title, _clip(description or "多源公开数据聚合的苍蝇馆子榜单")


def rank_share_text(city: str, items: list[dict]) -> tuple[str, str]:
    """城市榜单 → (标题, 摘要)：取 Top3 店名做摘要。"""
    title = f"{city}苍蝇馆子榜｜本地人才知道的宝藏小店"
    names = [i.get("name") for i in items[:3] if i.get("name")]
    if names:
        return title, _clip(f"Top3：{'、'.join(names)}")
    return title, f"{city}的苍蝇馆子收录中，开城后第一时间更新榜单"


def render_share_html(
    *,
    title: str,
    description: str,
    base_url: str,
    page_path: str,
    site_name: str,
    image_url: str | None = None,
    og_type: str = "website",
) -> str:
    """渲染分享预览页；page_path 为对应的 SPA 路由（如 /detail/12）。"""
    url = absolute_url(base_url, page_path)
    t, d, s = escape(title), escape(description), escape(site_name)
    u = escape(url)

    image_tags = ""
    if image_url:
        img = escape(image_url)
        image_tags = (
            f'<meta property="og:image" content="{img}" />\n'
            f'    <meta name="twitter:card" content="summary_large_image" />\n'
            f'    <meta name="twitter:image" content="{img}" />'
        )
    else:
        image_tags = '<meta name="twitter:card" content="summary" />'

    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{t}</title>
    <meta name="description" content="{d}" />
    <link rel="canonical" href="{u}" />
    <meta property="og:type" content="{escape(og_type)}" />
    <meta property="og:site_name" content="{s}" />
    <meta property="og:title" content="{t}" />
    <meta property="og:description" content="{d}" />
    <meta property="og:url" content="{u}" />
    {image_tags}
    <meta name="twitter:title" content="{t}" />
    <meta name="twitter:description" content="{d}" />
    <script>location.replace({json.dumps(page_path)});</script>
  </head>
  <body>
    <p><a href="{escape(page_path)}">{t}</a></p>
  </body>
</html>
"""