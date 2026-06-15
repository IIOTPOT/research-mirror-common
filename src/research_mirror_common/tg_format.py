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


# Теги Telegram-HTML, которые порождают сводки/заголовки (b/i/u/s/a/code/pre/
# blockquote/tg-spoiler). Все они парные — простого стека open/close достаточно.
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)[^>]*?(/?)>")


def truncate_html(value: str, limit: int, *, suffix: str = "…") -> str:
    """Обрезать ГОТОВУЮ Telegram-HTML строку до ``limit`` символов без поломки разметки.

    ВАЖНО: применять только к строкам, которые УЖЕ являются Telegram-HTML (после
    ``escape_with_md`` / ``html.escape``). Сырой текст до экранирования обрезать
    этим нельзя — проверка ``&entity;`` откусит на ``&`` в «S&P»/«M&A». Для сырого
    текста используйте обычную обрезку.

    Наивный срез ``text[:limit]`` режет посреди тега (``<b``) или сущности
    (``&am`` от ``&amp;``) → Telegram отвечает ``Bad Request: ... Unclosed start
    tag``. Здесь срез откатывается из частичного тега/сущности и закрывает теги,
    оставшиеся открытыми на месте обрезки. Результат всегда ≤ ``limit`` символов и
    является валидным Telegram-HTML.

    Единая точка правды для всех ботов-зеркал — заменяет дублированные локальные
    ``truncate``/``trim_text``/``truncate_text``.
    """
    text = str(value or "").strip()
    if len(text) <= limit:
        return text

    end = max(0, limit - len(suffix))
    cut = ""
    for _ in range(8):  # сходится за пару итераций; ограничено от патологий
        cut = text[:end]
        # Не заканчиваемся внутри тега: если последний '<' без пары '>', срезаем хвост.
        if cut.rfind("<") > cut.rfind(">"):
            cut = cut[: cut.rfind("<")]
        # Не заканчиваемся внутри ссылки на сущность &...;
        amp = cut.rfind("&")
        if amp > cut.rfind(";") and len(cut) - amp <= 10:
            cut = cut[:amp]
        cut = cut.rstrip()
        # Закрываем теги, оставшиеся открытыми на месте обрезки (внутренний — первым).
        open_stack: list[str] = []
        for match in _TAG_RE.finditer(cut):
            closing, name, self_closing = match.group(1), match.group(2).lower(), match.group(3)
            if self_closing:
                continue
            if closing:
                for i in range(len(open_stack) - 1, -1, -1):
                    if open_stack[i] == name:
                        del open_stack[i]
                        break
            else:
                open_stack.append(name)
        closers = "".join(f"</{name}>" for name in reversed(open_stack))
        if len(cut) + len(suffix) + len(closers) <= limit or end <= 0:
            return cut + suffix + closers
        # Закрывающие теги не влезли в лимит — резервируем под них место и повторяем.
        end = limit - len(suffix) - len(closers)
    return cut + suffix
