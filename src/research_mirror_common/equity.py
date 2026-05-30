"""Shared Russian-equity ticker resolver.

Maps a publication title to MOEX ticker hashtags. Used by every source bot so
that ticker tagging is consistent and correct.

Two properties matter for correctness:

1. **Span suppression.** A title like "Озон Фармацевтика ..." contains the alias
   "озон" (OZON, the marketplace) *inside* the alias "озон фармацевтика" (OZPH).
   We match longest aliases first and claim their character span, so a shorter
   alias only counts when it occurs OUTSIDE every already-claimed span. That
   yields #OZPH, not #OZON #OZPH.

2. **Manual aliases.** Some MOEX short/sec names don't resemble how a title names
   the company (e.g. VSEH is "ВИ.ру" on MOEX but titles say "ВсеИнструменты.ру").
   MANUAL_ALIASES injects the human-facing names so they resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable


IGNORED_EQUITY_SECIDS = {"GAZA", "GAZAP"}
RUSSIAN_SHARE_BOARDS = {"TQBR", "TQPI", "SMAL", "SPEQ"}

# secid -> extra aliases. For companies whose MOEX name doesn't match how titles
# name them. Keep normalized (lowercase, ё→е, no punctuation, dashes→spaces) — the
# resolver matches these verbatim against the normalized title, it does NOT
# re-normalize them. Extend as needed.
MANUAL_ALIASES: dict[str, tuple[str, ...]] = {
    "VSEH": ("всеинструменты", "всеинструменты ру", "все инструменты"),
    "MOEX": ("мосбиржа", "московская биржа", "moscow exchange"),
    "T": ("тинькофф", "т технологии", "т банк", "тбанк", "t technologies"),
    "SOFL": ("софтлайн",),
    "MBNK": ("мтс банк",),
    # MOEX lists only the preferred Транснефть (TRNFP); titles say "Транснефть".
    "TRNFP": ("транснефть", "transneft"),
    # Short/colloquial names titles use that MOEX short/sec names don't yield.
    "SBER": ("сбер", "sberbank"),
    "BSPB": ("бсп", "bank saint petersburg", "bank st petersburg"),
    "LKOH": ("лукойл", "lukoil"),
    "SIBN": ("гпн", "gazprom neft"),
    "GMKN": ("норникель", "nornickel"),
    "MAGN": ("ммк", "mmk"),
    "FEES": ("фск", "фск россети"),
    "DOMRF": ("дом рф",),
    "BELU": ("новабев",),
    "AQUA": ("инарктика",),
    "HEAD": ("headhunter",),
    # MOEX "Фикс Прайс"; titles also use the Latin "Fix Price".
    "FIXR": ("fix price",),
    # MOEX "Корпоративный центр ИКС 5"; titles say "Икс 5".
    "X5": ("икс 5",),
    "GEMC": ("емц", "юмг", "евромедцентр"),
    "BTBR": ("b2b ртс", "в2в ртс"),
    "RZSB": ("рязаньэнергосбыт", "рязанская энергосбытовая компания"),
    "SNGSP": ("сургут преф", "сургутнефтегаз преф", "сургутнефтегат преф"),
    # Rosseti family — titles drop "Россети" / use regional shorthands.
    "MRKP": ("цип", "россети центр и приволжье", "центр и приволжье"),
    "MRKC": ("россети центр",),
    "MRKU": ("россети урал",),
    "MRKZ": ("россети северо запад",),
    "MSRS": ("моэск", "московский регион"),
    "LSNGP": ("ленэнерго", "россети ленэнерго"),
    # Titles that name the company in English/Latin or a short form the MOEX
    # SHORTNAME/SECNAME doesn't yield (seen in Alfa Research report titles).
    "POSI": ("positive technologies",),  # MOEX: iПозитив / Группа Позитив
    "IVAT": ("iva technologies", "iva"),  # MOEX: iИВА / ПАО ИВА
    "DATA": ("arenadata",),  # MOEX: iАренадата / Группа Аренадата
    "MDMG": ("мд медикал", "md medical"),  # MOEX: MDMG-ао / МКПАО «МД Медикал Груп»
    "ETLN": ("эталон",),  # MOEX: ЭталонГруп / МКПАО Эталон Груп
    "GLRX": ("glorax",),  # MOEX: ГЛОРАКС
    # English company names seen in RenCap report titles (the Russian-only MOEX
    # cache carries no Latin name, so these would otherwise resolve to nothing).
    "AFLT": ("aeroflot",),
    "AKRN": ("acron",),
    "CHMF": ("severstal",),
    "FLOT": ("sovcomflot",),
    "GAZP": ("gazprom",),
    "NVTK": ("novatek",),
    "PHOR": ("phosagro",),
    "ROSN": ("rosneft",),
    "SVCB": ("sovcombank",),
    "TATN": ("tatneft",),
    "VTBR": ("vtb",),
}


@dataclass(frozen=True)
class RussianEquity:
    secid: str
    aliases: tuple[str, ...]
    is_preferred: bool = False


class RussianEquityResolver:
    def __init__(self, equities: Iterable[RussianEquity] = ()) -> None:
        self._equities = tuple(equities)
        self._alias_index: list[tuple[str, RussianEquity]] = []
        for equity in self._equities:
            for alias in equity.aliases:
                self._alias_index.append((alias, equity))
        # Longest aliases first so specific names claim their span before a
        # shorter contained alias gets a chance to match.
        self._alias_index.sort(key=lambda item: len(item[0]), reverse=True)

    @classmethod
    def empty(cls) -> "RussianEquityResolver":
        return cls(())

    @classmethod
    def from_records(cls, records: Iterable[dict]) -> "RussianEquityResolver":
        normalized = [
            {_normalize_key(key): value for key, value in dict(raw).items()} for raw in records
        ]
        valid = [record for record in normalized if _is_russian_share_record(record)]
        # The set of all listed tickers lets us tell a genuine preferred share
        # (secid ends in "P" *and* its ordinary base ticker is also listed) from
        # an ordinary share that merely happens to end in "P" (GAZP, NMTP, NKHP).
        known_secids = {str(record.get("SECID") or "").strip().upper() for record in valid}
        known_secids.discard("")
        by_secid: dict[str, RussianEquity] = {}
        for record in valid:
            secid = str(record.get("SECID") or "").strip().upper()
            if not secid or secid in IGNORED_EQUITY_SECIDS or secid in by_secid:
                continue
            is_preferred = _is_preferred_share(record, known_secids)
            aliases = set(_record_aliases(record, is_preferred))
            aliases.update(MANUAL_ALIASES.get(secid, ()))
            aliases = {alias for alias in aliases if _useful_alias(alias)}
            if aliases:
                by_secid[secid] = RussianEquity(
                    secid=secid,
                    aliases=tuple(sorted(aliases, key=len, reverse=True)),
                    is_preferred=is_preferred,
                )
        return cls(by_secid.values())

    def resolve_title(self, title: str) -> tuple[str, ...]:
        return self._resolve(_normalize_text(_title_lead(title)))

    def resolve_text(self, text: str) -> tuple[str, ...]:
        """Resolve tickers from arbitrary text (e.g. a body), not just a title.

        Same span-suppression as :meth:`resolve_title` but without the
        title-lead heuristic — every company named anywhere in ``text`` is a
        candidate. Use this only for contexts where naming many companies is
        the point (sector/industry overviews), not for arbitrary article bodies
        that mention peers in passing."""
        return self._resolve(_normalize_text(text))

    def _resolve(self, haystack: str) -> tuple[str, ...]:
        if not haystack:
            return ()
        padded = f" {haystack} "
        matches: list[RussianEquity] = []
        positions: dict[str, int] = {}
        claimed: list[tuple[int, int]] = []
        explicit_preferred = _has_preferred_marker(haystack)
        for alias, equity in self._alias_index:
            span = _find_unclaimed(padded, alias, claimed)
            if span is None:
                continue
            claimed.append(span)
            if explicit_preferred and not equity.is_preferred and _base_alias_conflicts(alias, matches):
                continue
            if equity not in matches:
                matches.append(equity)
            positions.setdefault(equity.secid, min(positions.get(equity.secid, span[0]), span[0]))
        if explicit_preferred and any(equity.is_preferred for equity in matches):
            matches = [equity for equity in matches if equity.is_preferred]
        # Order by first occurrence in the text, not by alias length, so multi-
        # ticker results read in the order the companies are named.
        ordered = sorted(matches, key=lambda equity: positions.get(equity.secid, 0))
        return tuple(f"#{equity.secid}" for equity in ordered if equity.secid not in IGNORED_EQUITY_SECIDS)


def load_resolver_from_cache(cache_path: str | Path) -> RussianEquityResolver:
    """Build a resolver from a MOEX cache file ({"records": [...]}). Returns an
    empty resolver if the file is missing or unreadable (callers then emit no
    ticker tags rather than crashing)."""
    try:
        payload = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return RussianEquityResolver.empty()
    records = payload.get("records") if isinstance(payload, dict) else None
    return RussianEquityResolver.from_records(records or [])


def _find_unclaimed(padded: str, alias: str, claimed: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Return the (start, end) char span of the first occurrence of ``alias`` in
    ``padded`` (as a whole space-delimited token) that is not fully inside an
    already-claimed span, or None."""
    needle = f" {alias} "
    search_from = 0
    while True:
        idx = padded.find(needle, search_from)
        if idx == -1:
            return None
        start = idx + 1
        end = start + len(alias)
        if not any(c_start <= start and end <= c_end for c_start, c_end in claimed):
            return (start, end)
        search_from = idx + 1


