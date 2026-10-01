import os, tempfile, json
from datetime import date
os.environ.setdefault("FARO_CONFIG_DIR", tempfile.mkdtemp())
os.environ.setdefault("FARO_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("FARO_CAMPAIGNS_DIR", tempfile.mkdtemp())

import httpx
import pytest

from faro import leaks, evidence
from faro.campaign import Campaign
from faro.db import connect
from faro.paths import campaign_data
from faro.importers import spiderfoot


def _camp(name):
    c = Campaign(name=name, title=name, created=date(2026, 10, 1))
    c.save()
    return c


def _mock(monkeypatch, handler):
    monkeypatch.setattr(leaks, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(leaks.time, "sleep", lambda s: None)


def test_hibp_accounts_pseudonymizes_and_keeps_evidence(monkeypatch):
    seen = {}

    def handler(req):
        seen["key"] = req.headers.get("hibp-api-key")
        if "nadie%40ejemplo.es" in str(req.url) or "nadie@ejemplo.es" in str(req.url):
            return httpx.Response(404)
        return httpx.Response(200, json=[{"Name": "Adobe", "Title": "Adobe", "Domain": "adobe.com",
                                          "BreachDate": "2013-10-04", "DataClasses": ["Email addresses", "Passwords"]}])

    _mock(monkeypatch, handler)
    c = _camp("leaks-hibp")
    s = leaks.hibp_accounts(c, ["Ana@Ejemplo.es", "nadie@ejemplo.es"], api_key="k")
    assert seen["key"] == "k"
    assert s == {"emails": 2, "found": 1, "new": 1, "errors": 0}
    rows = leaks.listing(c)
    assert len(rows) == 1 and "@" not in rows[0]["target"] and rows[0]["sensitive"] == 1
    assert leaks.listing(c, reveal=True)[0]["target"] == "ana@ejemplo.es"
    assert all(ok for _, ok in evidence.verify_raw(campaign_data(c.name)))
    assert leaks.hibp_accounts(c, ["ana@ejemplo.es"], api_key="k")["new"] == 0


def test_hibp_without_key_fails_clearly(monkeypatch):
    monkeypatch.delenv("FARO_HIBP_API_KEY", raising=False)
    with pytest.raises(leaks.LeakError):
        leaks.hibp_accounts(_camp("leaks-nokey"), ["a@b.es"])


def test_domain_breaches(monkeypatch):
    _mock(monkeypatch, lambda req: httpx.Response(200, json=[{"Name": "LinkedIn", "BreachDate": "2012-05-05",
                                                             "DataClasses": ["Email addresses"]}]
                                                  if req.url.params.get("domain") == "linkedin.com" else []))
    c = _camp("leaks-domain")
    s = leaks.hibp_domain_breaches(c, ["linkedin.com", "ejemplo.es"])
    assert s["found"] == 1 and s["new"] == 1
    assert leaks.listing(c)[0]["target"] == "linkedin.com"


def test_github_simple_queries_and_secret_flag(monkeypatch):
    queries = []

    def handler(req):
        q = req.url.params["q"]
        queries.append(q)
        if "extension:sql" in q:
            return httpx.Response(422, json={"message": "Validation Failed"})
        frag = 'SMTP_PASSWORD = "Mx82!kq0z"' if "smtp" in q else "Forgot your password?"
        return httpx.Response(200, json={"items": [{"path": f"{q[-4:]}.cfg", "html_url": "https://github.com/x",
                                                    "repository": {"full_name": "o/r"},
                                                    "text_matches": [{"fragment": frag}]}]})

    _mock(monkeypatch, handler)
    c = _camp("leaks-gh")
    s = leaks.github_code(c, ["ejemplo.es"], token="t")
    assert all(" OR " not in q and "(" not in q for q in queries)
    assert len(queries) == len(leaks.GITHUB_HINTS)
    assert s["errors"] == 1 and s["queries"] == 3 and s["sensitive"] == 1


@pytest.mark.parametrize("text,expected", [
    ('password = "Xk82!pq0z"', True), ("SMTP_PASSWORD: mailpass991", True), ("contraseña: Cadiz2026!", True),
    ('api_key="${API_KEY}"', False), ("password: changeme123", False),
    ("Forgot your password? Reset it", False), ("token = <your-token>", False),
])
def test_looks_secret(text, expected):
    assert leaks.looks_secret(text) is expected


def test_dorks_are_generated_not_fetched():
    ds = leaks.dorks("Ejemplo.es")
    assert ds and all("ejemplo.es" in q for _, q in ds)
    assert leaks.dork_url(ds[0][1], "bing").startswith("https://www.bing.com/search?q=site%3Aejemplo.es")


def test_spiderfoot_formats(tmp_path):
    cli = [{"generated": 1790860673, "type": "IP Address", "data": "1.2.3.4", "module": "sfp_dnsresolve", "source": "ejemplo.es"}]
    web = [{"data": "1.2.3.4", "event_type": "IP Address", "module": "sfp_dnsresolve", "source_data": "ejemplo.es",
            "false_positive": 0, "last_seen": "2026-10-01 12:00:00", "scan_name": "s", "scan_target": "ejemplo.es"}]
    web_csv = "Updated,Type,Module,Source,F/P,Data\n2026-10-01 12:00:00,IP Address,sfp_dnsresolve,ejemplo.es,0,1.2.3.4\n"
    cli_csv = "Source,Type,Data\nsfp_dnsresolve,IP Address,ejemplo.es,1.2.3.4\n"
    for raw in (json.dumps(cli), json.dumps(web), web_csv, cli_csv):
        rows = spiderfoot.parse(raw.encode())
        assert [(r["kind"], r["module"], r["data"], r["source_data"]) for r in rows] == \
               [("IP Address", "sfp_dnsresolve", "1.2.3.4", "ejemplo.es")]
    with pytest.raises(ValueError):
        spiderfoot.parse(b'{"x": 1}')

    c = _camp("sf")
    p = tmp_path / "scan.json"
    p.write_text(json.dumps(cli))
    assert spiderfoot.ingest(c, p)["new"] == 1
    assert spiderfoot.ingest(c, p)["new"] == 0
    with connect(campaign_data(c.name)) as db:
        assert db.execute("SELECT count(*) FROM external").fetchone()[0] == 1
    assert all(ok for _, ok in evidence.verify_raw(campaign_data(c.name)))


def test_cli_loads_and_lists_commands():
    from typer.testing import CliRunner
    from faro.cli import app
    r = CliRunner().invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("collect", "analyze", "leaks", "import", "evidence"):
        assert cmd in r.output
    assert CliRunner().invoke(app, ["leaks", "--help"]).exit_code == 0
