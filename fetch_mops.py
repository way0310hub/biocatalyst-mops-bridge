#!/usr/bin/env python3
"""Build a durable, multi-source MOPS announcement history for BioCatalyst TW."""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.request
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

OUT = Path("public/mops.json")
HISTORY_DAYS = 365
MOPS_LOOKBACK_DAYS = int(os.environ.get("MOPS_LOOKBACK_DAYS", "7"))
MOPS_HISTORY_CODES = [
    code.strip()
    for code in os.environ.get("MOPS_HISTORY_CODES", "").split(",")
    if code.strip()
]
TW = ZoneInfo("Asia/Taipei")

OPEN_DATA_SOURCES = [
    ("TWSE Open Data", "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"),
    ("TPEx Open Data OTC", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"),
]

MOPS_ENDPOINT = "https://mops.twse.com.tw/mops/web/ajax_t05st02"


def value(row: dict, names: tuple[str, ...]) -> str:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name]).strip()
    normalized = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        item = normalized.get(name.strip().lower())
        if item not in (None, ""):
            return str(item).strip()
    return ""


def normalize_date(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    match = re.search(r"(\d{3})[/-]?(\d{1,2})[/-]?(\d{1,2})", raw)
    if match:
        return f"{int(match.group(1)) + 1911:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"(\d{4})[/-]?(\d{1,2})[/-]?(\d{1,2})", raw)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return raw[:10]


def clean_text(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", unescape(raw)).strip()


def request_bytes(url: str, data: bytes | None = None) -> bytes:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 BioCatalystTW-MOPS-Bridge/2.0",
        "Accept": "text/html,application/json,application/xhtml+xml",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Referer": "https://mops.twse.com.tw/mops/#/",
    }
    request = urllib.request.Request(url, data=data, headers=headers)
    context = ssl.create_default_context()
    if "mops.twse.com.tw" in url:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            urllib.request.HTTPSHandler(context=context),
        )
        opener.open(
            urllib.request.Request("https://mops.twse.com.tw/mops/#/", headers=headers),
            timeout=30,
        ).close()
        with opener.open(request, timeout=30) as response:
            return response.read()
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        return response.read()


