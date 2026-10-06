"""LLM 客户端（OpenAI 兼容协议），并提供离线 mock 模式。"""

from ..core.config import Settings, get_settings
from .metrics import record_llm_call

# 离线 mock 的固定返回，仅用于 demo / 单测，保证无 API Key 也能跑通链路。
_MOCK_FIXTURES: list[tuple[str, str]] = [
    (
        "明婷饭店",
        '{"mentions":[{"shop_name":"明婷饭店","address_text":"青羊区同心路的巷子里",'
        '"area":"青羊区","dishes":["脑花豆腐","霸王兔"],"avg_price":65,"cuisine":"川菜",'
        '"sentiment":"positive","praise_keywords":["锅气足","嬢嬢热情","性价比高"],'
        '"complaints":["环境一般","饭点排队"],"is_recommendation":true,"confidence":0.9,'
        '"evidence_span":"环境确实一般，但脑花豆腐和霸王兔太香了"}]}',
    ),
    (
        "王妈手撕烤兔",
        '{"mentions":[{"shop_name":"王妈手撕烤兔","address_text":"玉林","area":"武侯区",'
        '"dishes":["手撕烤兔","兔头"],"avg_price":40,"cuisine":"川菜/小吃",'
        '"sentiment":"positive","praise_keywords":["麻辣入味","本地人常去"],'
        '"complaints":[],"is_recommendation":true,"confidence":0.85,'
        '"evidence_span":"王妈手撕烤兔，开在玉林，兔头麻辣入味，人均 40"}]}',
    ),
    (
        "耍酒馆",
        '{"mentions":[{"shop_name":"耍酒馆·冒菜","address_text":"春熙路","area":"锦江区",'
        '"dishes":["冒菜"],"avg_price":128,"cuisine":"冒菜","sentiment":"negative",'
        '"praise_keywords":[],"complaints":["价格偏高","分量少","味道一般"],'
        '"is_recommendation":false,"confidence":0.88,'
        '"evidence_span":"一份冒菜 128，量还少，味道很一般"}]}',
    ),
    (
        "沸腾里",
        '{"mentions":[{"shop_name":"沸腾里火锅","address_text":null,"area":null,'
        '"dishes":[],"avg_price":null,"cuisine":"火锅","sentiment":"neutral",'
        '"praise_keywords":[],"complaints":[],"is_recommendation":false,'
        '"confidence":0.35,"evidence_span":"成都新开的沸腾里火锅，团购链接见评论区"}]}',
    ),
]


class LLMClient:
    """统一封装大模型调用；mock 模式下不发起网络请求。"""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = None

    @property
    def is_mock(self) -> bool:
        return self.settings.use_mock

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url,
                timeout=self.settings.llm_timeout,
            )
        return self._client

    def chat_json(self, messages: list[dict]) -> str:
        """返回模型输出的原始字符串（期望为 JSON）。"""
        if self.is_mock:
            return self._mock_reply(messages)

        provider = self.settings.llm_provider
        try:
            resp = self._get_client().chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                response_format={"type": "json_object"},
            )
        except Exception:
            record_llm_call(provider, ok=False)
            raise

        usage = getattr(resp, "usage", None)
        record_llm_call(
            provider,
            ok=True,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def _mock_reply(messages: list[dict]) -> str:
        text = messages[-1]["content"] if messages else ""
        for keyword, payload in _MOCK_FIXTURES:
            if keyword in text:
                return payload
        return '{"mentions":[]}'