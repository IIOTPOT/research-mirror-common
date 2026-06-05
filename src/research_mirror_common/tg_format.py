"""Единый конвертер разметки сводок в Telegram-HTML для ВСЕХ ботов-зеркал.

LLM отдаёт сводки в CommonMark (`**жирный**`, `*курсив*`, `` `код` ``), а боты
шлют сообщения с ``parse_mode="HTML"``. Раньше каждый бот делал свой
``html.escape`` (или копию хелпера) — звёздочки оставались текстом и формат
«плодился». Это — ЕДИНСТВЕННАЯ точка правды: экранирует HTML и превращает
разметку в теги Telegram (``<b>``/``<i>``/``<code>``).

Поддерживаемые теги Telegram HTML: b, i, u, s, a, code, pre, blockquote.
Здесь покрыто то, что реально встречается в сводках; одиночные ``*``/``_`` без
пары, буллеты ``* ``, ``5 * 3`` и snake_case НЕ трогаются (защита от ложных
срабатываний).
"""

from __future__ import annotations

import html
import re

# **жирный** / __жирный__  (не-жадно, границы — не пробел)
_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
# `код`
_CODE_RE = re.compile(r"`([^`\n]+?)`")
# *курсив* — одиночная звёздочка: не часть слова/`**`/кода, границы — не пробел.
_ITALIC_RE = re.compile(r"(?<![\w*`])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")


def to_telegram_html(text: str) -> str:
    """HTML-escape + конвертация markdown сводки в Telegram-HTML.

    Идемпотентна по смыслу: без markdown эквивалентна ``html.escape``.
    Порядок важен: сначала жирный (съедает ``**``), затем код, затем курсив
    по оставшимся одиночным ``*``.
    """
    escaped = html.escape(str(text or ""))
    escaped = _BOLD_RE.sub(r"<b>\2</b>", escaped)
    escaped = _CODE_RE.sub(r"<code>\1</code>", escaped)
    escaped = _ITALIC_RE.sub(r"<i>\1</i>", escaped)
    return escaped


# Обратносовместимый алиас — боты исторически звали хелпер так.
escape_with_md = to_telegram_html