def fetch_json(url: str) -> list[dict]:
    payload = json.loads(request_bytes(url).decode("utf-8-sig"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [row for row in payload["data"] if isinstance(row, dict)]
    return []


def normalize_open_data(row: dict, source: str) -> dict | None:
    code = value(row, ("公司代號", "股票代號", "證券代號", "SecuritiesCompanyCode", "公司代碼"))
    title = value(row, ("主旨", "標題", "重大訊息主旨", "Subject", "title"))
    raw_date = value(row, ("發言日期", "公告日期", "日期", "Date", "發言日期時間", "發佈日期"))
    raw_time = value(row, ("發言時間", "公告時間", "時間", "Time"))
    company = value(row, ("公司名稱", "公司名", "CompanyName", "name"))
    if not code or not title:
        return None
    code = re.sub(r"\D", "", code).zfill(4)
    item_date = normalize_date(raw_date)
    return {
        "code": code,
        "company": company,
        "date": item_date,
        "time": raw_time,
        "title": title,
        "source": source,
        "url": f"https://mops.twse.com.tw/mops/#/web/t146sb05?companyId={code}",
    }


def mops_day_url(target: date) -> str:
    params = {
        "encodeURIComponent": "1",
        "step": "1",
        "step00": "0",
        "firstin": "1",
        "off": "1",
        "TYPEK": "all",
        "year": str(target.year - 1911),
        "month": f"{target.month:02d}",
        "day": f"{target.day:02d}",
    }
    return f"{MOPS_ENDPOINT}?{urlencode(params)}"


def parse_mops_rows(body: bytes, target: date) -> list[dict]:
    text = body.decode("utf-8", errors="replace")
    if "�" in text or ("公司代號" not in text and "公司名稱" not in text):
        text = body.decode("big5", errors="replace")

    notices: list[dict] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        cells = [
            clean_text(cell)
            for cell in re.findall(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row_html, flags=re.I | re.S)
        ]
        cells = [cell for cell in cells if cell]
        if len(cells) < 4:
            continue

        code = next((re.sub(r"\D", "", cell).zfill(4) for cell in cells if re.fullmatch(r"\D*(\d{4})\D*", cell)), "")
        if not code:
            continue

        date_index = next(
            (index for index, cell in enumerate(cells) if re.search(r"\d{3}[/-]\d{1,2}[/-]\d{1,2}", cell)),
            None,
        )
        item_date = normalize_date(cells[date_index]) if date_index is not None else target.isoformat()
        time_value = next((cell for cell in cells if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", cell)), "")

        company = ""
        if date_index is not None and date_index > 0:
            company = cells[date_index - 1]
        if not company:
            company = next((cell for cell in cells if cell != code and len(cell) > 1), "")

        title = ""
        if len(cells) > 6:
            title = cells[6]
        if not title or title in {code, company, item_date, time_value}:
            candidates = [
                cell for cell in cells
                if cell not in {code, company, item_date, time_value}
                and len(cell) >= 6
                and not re.fullmatch(r"\d+", cell)
            ]
            title = max(candidates, key=len, default="")
        if not title:
            continue

        notices.append({
            "code": code,
            "company": company,
            "date": item_date,
            "time": time_value,
            "title": title,
            "source": "MOPS 重大訊息",
            "url": f"https://mops.twse.com.tw/mops/#/web/t146sb05?companyId={code}",
        })
    return notices


def fetch_mops_day(target: date) -> list[dict]:
    return parse_mops_rows(request_bytes(mops_day_url(target)), target)




def mops_company_url(code: str, market_type: str, start: date, end: date) -> str:
    params = {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "TYPEK": market_type,
        "co_id": code,
        "year": str(start.year - 1911),
        "month": "",
        "b_date": start.strftime("%Y%m%d"),
        "e_date": end.strftime("%Y%m%d"),
    }
    return f"https://mops.twse.com.tw/mops/web/ajax_t05st01?{urlencode(params)}"


def fetch_mops_company_history(code: str, market_type: str, start: date, end: date) -> list[dict]:
    return parse_mops_rows(
        request_bytes(mops_company_url(code, market_type, start, end)),
        start,
    )

def fetch_tpex_emerging_notices() -> list[dict]:
    """Fetch the latest emerging-company notice cards from TPEx's official page.

    TPEx's OpenAPI Swagger currently has no emerging-company major-notice
    endpoint. The official market-important page exposes the latest emerging
    notice as a MOPS link with TYPEK=rotc and company/date/time identifiers.
    """
    body = request_bytes("https://www.tpex.org.tw/zh-tw/market-important.html")
    text = unescape(body.decode("utf-8", errors="replace"))

    notices: list[dict] = []
    # Decode HTML entities first because the page renders query separators as
    # &amp;, then inspect every official market-important card link.
    pattern = r'<a[^>]*href=["\']([^"\']*COMPANY_ID=\d+[^"\']*)["\'][^>]*>(.*?)</a>'
    for href, inner in re.findall(pattern, text, flags=re.I | re.S):
        if not re.search(r"(?:TYPEK=|TYPEK%3D)rotc", href, flags=re.I):
            continue
        code_match = re.search(r"COMPANY_ID=(\d+)", href, flags=re.I)
        date_match = re.search(r"SPOKE_DATE=(\d{8})", href, flags=re.I)
        time_match = re.search(r"SPOKE_TIME=(\d{6})", href, flags=re.I)
        if not (code_match and date_match):
            continue

        card_text = clean_text(inner)
        title_match = re.match(
            r"^\[([^\]]+)\]\s*(.*?)(?:\s+\d{2,3}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}:\d{2})?$",
            card_text,
        )
        if not title_match:
            continue

        code = code_match.group(1).zfill(4)
        raw_date = date_match.group(1)
        item_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        raw_time = time_match.group(1) if time_match else ""
        time_value = f"{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:]}" if len(raw_time) == 6 else raw_time
        company = clean_text(title_match.group(1))
        title = clean_text(title_match.group(2)).rstrip(".")
        if not title:
            continue

        notices.append({
            "code": code,
            "company": company,
            "date": item_date,
            "time": time_value,
            "title": title,
            "source": "TPEx 官方市場重大訊息",
            "url": f"https://mops.twse.com.tw/mops/#/web/t146sb05?companyId={code}",
        })
    return notices

def load_existing() -> list[dict]:
    try:
        payload = json.loads(OUT.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if isinstance(payload, dict):
        notices = payload.get("notices", [])
    else:
        notices = payload
    return [item for item in notices if isinstance(item, dict) and item.get("code") and item.get("title")]


def item_key(item: dict) -> tuple[str, str, str, str]:
    # 同一公司可能在不同日期／時間重複發布相同主旨；不能只用
    # 公司代號＋主旨去重，否則會把不同公告錯誤合併。
    return (
        str(item.get("code", "")),
        str(item.get("date", "")),
        str(item.get("time", "")),
        re.sub(r"\s+", " ", str(item.get("title", "")).strip()),
    )


def item_sort_key(item: dict) -> tuple[str, str, str]:
    return (str(item.get("date", "")), str(item.get("time", "")), str(item.get("code", "")))


def main() -> None:
    today = datetime.now(TW).date()
    cutoff = today - timedelta(days=HISTORY_DAYS)
    existing = load_existing()
    notices = [item for item in existing if str(item.get("date", "")) >= cutoff.isoformat()]
    stats: list[dict] = []
    fetched_any = False
    failures = 0
    open_data_notices = 0

    for source, url in OPEN_DATA_SOURCES:
        try:
            rows = fetch_json(url)
            parsed = [item for row in rows if (item := normalize_open_data(row, source))]
            notices.extend(parsed)
            open_data_notices += len(parsed)
            fetched_any = True
            stats.append({"source": source, "rows": len(rows), "notices": len(parsed), "ok": True})
        except Exception as exc:
            failures += 1
            stats.append({"source": source, "rows": 0, "notices": 0, "ok": False, "error": str(exc)[:160]})

    try:
        emerging = fetch_tpex_emerging_notices()
        notices.extend(emerging)
        fetched_any = True
        stats.append({"source": "TPEx 官方市場重大訊息", "notices": len(emerging), "ok": True})
    except Exception as exc:
        failures += 1
        stats.append({"source": "TPEx 官方市場重大訊息", "notices": 0, "ok": False, "error": str(exc)[:160]})

    if MOPS_HISTORY_CODES:
        history_start = today - timedelta(days=HISTORY_DAYS)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(fetch_mops_company_history, code, "all", history_start, today): code
                for code in MOPS_HISTORY_CODES
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    parsed = future.result()
                    notices.extend(parsed)
                    fetched_any = True
                    stats.append({
                        "source": "MOPS 歷史公司查詢",
                        "code": code,
                        "from": history_start.isoformat(),
                        "to": today.isoformat(),
                        "notices": len(parsed),
                        "ok": True,
                    })
                except Exception as exc:
                    failures += 1
                    stats.append({
                        "source": "MOPS 歷史公司查詢",
                        "code": code,
                        "notices": 0,
                        "ok": False,
                        "error": str(exc)[:160],
                    })

    mops_dates = [today - timedelta(days=offset) for offset in range(MOPS_LOOKBACK_DAYS + 1)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_mops_day, target): target for target in mops_dates}
        for future in as_completed(futures):
            target = futures[future]
            try:
                parsed = future.result()
                notices.extend(parsed)
                fetched_any = True
                stats.append({"source": "MOPS 重大訊息", "date": target.isoformat(), "notices": len(parsed), "ok": True})
            except Exception as exc:
                failures += 1
                stats.append({"source": "MOPS 重大訊息", "date": target.isoformat(), "notices": 0, "ok": False, "error": str(exc)[:160]})

    # 若 MOPS 在整個回補期間都回傳 0 筆，但官方 TWSE/TPEx Open Data
    # 明確有公告，代表目前使用的舊 AJAX 端點很可能只回傳空殼／空結果。
    # 不能把這種情況標示成「更新成功」。
    mops_stats = [item for item in stats if item.get("source") == "MOPS 重大訊息"]
    mops_total = sum(int(item.get("notices", 0)) for item in mops_stats)
    if mops_stats and mops_total == 0 and open_data_notices > 0:
        for item in mops_stats:
            item["ok"] = False
        stats.append({
            "source": "MOPS 重大訊息",
            "notices": 0,
            "ok": False,
            "error": "MOPS 舊 AJAX 查詢在回補期間全部回傳 0 筆；可能是新版 SPA 動態端點，未視為有效更新。",
        })
        failures += 1

    unique: dict[tuple[str, str, str, str], dict] = {}
    for item in notices:
        if str(item.get("date", "")) < cutoff.isoformat():
            continue
        key = item_key(item)
        if key not in unique or item_sort_key(item) > item_sort_key(unique[key]):
            unique[key] = item

    ordered = sorted(unique.values(), key=item_sort_key, reverse=True)
    result = {
        "ok": bool(ordered),
        "fetchedAt": datetime.now(TW).isoformat(),
        "source": "MOPS + TWSE／TPEx Open Data",
        "historyFrom": cutoff.isoformat(),
        "historyTo": today.isoformat(),
        "notices": ordered,
        "sourceStats": stats,
        "partial": failures > 0,
        "status": (
            "更新成功"
            if fetched_any and failures == 0
            else "部分來源更新成功・保留前次成功資料"
            if fetched_any
            else "本次抓取失敗・沿用前次成功資料"
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
