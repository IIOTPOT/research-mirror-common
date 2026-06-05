from research_mirror_common.tg_format import to_telegram_html, escape_with_md


def test_bold_double_star_and_underscore():
    assert to_telegram_html("**SFI** и __тоже__") == "<b>SFI</b> и <b>тоже</b>"


def test_italic_single_star():
    assert to_telegram_html("рек. *Держать*)") == "рек. <i>Держать</i>)"


def test_code_backticks():
    assert to_telegram_html("тикер `RENG`") == "тикер <code>RENG</code>"


def test_html_is_escaped():
    assert to_telegram_html("<тег> & 5>3") == "&lt;тег&gt; &amp; 5&gt;3"


def test_no_false_positive_italic():
    # буллеты, умножение и snake_case не должны становиться курсивом
    src = "* пункт; 5 * 3 = 15; last_successful_scan"
    assert "<i>" not in to_telegram_html(src)
    assert to_telegram_html(src) == src


def test_bold_and_italic_together():
    assert to_telegram_html("**жирный** и *курсив*") == "<b>жирный</b> и <i>курсив</i>"


def test_plain_text_equals_html_escape():
    assert to_telegram_html("обычный текст без разметки") == "обычный текст без разметки"


def test_alias_is_same():
    assert escape_with_md is to_telegram_html
