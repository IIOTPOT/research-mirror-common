from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re

import httpx


DEFAULT_GROQ_MODELS = (
    "auto,"
    "mistral-small-latest,"
    "llama-3.1-8b-instant"
)
DEFAULT_AUTO_MODEL_ATTEMPTS = 8
SUMMARY_SYSTEM_PROMPT = (
    "Ты редактор инвестиционных Telegram-саммари. Пиши кратко, на русском и строго по источнику."
)
PROMPT_PROFILES: dict[str, dict[str, object]] = {
    "generic": {
        "source_name": "Research",
        "audience": "опытного инвестора",
        "bullet_range": "2-4",
        "focus": [],
        "avoid": [],
    },
    "d8": {
        "source_name": "D8 Research",
        "audience": "опытного инвестора",
        "bullet_range": "2-4",
        "focus": [],
        "avoid": ["меню, навигацию и служебные элементы сайта"],
    },
    "alfa": {
        "source_name": "Alfa Research",
        "audience": "опытного инвестора",
        "bullet_range": "2-5",
        "focus": [],
        "avoid": [],
    },
    "rencap": {
        "source_name": "RenCap Research",
        "audience": "опытного инвестора",
        "bullet_range": "2-3",
        "focus": ["рейтинги", "прогнозные метрики"],
        "avoid": [],
    },
    "alenka": {
        "source_name": "Alenka Capital",
        "audience": "опытного инвестора",
        "bullet_range": "2-5",
        "focus": [],
        "avoid": [],
    },
    "mozgovik": {
        "source_name": "Smart-Lab/Mozgovik",
        "audience": "опытного инвестора",
        "bullet_range": "3-5",
        "focus": ["дивиденды", "оценка", "авторский вывод", "для облигаций: доходность, дюрация, рейтинг, эмитент"],
        "avoid": ["списки ISIN/RU-кодов", "строки «ISIN для удобства копирования»", "paywall-текст"],
    },
    "euler": {
        "source_name": "Euler",
        "audience": "опытного инвестора",
        "bullet_range": "2-5",
        "focus": [],
        "avoid": ["risk factors, причинно-следственные связи и релевантность для акций, если этого нет в тексте"],
    },
}


@dataclass(frozen=True)
class SummaryResult:
    text: str
    provider: str
    model: str | None
    prompt_version: str


class SummaryClient:
    def __init__(
        self,
        *,
        provider: str = "auto",
        api_key: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        models: str | list[str] | tuple[str, ...] = DEFAULT_GROQ_MODELS,
        prompt_profile: str = "generic",
        prompt_version: str = "v2",
        max_tokens: int = 700,
    ) -> None:
        self.provider = (provider or "auto").strip().lower()
        self.api_key = (api_key or "").strip() or None
        self.base_url = (base_url or "https://api.groq.com/openai/v1").rstrip("/")
        self.models = split_models(models)
        self.prompt_profile = (prompt_profile or "generic").strip() or "generic"
        self.prompt_version = (prompt_version or "v1").strip() or "v1"
        self.max_tokens = max(1, int(max_tokens))

    def build_summary(self, *, title: str, text: str, max_bullets: int = 4) -> SummaryResult:
        clean_text = clean_summary_source_text(text)
        if self.provider != "extractive" and self.api_key and clean_text:
            prompt = self._build_prompt(title, clean_text[:14000])
            for model in self.models:
                data = self._json_response(model=model, prompt=prompt)
                if not data:
                    continue
                headline = _one_line(str(data.get("headline") or title)).strip()
                bullets = [_one_line(str(item)) for item in data.get("bullets") or [] if str(item).strip()]
                bullets = remove_title_echo_bullets(title, bullets)[:max_bullets]
                if headline and bullets:
                    return SummaryResult(
                        text="\n".join([headline, *[f"• {bullet}" for bullet in bullets]]),
                        provider="llm",
                        model=model,
                        prompt_version=self.prompt_version,
                    )
        return SummaryResult(
            text=extractive_summary(title=title, text=clean_text or text, max_bullets=max_bullets),
            provider="extractive",
            model=None,
            prompt_version=self.prompt_version,
        )

    def _json_response(self, *, model: str, prompt: str) -> dict | None:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": summary_system_prompt(),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        return self._post_json_response(payload | {"response_format": {"type": "json_object"}}) or self._post_json_response(payload)

    def _post_json_response(self, payload: dict) -> dict | None:
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=httpx.Timeout(45.0, connect=15.0),
            )
            if response.status_code in {401, 403, 429} or response.status_code >= 500:
                return None
            if response.status_code == 400:
                return None
            response.raise_for_status()
            return parse_json_object(response.json()["choices"][0]["message"]["content"])
        except Exception:
            return None

    def _build_prompt(self, title: str, text: str) -> str:
        return build_summary_prompt(title=title, text=text, prompt_profile=self.prompt_profile)


