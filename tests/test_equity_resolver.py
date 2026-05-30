from __future__ import annotations

import json

from research_mirror_common.equity import RussianEquityResolver, load_resolver_from_cache


def _rec(secid: str, shortname: str, secname: str) -> dict:
    return {
        "SECID": secid,
        "SHORTNAME": shortname,
        "SECNAME": secname,
        "BOARDID": "TQBR",
        "ISIN": f"RU000{secid}0000"[:12],
    }


# Mirrors the live MOEX records for these tickers.
RECORDS = [
    _rec("OZON", "Озон", "МКПАО Озон"),
    _rec("OZPH", "iОзонФарм", "Озон Фармацевтика"),
    _rec("VSEH", "ВИ.ру", "ВИ.ру"),
    _rec("CNRU", "iЦиан", 'МКПАО "Циан"'),
    _rec("SBER", "Сбербанк", "Сбербанк России ПАО ао"),
    _rec("SBERP", "Сбербанк-п", "Сбербанк России ПАО ап"),
    # Ordinary shares whose secid happens to end in "P" — must NOT be treated as
    # preferred (no ordinary base ticker is listed for them).
    _rec("GAZP", "ГАЗПРОМ ао", '"Газпром" (ПАО) ао'),
    _rec("NMTP", "НМТП ао", "НМТП (ПАО) ао"),
    # Транснефть lists only the preferred share; titles say "Транснефть".
    _rec("TRNFP", "Транснф ап", "Транснефть ПАО акц.пр."),
]


def _resolver() -> RussianEquityResolver:
    return RussianEquityResolver.from_records(RECORDS)


def test_ozon_pharma_suppresses_marketplace():
    # "Озон Фармацевтика" contains "озон" (OZON marketplace) but the subject is
    # OZPH — the nested OZON match must be suppressed.
    assert _resolver().resolve_title("Озон Фармацевтика — отчёт за 1-й квартал 2026") == ("#OZPH",)


def test_plain_ozon_resolves_marketplace():
    assert _resolver().resolve_title("Озон — сильный квартал") == ("#OZON",)


def test_vseinstrumenty_resolves_via_manual_alias():
    # MOEX names it "ВИ.ру"; titles say "ВсеИнструменты.ру". Manual alias bridges.
    tags = _resolver().resolve_title("ВсеИнструменты.ру отчёт за 1-й квартал 2026 по МСФО")
    assert tags == ("#VSEH",)


def test_vseinstrumenty_does_not_get_cian():
    assert "#CNRU" not in _resolver().resolve_title("ВсеИнструменты.ру отчёт за 1-й квартал 2026 по МСФО")


def test_unrelated_title_yields_no_ticker():
    assert _resolver().resolve_title("Результаты банков в апреле 2026 по РСБУ") == ()


def test_cian_still_resolves_when_it_is_the_subject():
    assert _resolver().resolve_title("Циан — отчёт за 1-й квартал") == ("#CNRU",)


def test_manual_alias_resolves_title_form():
    # MOEX names "Московская Биржа ..."; titles say "Мосбиржа". Manual alias.
    resolver = RussianEquityResolver.from_records(
        [_rec("MOEX", "МосБиржа", "Московская Биржа ММВБ-РТС")]
    )
    assert resolver.resolve_title("Мосбиржа — отчёт за апрель") == ("#MOEX",)


def test_load_resolver_from_cache_roundtrip(tmp_path):
    path = tmp_path / "moex.json"
    path.write_text(json.dumps({"records": RECORDS}, ensure_ascii=False), encoding="utf-8")
    resolver = load_resolver_from_cache(path)
    assert resolver.resolve_title("Озон Фармацевтика — отчёт") == ("#OZPH",)


def test_load_resolver_from_missing_cache_is_empty():
    assert load_resolver_from_cache("/no/such/file.json").resolve_title("Озон") == ()


def test_preferred_share_suppresses_ordinary():
    # "Сбербанк-п" → preferred SBERP; the contained ordinary "сбербанк" (SBER)
    # is suppressed as a nested span.
    assert _resolver().resolve_title("Сбербанк-п — дивиденды за 2025") == ("#SBERP",)


def test_ordinary_ticker_ending_in_p_resolves():
    # GAZP/NMTP end in "P" but are ordinary shares; the trailing-"P" heuristic
    # must not misclassify them as preferred (which dropped their clean alias).
    assert _resolver().resolve_title("Газпром — операционные результаты") == ("#GAZP",)
    assert _resolver().resolve_title("НМТП — отчёт за 1-й квартал 2026") == ("#NMTP",)


def test_preferred_only_company_resolves_via_manual_alias():
    # MOEX lists only the preferred Транснефть (TRNFP); a manual alias bridges
    # the bare "Транснефть" title form.
    assert _resolver().resolve_title("Транснефть — дивиденды за 2025") == ("#TRNFP",)


# --- Colloquial / shorthand title forms that MOEX short/sec names don't yield ---
# Each record uses the real MOEX SHORTNAME/SECNAME (which do NOT contain the
# colloquial title form), so resolution proves the manual alias bridges.
_COLLOQUIAL = RussianEquityResolver.from_records(
    [
        _rec("SIBN", "Газпрнефть", "Газпром нефть ПАО ао"),
        _rec("GMKN", "ГМКНорНик", "ГМК Норильский Никель ПАО ао"),
        _rec("MSRS", "Россети МР", "Россети Московский регион ПАО ао"),
        _rec("SNGSP", "Сургнфгз-п", "Сургутнефтегаз ПАО ап"),
        _rec("BTBR", "ВУШ", "B2B-РТС ПАО ао"),
        _rec("MRKC", "РСетиЦентр", "Россети Центр ПАО ао"),
        _rec("MRKP", "РСетиЦП ао", "Россети Центр и Приволжье ПАО ао"),
    ]
)


