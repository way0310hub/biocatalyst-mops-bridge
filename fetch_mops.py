#!/usr/bin/env python3
"""Fetch official TWSE/TPEx major announcements into public/mops.json."""
from __future__ import annotations

import json
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCES = [
    ("TWSE Open Data", "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"),
    ("TPEx Open Data OTC", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"),
]

OUT = Path("public/mops.json")


def value(row: dict, names: tuple[str, ...]) -> str:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name]).strip()
    normalized = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        v = normalized.get(name.strip().lower())
        if v not in (None, ""):
            return str(v).strip()
    return ""


def normalize_date(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    m = re.match(r"^(\d{3})(\d{2})(\d{2})", raw)
    if m:
        return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{3})[/-](\d{1,2})[/-](\d{1,2})", raw)
    if m:
        return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{4})[/-]?(\d{1,2})[/-]?(\d{1,2})", raw)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return raw[:10]


def fetch(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "BioCatalystTW-MOPS-Bridge/1.0"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=20, context=context) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    return []


def normalize(row: dict, source: str) -> dict | None:
    code = value(row, ("公司代號", "股票代號", "證券代號", "SecuritiesCompanyCode", "公司代碼"))
    date = normalize_date(value(row, ("發言日期", "公告日期", "日期", "Date", "發言日期時間", "發佈日期")))
    title = value(row, ("主旨", "標題", "重大訊息主旨", "Subject", "title"))
    if not code or not title:
        return None
    code = re.sub(r"\D", "", code).zfill(4)
    return {
        "code": code,
        "date": date,
        "title": title,
        "source": source,
        "url": f"https://mops.twse.com.tw/mops/#/web/t146sb05?companyId={code}",
    }


def main() -> None:
    notices = []
    stats = []
    for source, url in SOURCES:
        try:
            rows = fetch(url)
            parsed = [n for row in rows if isinstance(row, dict) for n in [normalize(row, source)] if n]
            notices.extend(parsed)
            stats.append({"source": source, "rows": len(rows), "notices": len(parsed), "ok": True})
        except Exception as exc:  # one unavailable market must not erase prior data
            stats.append({"source": source, "rows": 0, "notices": 0, "ok": False, "error": str(exc)[:160]})

    unique = {}
    for item in notices:
        unique[(item["code"], item["date"], item["title"])] = item
    result = {
        "ok": bool(unique),
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "source": "TWSE／TPEx Open Data",
        "notices": sorted(unique.values(), key=lambda x: (x["date"], x["code"]), reverse=True),
        "sourceStats": stats,
        "partial": not all(s["ok"] for s in stats),
        "status": "更新成功" if unique else "本次沒有取得公告，沿用前次成功資料",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
