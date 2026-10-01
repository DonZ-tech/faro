"""Normalización de texto multilingüe (es/ar/arabizi) y extracción de URLs y menciones."""
from __future__ import annotations
import re
import unicodedata
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
MENTION_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_]{4,32})")
HASHTAG_RE = re.compile(r"#([\w؀-ۿ]+)")
ARABIC_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
ARABIC_RE = re.compile(r"[؀-ۿ]")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = URL_RE.sub(" ", t)
    t = ARABIC_DIACRITICS.sub("", t)
    t = t.replace("\u0640", "")            # tatweel
    t = re.sub(r"(?<=[\u0600-\u06FF])[.\u2026_\-]+(?=[\u0600-\u06FF])", "", t)  # fragmentación الهجـ..ـمة
    t = t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").replace("ة", "ه")
    t = t.lower()
    t = re.sub(r"[^\w\s؀-ۿ#]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def guess_lang(text: str) -> str:
    if not text:
        return "und"
    ar = len(ARABIC_RE.findall(text))
    latin = len(re.findall(r"[a-zA-Z]", text))
    if ar > latin:
        return "ar"
    if re.search(r"\b(l7rig|lhrig|sebta|fnideq|sba7|9sar|wach|bzaf)\b", text, re.I):
        return "arabizi"
    if re.search(r"\b(el|la|los|las|de|que|y|en|para)\b", text, re.I):
        return "es"
    return "und" if not latin else "lat"


def urls(text: str | None) -> list[str]:
    return URL_RE.findall(text or "")


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def mentions(text: str | None) -> list[str]:
    return MENTION_RE.findall(text or "")


def hashtags(text: str | None) -> list[str]:
    return [h.lower() for h in HASHTAG_RE.findall(text or "")]


def matches_lexicon(text: str | None, terms: list[str]) -> list[str]:
    n = normalize(text)
    return [t for t in terms if normalize(t) and normalize(t) in n]