def test_colloquial_aliases_resolve():
    assert _COLLOQUIAL.resolve_title("ГПН — операционные результаты") == ("#SIBN",)
    assert _COLLOQUIAL.resolve_title("Норникель отчитался за полугодие") == ("#GMKN",)
    assert _COLLOQUIAL.resolve_title("МОЭСК — дивиденды") == ("#MSRS",)
    assert _COLLOQUIAL.resolve_title("Сургут преф — дивидендная история") == ("#SNGSP",)


def test_rosseti_longest_alias_wins_over_contained_region():
    # "Россети Центр и Приволжье" (MRKP) contains "Россети Центр" (MRKC); the
    # longer alias claims the span and suppresses the nested MRKC match.
    assert _COLLOQUIAL.resolve_title("Россети Центр и Приволжье — отчёт") == ("#MRKP",)
    assert _COLLOQUIAL.resolve_title("ЦиП — отчёт за 1-й квартал") == ("#MRKP",)
    assert _COLLOQUIAL.resolve_title("Россети Центр — отчёт за 1-й квартал") == ("#MRKC",)


def test_manual_alias_sber_short_form():
    # MOEX short name is "Сбербанк"; titles abbreviate to "Сбер".
    assert _resolver().resolve_title("Сбер — итоги месяца") == ("#SBER",)


def test_manual_alias_headhunter_latin_form():
    # MOEX names HEAD "ХэдХантер" (Cyrillic); titles often write "Headhunter".
    resolver = RussianEquityResolver.from_records([_rec("HEAD", "ХэдХантер", "МКПАО ХэдХантер")])
    assert resolver.resolve_title("Headhunter: дивиденды и обратный выкуп") == ("#HEAD",)


def test_manual_alias_fix_price_latin_form():
    resolver = RussianEquityResolver.from_records([_rec("FIXR", "Фикс Прайс", "ПАО Фикс Прайс")])
    assert resolver.resolve_title("Fix Price — слабый квартал") == ("#FIXR",)


def test_resolve_text_finds_all_named_companies():
    # resolve_text resolves every company named anywhere (sector overviews),
    # unlike resolve_title which only looks at the title lead.
    resolver = RussianEquityResolver.from_records(
        [
            _rec("ASTR", "Астра", 'ПАО "Группа Астра"'),
            _rec("POSI", "Позитив", 'ПАО "Группа Позитив"'),
            _rec("DATA", "Аренадата", 'ПАО "Группа Аренадата"'),
        ]
    )
    body = "Астра показала низкий рост. Позитив выделяется. Аренадата превысила ожидания."
    assert resolver.resolve_text(body) == ("#ASTR", "#POSI", "#DATA")


def test_english_title_forms_resolve_via_manual_alias():
    # RenCap posts some titles in English; the Russian-only MOEX cache has no
    # Latin name, so these resolve only through MANUAL_ALIASES.
    resolver = RussianEquityResolver.from_records(
        [
            _rec("FLOT", "Совкомфлот", "Совкомфлот ПАО ао"),
            _rec("GAZP", "ГАЗПРОМ ао", '"Газпром" (ПАО) ао'),
            _rec("ROSN", "Роснефть", "Роснефть ПАО ао"),
            _rec("VTBR", "ВТБ ао", "Банк ВТБ ПАО ао"),
        ]
    )
    assert resolver.resolve_title("Sovcomflot – Comeback is real!") == ("#FLOT",)
    assert resolver.resolve_title("Gazprom – 1Q26 preview") == ("#GAZP",)
    assert resolver.resolve_title("Rosneft – results") == ("#ROSN",)
    assert resolver.resolve_title("VTB – results") == ("#VTBR",)


def test_manual_aliases_bridge_alfa_company_names():
    # Alfa Research titles name these companies in English/Latin or a short form
    # the MOEX SHORTNAME/SECNAME does not carry; manual aliases must bridge them.
    cases = [
        (_rec("POSI", "iПозитив", "Группа Позитив ао"), "Positive Technologies: Результаты за 2025", "#POSI"),
        (_rec("IVAT", "iИВА", "ПАО ИВА ао"), "IVA Technologies: Ставим бумагу на Пересмотр", "#IVAT"),
        (_rec("DATA", "iАренадата", "Группа Аренадата"), "Arenadata: Результаты за 2025 г.", "#DATA"),
        (_rec("MDMG", "MDMG-ао", "МКПАО «МД Медикал Груп»"), "ГК МД Медикал: Рост выручки", "#MDMG"),
        (_rec("ETLN", "ЭталонГруп", "МКПАО Эталон Груп"), "Эталон: Операционные результаты за 2025", "#ETLN"),
        (_rec("GLRX", "ГЛОРАКС", "ПАО ГЛОРАКС"), "GloraX (ВЫШЕ РЫНКА): результаты", "#GLRX"),
        (_rec("MAGN", "ММК", '"Магнитогорск.мет.комб" ПАО ао'), "MMK: Итоги 1К26", "#MAGN"),
    ]
    for record, title, expected in cases:
        resolver = RussianEquityResolver.from_records([record])
        assert resolver.resolve_title(title) == (expected,), title
