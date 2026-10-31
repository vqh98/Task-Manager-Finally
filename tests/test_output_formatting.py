from app.output_formatting import html_to_plain_text, localize_visible_digits


def test_bale_plain_text_removes_telegram_html_without_losing_content():
    source = (
        "<b>📅 برنامه جلسات امروز (30 مهر 1405)</b>\n\n"
        "۱. <b>DBA</b>\n   🕐 12:00 تا 13:00\n\n"
        "مجموع زمان جلسات: <b>1 ساعت و 0 دقیقه</b>"
    )
    rendered = localize_visible_digits(html_to_plain_text(source))
    assert "<b>" not in rendered
    assert "</b>" not in rendered
    assert "DBA" in rendered
    assert "۱۲:۰۰ تا ۱۳:۰۰" in rendered
    assert "۱ ساعت و ۰ دقیقه" in rendered


def test_bale_plain_text_decodes_html_entities():
    assert html_to_plain_text("<b>A &amp; B</b><br>C") == "A & B\nC"