def _is_russian_share_record(record: dict) -> bool:
    secid = str(record.get("SECID") or "").strip().upper()
    isin = str(record.get("ISIN") or "").strip().upper()
    board = str(record.get("BOARDID") or "").strip().upper()
    if not secid or secid.endswith("-RM"):
        return False
    if isin and not isin.startswith("RU"):
        return False
    return not board or board in RUSSIAN_SHARE_BOARDS


def _record_aliases(record: dict, is_preferred: bool) -> tuple[str, ...]:
    secid = str(record.get("SECID") or "").strip().upper()
    shortname = str(record.get("SHORTNAME") or "")
    secname = str(record.get("SECNAME") or "")
    aliases = {_normalize_text(secid)}
    if is_preferred:
        aliases.update({_normalize_text(shortname), _normalize_text(secname)})
    else:
        aliases.update(
            {
                _clean_company_alias(shortname),
                _clean_company_alias(secname),
                _normalize_text(shortname),
            }
        )
    return tuple(sorted({alias for alias in aliases if _useful_alias(alias)}, key=len, reverse=True))


def _is_preferred_share(record: dict, known_secids: Iterable[str] = ()) -> bool:
    secid = str(record.get("SECID") or "").strip().upper()
    text = _normalize_text(f"{record.get('SHORTNAME') or ''} {record.get('SECNAME') or ''}")
    if re.search(r"\b(ап|прив|п)\b", text):
        return True
    # A trailing "P" only means preferred when an ordinary base ticker is also
    # listed (e.g. SBER/SBERP). Standalone names like GAZP/NMTP/NKHP/RASP end in
    # "P" but are ordinary shares — trusting the suffix alone drops their alias.
    return secid.endswith("P") and secid[:-1] in set(known_secids)


