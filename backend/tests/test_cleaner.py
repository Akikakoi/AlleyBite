from app.services.cleaner import DEFAULT_AD_WORDS, clean_text


def test_strips_html_script_style():
    raw = "<div>明婷饭店</div><script>alert(1)</script><style>.a{}</style>"
    report = clean_text(raw)
    assert "明婷饭店" in report.text
    assert "<" not in report.text
    assert "alert" not in report.text and ".a{}" not in report.text


def test_removes_urls():
    report = clean_text("这家好吃，详见 https://example.com/x 快去")
    assert "http" not in report.text
    assert "这家好吃" in report.text


def test_traditional_to_simplified():
    report = clean_text("這家店很好吃，價錢也便宜")
    assert "这" in report.text and "价" in report.text
    assert "這" not in report.text and "價" not in report.text


def test_fullwidth_alnum_to_halfwidth():
    report = clean_text("人均８０元，评分４．８")
    assert "80" in report.text
    assert "４" not in report.text


def test_unit_normalization():
    report = clean_text("人均 80 块钱，隔壁人均100块，这家￥120")
    assert "80 元" in report.text
    assert "100 元" in report.text
    assert "120 元" in report.text


def test_emoji_removed():
    report = clean_text("太好吃了😋👍！！")
    assert "😋" not in report.text and "👍" not in report.text
    assert "好吃" in report.text


def test_removes_template_noise():
    report = clean_text("明婷饭店不错。点击查看全文")
    assert "查看全文" not in report.text
    assert "明婷饭店不错" in report.text


def test_chinese_language_filter():
    assert clean_text("明婷饭店很好吃，锅气足").is_chinese is True
    assert clean_text("this is a plain english review of a restaurant").is_chinese is False


def test_ad_detection_marks_low_trust():
    report = clean_text("【探店合作】成都新开的火锅店，团购链接见评论区")
    assert report.low_trust is True
    assert "探店" in report.ad_hits
    assert "团购链接" in report.ad_hits


def test_custom_ad_words():
    report = clean_text("本店新开业", ad_words=["新开业"])
    assert report.ad_hits == ["新开业"]


def test_empty_input():
    report = clean_text("   ")
    assert report.text == ""
    assert report.is_chinese is False
    assert report.low_trust is False


def test_default_ad_words_non_empty():
    assert "探店" in DEFAULT_AD_WORDS