def split_models(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if isinstance(value, (list, tuple)):
        models = [str(item).strip() for item in value if str(item).strip()]
    else:
        models = [item.strip() for item in str(value or DEFAULT_GROQ_MODELS).split(",") if item.strip()]
    return expand_auto_models(models or [DEFAULT_GROQ_MODELS.split(",")[0]])


def expand_auto_models(models: list[str], *, attempts: int | None = None) -> list[str]:
    auto_attempts = attempts if attempts is not None else _auto_model_attempts()
    expanded: list[str] = []
    for model in models:
        if model.strip().casefold() == "auto":
            expanded.extend(["auto"] * auto_attempts)
        else:
            expanded.append(model)
    return expanded or ["auto"]


def _auto_model_attempts() -> int:
    raw = os.getenv("RESEARCH_MIRROR_GROQ_AUTO_ATTEMPTS")
    try:
        return max(1, min(50, int(raw))) if raw else DEFAULT_AUTO_MODEL_ATTEMPTS
    except ValueError:
        return DEFAULT_AUTO_MODEL_ATTEMPTS


def summary_system_prompt() -> str:
    return SUMMARY_SYSTEM_PROMPT


def build_summary_prompt(*, title: str, text: str, prompt_profile: str = "generic") -> str:
    profile = _prompt_profile(prompt_profile)
    focus = [
        "тикеры/компании",
        "цифры, даты, проценты, цены и финансовые метрики",
        "драйверы, риски, прогнозы, рекомендации и таргеты, если они есть в тексте",
        "вывод автора, если он прямо сформулирован",
        "целевые цены и дивдоходности, если они указаны",
        "что изменилось для компании, сектора или рынка",
        *[str(item) for item in profile["focus"]],
    ]
    avoid = [
        "выводы, причины и рекомендации, которых нет в источнике",
        "дисклеймеры, ссылки, контакты, подписи авторов, повторы заголовка и служебный шум",
        "сложные формулировки",
        *[str(item) for item in profile["avoid"]],
    ]
    focus_text = "; ".join(focus)
    avoid_text = "; ".join(avoid)
    return (
        f'Сделай fact-only саммари материала {profile["source_name"]} для {profile["audience"]}.\n'
        f"Выбери только инвестиционно значимые факты: {focus_text}.\n"
        f"Не добавляй и не пересказывай: {avoid_text}.\n"
        "Если исходник на английском, переведи факты на русский.\n"
        f'Формат: headline до 140 символов и {profile["bullet_range"]} bullets до 220 символов каждый; '
        "если фактов мало, верни меньше bullets.\n"
        "Целевые цены и дивдоходности, если они указаны, объединяй в один bullet.\n"
        "Каждый bullet должен содержать один факт или связку факт -> эффект, только если эффект прямо указан.\n\n"
        "Ответ строго JSON:\n"
        '{"headline":"короткий заголовок","bullets":["факт 1","факт 2"]}\n\n'
        f"Заголовок: {title}\n\n"
        f"Текст материала:\n{text}"
    )


def parse_json_object(content: str) -> dict | None:
    text = str(content or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def clean_summary_source_text(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        normalized = line.strip()
        lowered = normalized.casefold()
        if not normalized:
            continue
        if "дисклеймер" in lowered or "не является индивидуальной инвестиционной рекомендацией" in lowered:
            continue
        if "important disclosures" in lowered or "disclaimer" in lowered:
            continue
        lines.append(normalized)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def extractive_summary(*, title: str, text: str, max_bullets: int = 4) -> str:
    clean_text = clean_summary_source_text(text)
    sentences = [
        _one_line(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+", clean_text)
        if len(_one_line(sentence)) >= 20
    ]
    if not sentences:
        return str(title or "").strip() or "Саммари недоступно."
    headline = sentences[0][:180]
    bullets = [sentence for sentence in sentences[1:] if sentence != headline][:max_bullets]
    if not bullets and len(sentences) > 1:
        bullets = sentences[:max_bullets]
    if not bullets:
        return headline
    return "\n".join([headline, *[f"• {bullet[:280]}" for bullet in bullets]])


def remove_title_echo_bullets(title: str, bullets: list[str]) -> list[str]:
    normalized_title = _normalized(title)
    title_tokens = {token for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", normalized_title) if len(token) >= 4}
    result: list[str] = []
    for bullet in bullets:
        normalized = _normalized(bullet)
        if normalized_title and normalized.strip(" .:-–—") == normalized_title.strip(" .:-–—"):
            continue
        bullet_tokens = {token for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", normalized) if len(token) >= 4}
        if bullet_tokens and title_tokens and bullet_tokens <= title_tokens:
            continue
        result.append(bullet)
    return result


def _one_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized(value: str) -> str:
    return _one_line(value).casefold()


def _prompt_profile(prompt_profile: str) -> dict[str, object]:
    key = _normalized(prompt_profile).replace(" ", "_")
    return PROMPT_PROFILES.get(key) or PROMPT_PROFILES["generic"]
