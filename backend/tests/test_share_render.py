"""分享预览单测（文档 9.4 预渲染 / 14 章"分享链接有标题/摘要预览"）。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.db.base import Base
from app.db.models import City, Restaurant
from app.main import app
from app.services.share_render import (
    absolute_url,
    rank_share_text,
    render_share_html,
    restaurant_share_text,
)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- 纯函数 -----------------------------------------------------------------

def test_absolute_url_keeps_absolute_and_joins_relative():
    assert absolute_url("https://x.com/", "/detail/1") == "https://x.com/detail/1"
    assert absolute_url("https://x.com", "https://y.com/a") == "https://y.com/a"


def test_render_share_html_has_og_meta_and_escapes_values():
    html = render_share_html(
        title='明婷"饭店" <总店>',
        description="人均 ¥65 & 推荐菜",
        base_url="https://x.com",
        page_path="/detail/12",
        site_name="苍蝇馆子美食发现器",
        image_url="https://x.com/og-default.png",
    )

    assert "<title>明婷" in html
    assert "&lt;总店&gt;" in html and "&quot;饭店&quot;" in html
    assert "&amp;" in html
    assert 'property="og:title"' in html
    assert 'property="og:url" content="https://x.com/detail/12"' in html
    assert 'property="og:image" content="https://x.com/og-default.png"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert 'location.replace("/detail/12")' in html


def test_render_share_html_without_image_degrades_to_summary_card():
    html = render_share_html(
        title="t",
        description="d",
        base_url="https://x.com",
        page_path="/rank?city=%E6%88%90%E9%83%BD",
        site_name="s",
    )

    assert 'property="og:image"' not in html
    assert 'name="twitter:card" content="summary"' in html


def test_restaurant_share_text_summarises_facts():
    title, description = restaurant_share_text(
        {
            "name": "明婷饭店",
            "area": "青羊区",
            "cuisine": "川菜",
            "avg_price": 65,
            "score": 82.5,
            "recommended_dishes": ["脑花豆腐", "霸王兔"],
            "praise_keywords": ["锅气足"],
        }
    )

    assert "明婷饭店" in title
    assert "青羊区" in description
    assert "人均 ¥65" in description
    assert "综合分 82.5" in description
    assert "脑花豆腐" in description
    assert "锅气足" in description


def test_rank_share_text_with_and_without_items():
    title, description = rank_share_text("成都", [{"name": "A"}, {"name": "B"}])
    assert "成都" in title
    assert "A、B" in description

    title, description = rank_share_text("西安", [])
    assert "西安" in title
    assert "收录中" in description


# --- HTTP 接口 --------------------------------------------------------------

def test_share_restaurant_endpoint_returns_html(client, session):
    city = City(name="成都")
    session.add(city)
    session.flush()
    session.add(
        Restaurant(
            city_id=city.id,
            name="明婷饭店",
            name_norm="明婷饭店",
            area="青羊区",
            cuisine="川菜",
            avg_price=65,
            status="active",
        )
    )
    session.commit()
    restaurant_id = session.scalars(select(Restaurant)).one().id

    resp = client.get(f"/share/restaurant/{restaurant_id}")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "明婷饭店" in resp.text
    assert 'property="og:title"' in resp.text
    assert f'location.replace("/detail/{restaurant_id}")' in resp.text


def test_share_restaurant_endpoint_missing_returns_404_html(client):
    resp = client.get("/share/restaurant/999")

    assert resp.status_code == 404
    assert 'property="og:title"' in resp.text


def test_share_rank_endpoint_without_snapshot_keeps_link_working(client):
    resp = client.get("/share/rank", params={"city": "成都"})

    assert resp.status_code == 200
    assert "成都苍蝇馆子榜" in resp.text
    assert "/rank?city=%E6%88%90%E9%83%BD" in resp.text