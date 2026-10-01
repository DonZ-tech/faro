"""Modelo de campaña: la unidad reutilizable. Una campaña = un tema + léxico + semillas + fechas."""
from __future__ import annotations
from datetime import date
from pathlib import Path
import yaml
from pydantic import BaseModel, Field
from .paths import campaign_dir


class Seeds(BaseModel):
    telegram: list[str] = Field(default_factory=list)   # usernames o t.me/…
    tiktok_hashtags: list[str] = Field(default_factory=list)
    youtube_queries: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    onion_queries: list[str] = Field(default_factory=list)
    onion_engines: list[str] = Field(default_factory=list)   # plantillas de buscador .onion con {q}


class Lexicon(BaseModel):
    terms: dict[str, list[str]] = Field(default_factory=dict)  # {"es": [...], "ar": [...], "arabizi": [...]}
    hashtags: list[str] = Field(default_factory=list)

    def all_terms(self) -> list[str]:
        out: list[str] = []
        for v in self.terms.values():
            out.extend(v)
        out.extend(self.hashtags)
        return sorted(set(t.strip() for t in out if t.strip()))


class KeyDate(BaseModel):
    date: date
    label: str


class Campaign(BaseModel):
    name: str
    title: str
    description: str = ""
    created: date
    lexicon: Lexicon = Field(default_factory=Lexicon)
    seeds: Seeds = Field(default_factory=Seeds)
    key_dates: list[KeyDate] = Field(default_factory=list)
    since: date | None = None   # fecha desde la que captar histórico

    @property
    def path(self) -> Path:
        return campaign_dir(self.name) / "campaign.yml"

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, name: str) -> "Campaign":
        p = campaign_dir(name) / "campaign.yml"
        if not p.exists():
            raise FileNotFoundError(f"No existe la campaña '{name}' ({p}). Usa: faro campaign new {name}")
        return cls.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))

    @classmethod
    def list_names(cls) -> list[str]:
        from .paths import CAMPAIGNS_DIR
        if not CAMPAIGNS_DIR.exists():
            return []
        return sorted(p.parent.name for p in CAMPAIGNS_DIR.glob("*/campaign.yml"))
