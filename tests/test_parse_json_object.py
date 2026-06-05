from research_mirror_common.summary import parse_json_object


def test_clean_json():
    assert parse_json_object('{"headline":"H","bullets":["a","b"]}') == {
        "headline": "H",
        "bullets": ["a", "b"],
    }


def test_markdown_fenced_json():
    raw = '```json\n{"headline":"H","bullets":["a"]}\n```'
    assert parse_json_object(raw) == {"headline": "H", "bullets": ["a"]}


def test_truncated_midstring_is_repaired():
    # ответ обрезан по max_tokens посреди последнего булета
    raw = '```json\n{\n "headline":"Роснефть",\n "bullets":[\n  "Прибыль +5%",\n  "Дивиденд ~3'
    data = parse_json_object(raw)
    assert data is not None
    assert data["headline"] == "Роснефть"
    assert data["bullets"][0] == "Прибыль +5%"  # полные булеты сохранены


def test_truncated_after_comma():
    assert parse_json_object('{"headline":"H","bullets":["a","b",') == {
        "headline": "H",
        "bullets": ["a", "b"],
    }


def test_prose_without_json_returns_none():
    assert parse_json_object("Просто текст без JSON.") is None


def test_empty_returns_none():
    assert parse_json_object("") is None
    assert parse_json_object(None) is None