def _title_lead(title: str) -> str:
    if re.search(r"(?i)\bфаворит\s+недели\b", str(title or "")):
        return str(title or "")
    return re.split(r"\s+[–—]\s+|\s+-\s+", str(title or ""), maxsplit=1)[0]


def _base_alias_conflicts(alias: str, matches: list[RussianEquity]) -> bool:
    return any(alias in existing_alias for equity in matches for existing_alias in equity.aliases)


def _has_preferred_marker(normalized_title: str) -> bool:
    return bool(re.search(r"\b(ап|прив|п)\b", normalized_title))


def _clean_company_alias(value: str) -> str:
    text = _normalize_text(value)
    text = re.sub(
        r"\b(публичное|акционерное|общество|пао|ао|мкпао|ооо|орд|ао|ап|обыкновенные|акции?)\b",
        " ",
        text,
    )
    text = re.sub(r"^i(?=[а-яa-z0-9])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_text(value: str) -> str:
    text = str(value or "").casefold().replace("ё", "е").replace("\xa0", " ")
    text = re.sub(r"[\"'«»“”„(){}\[\],.:;!?/\\]+", " ", text)
    text = re.sub(r"[-–—]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_key(value: object) -> str:
    return str(value or "").strip().upper()


def _useful_alias(alias: str) -> bool:
    if not alias:
        return False
    if len(alias) < 3:
        return False
    return alias not in {"пао", "ао", "мкпао", "группа", "компания"}
