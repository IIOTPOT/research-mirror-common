from __future__ import annotations


class HashtagBuilder:
    thematic_order = ("#макро", "#облигации", "#валюта", "#цбрф", "#крипто")

    def __init__(self, *, thematic_order: tuple[str, ...] | None = None) -> None:
        self.thematic_order = thematic_order or self.thematic_order

    def order(self, tags: list[str] | tuple[str, ...]) -> list[str]:
        deduped = _dedupe([str(tag).strip() for tag in tags if str(tag).strip()])
        thematic = [tag for tag in self.thematic_order if tag in deduped]
        remaining = [tag for tag in deduped if tag not in thematic]
        tickers = [tag for tag in remaining if _looks_like_ticker_tag(tag)]
        other = [tag for tag in remaining if tag not in tickers]
        return thematic + tickers + other


def _dedupe(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def _looks_like_ticker_tag(tag: str) -> bool:
    if not tag.startswith("#"):
        return False
    body = tag[1:]
    return bool(body) and body.upper() == body and any(char.isalpha() for char in body)
