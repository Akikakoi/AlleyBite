"""LLM 抽取 Prompt（对应开发文档 5.3 与附录 A）。"""

SYSTEM_PROMPT = """你是一个美食信息结构化抽取引擎。仅依据用户提供的文本内容抽取信息，不得臆造。
若文本未提及某字段，返回 null。输出必须是合法 JSON，不要输出解释、Markdown 代码块或多余文字。

输出 JSON 结构：
{
  "mentions": [
    {
      "shop_name": "店名（原文写法，不含“这家/那家”等指代）",
      "address_text": "文中提到的地址或位置描述，未提及为 null",
      "area": "所在行政区/商圈，可推断则填，否则 null",
      "dishes": ["推荐/提到的菜品"],
      "avg_price": 65,
      "cuisine": "菜系",
      "sentiment": "positive|neutral|negative|mixed",
      "praise_keywords": ["正面口碑关键词，2-6 个短词"],
      "complaints": ["提到的缺点"],
      "is_recommendation": true,
      "confidence": 0.86,
      "evidence_span": "支撑该结论的原文片段"
    }
  ]
}

规则：
1. 合集帖中的“标题店名”若正文无对应描述，不要输出。
2. 明显为广告/团购/合作推广的店铺，sentiment 标 neutral，并降低 confidence。
3. 不要输出非餐饮店铺（如酒店、景点）。
4. 没有找到任何餐饮店铺时返回 {"mentions": []}。
5. evidence_span 必须是原文中的连续片段，不得改写。"""


_FEW_SHOT: list[tuple[str, str]] = [
    (
        "城市线索：成都\n文本：\n昨天去吃了明婷饭店，藏在青羊区同心路的巷子里，"
        "环境确实一般，但脑花豆腐和霸王兔太香了，锅气足，嬢嬢也热情，"
        "人均才 65，就是饭点要排队。",
        '{"mentions":[{"shop_name":"明婷饭店","address_text":"青羊区同心路的巷子里",'
        '"area":"青羊区","dishes":["脑花豆腐","霸王兔"],"avg_price":65,"cuisine":"川菜",'
        '"sentiment":"positive","praise_keywords":["锅气足","嬢嬢热情","性价比高"],'
        '"complaints":["环境一般","饭点排队"],"is_recommendation":true,"confidence":0.9,'
        '"evidence_span":"环境确实一般，但脑花豆腐和霸王兔太香了，锅气足"}]}',
    ),
    (
        "城市线索：成都\n文本：\n成都本地人才知道的苍蝇馆子合集（标题：红光冒菜也上榜了）。"
        "第 1 家王妈手撕烤兔，开在玉林，兔头麻辣入味，人均 40，"
        "本地人排长队都要买。",
        '{"mentions":[{"shop_name":"王妈手撕烤兔","address_text":"玉林","area":"武侯区",'
        '"dishes":["手撕烤兔","兔头"],"avg_price":40,"cuisine":"川菜/小吃",'
        '"sentiment":"positive","praise_keywords":["麻辣入味","本地人常去"],'
        '"complaints":[],"is_recommendation":true,"confidence":0.85,'
        '"evidence_span":"王妈手撕烤兔，开在玉林，兔头麻辣入味，人均 40"}]}',
    ),
    (
        "城市线索：成都\n文本：\n被安利去了春熙路的耍酒馆·冒菜，环境是网红 ins 风，"
        "一份冒菜 128，量还少，味道很一般，不会再去了。",
        '{"mentions":[{"shop_name":"耍酒馆·冒菜","address_text":"春熙路","area":"锦江区",'
        '"dishes":["冒菜"],"avg_price":128,"cuisine":"冒菜","sentiment":"negative",'
        '"praise_keywords":[],"complaints":["价格偏高","分量少","味道一般"],'
        '"is_recommendation":false,"confidence":0.88,'
        '"evidence_span":"一份冒菜 128，量还少，味道很一般"}]}',
    ),
    (
        "城市线索：成都\n文本：\n【探店合作】成都新开的沸腾里火锅，团购链接见评论区，"
        "商务合作请私信。",
        '{"mentions":[{"shop_name":"沸腾里火锅","address_text":null,"area":null,'
        '"dishes":[],"avg_price":null,"cuisine":"火锅","sentiment":"neutral",'
        '"praise_keywords":[],"complaints":[],"is_recommendation":false,'
        '"confidence":0.35,"evidence_span":"成都新开的沸腾里火锅，团购链接见评论区"}]}',
    ),
]


def build_messages(chunk_text: str, city_hint: str | None = None) -> list[dict]:
    """构造 OpenAI 兼容的 messages：system + few-shot + 当前用户文本。"""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for user_text, assistant_json in _FEW_SHOT:
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": assistant_json})

    user_content = f"城市线索：{city_hint or '未知'}\n文本：\n{chunk_text}"
    messages.append({"role": "user", "content": user_content})
    return messages