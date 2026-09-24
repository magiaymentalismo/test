#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# ===================== CONFIG ===================== #

DINATICKET_EVENTS = {
    "Escondido": "https://www.dinaticket.com/es/provider/20845/event/4948919",
    "Escondido2": " https://www.dinaticket.com/es/provider/19977/event/4950104"
}

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
    )
}

TZ = ZoneInfo("Europe/Madrid")

TEMPLATE_PATH = Path("template.html")
MANIFEST_PATH = Path("manifest.json")
SW_PATH = Path("sw.js")
DOCS_DIR = Path("docs")


MESES = {
    "Ene.": "01", "Ene": "01",
    "Feb.": "02", "Feb": "02",
    "Mar.": "03", "Mar": "03",
    "Abr.": "04", "Abr": "04",
    "May.": "05", "May": "05",
    "Jun.": "06", "Jun": "06",
    "Jul.": "07", "Jul": "07",
    "Ago.": "08", "Ago": "08",
    "Sep.": "09", "Sep": "09",
    "Oct.": "10", "Oct": "10",
    "Nov.": "11", "Nov": "11",
    "Dic.": "12", "Dic": "12",
}


# ================== HELPERS ================== #

def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# ================== OUTPUT ================== #

def write_html(payload: dict) -> None:
    if not TEMPLATE_PATH.exists():
        print("❌ Error: No existe template.html")
        return

    html_template = TEMPLATE_PATH.read_text("utf-8")
    html = html_template.replace(
        "{{PAYLOAD_JSON}}",
        json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    )

    DOCS_DIR.mkdir(exist_ok=True)

    (DOCS_DIR / "index.html").write_text(html, "utf-8")
    print("✔ Generado docs/index.html")

    if MANIFEST_PATH.exists():
        shutil.copy(MANIFEST_PATH, DOCS_DIR / "manifest.json")
        print("✔ Copiado manifest.json")

    if SW_PATH.exists():
        shutil.copy(SW_PATH, DOCS_DIR / "sw.js")
        print("✔ Copiado sw.js")


def write_schedule_json(payload: dict) -> None:
    DOCS_DIR.mkdir(exist_ok=True)

    (DOCS_DIR / "schedule.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        "utf-8",
    )

    print("✔ Generado docs/schedule.json")


# ================== DINATICKET ================== #

def parse_dinaticket_hour(raw: str) -> str | None:
    hora_txt = raw.strip().lower()
    hora_txt = hora_txt.replace(" ", "").replace("h", ":").rstrip(":")

    m = re.match(r"^(\d{1,2})(?::?(\d{2}))?$", hora_txt)
    if not m:
        return hora_txt or None

    hh = int(m.group(1))
    mm = int(m.group(2) or "00")
    return f"{hh:02d}:{mm:02d}"


def fetch_functions_dinaticket(url: str, timeout: int = 20) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    out: list[dict] = []

    for session in soup.find_all("div", class_="js-session-row"):
        parent = session.find_parent("div", class_="js-session-group")
        if not parent:
            continue

        date_div = parent.find("div", class_="session-card__date")
        if not date_div:
            continue

        dia = date_div.find("span", class_="num_dia")
        mes = date_div.find("span", class_="mes")
        if not dia or not mes:
            continue

        mes_txt = mes.get_text(strip=True)
        mes_num = MESES.get(mes_txt) or MESES.get(mes_txt.replace(".", ""))

        if not mes_num:
            print("DEBUG mes no reconocido Dinaticket:", repr(mes_txt))
            continue

        now = datetime.now(TZ)
        anio = now.year

        fecha_iso_tmp = f"{anio}-{mes_num}-{dia.get_text(strip=True).zfill(2)}"
        fecha_dt = datetime.strptime(fecha_iso_tmp, "%Y-%m-%d")

        if fecha_dt.date() < now.date():
            fecha_dt = fecha_dt.replace(year=anio + 1)

        fecha_iso = fecha_dt.strftime("%Y-%m-%d")
        fecha_label = fecha_dt.strftime("%d %b %Y")

        hora_span = session.find("span", class_="session-card__time-session")
        hora = parse_dinaticket_hour(hora_span.get_text(strip=True) if hora_span else "")

        quotas = session.find_all("div", class_="js-quota-row")

        if not quotas:
            cap = None
            stock = None
            vendidas = None
        else:
            cap = sum(safe_int(q.get("data-quota-total", 0)) for q in quotas)
            stock = sum(safe_int(q.get("data-stock", 0)) for q in quotas)
            vendidas = max(0, cap - stock)

        out.append({
            "fecha_label": fecha_label,
            "fecha_iso": fecha_iso,
            "hora": hora,
            "vendidas_dt": vendidas,
            "capacidad": cap,
            "stock": stock,
        })

    return sorted(out, key=lambda f: (f["fecha_iso"], f.get("hora") or "00:00"))


# ================== PAYLOAD ================== #

def build_rows(funcs: list[dict]) -> list[list]:
    return [
        [
            f.get("fecha_label"),
            f.get("hora"),
            f.get("vendidas_dt"),
            f.get("fecha_iso"),
            f.get("capacidad"),
            f.get("stock"),
        ]
        for f in funcs
    ]


def build_payload(eventos: dict[str, list[dict]]) -> dict:
    now = datetime.now(TZ)
    out: dict[str, dict] = {}

    for sala, funcs in eventos.items():
        proximas: list[dict] = []
        pasadas: list[dict] = []

        for f in funcs:
            fecha_iso = f["fecha_iso"]
            hora_txt = f.get("hora") or "00:00"

            try:
                ses_dt = datetime.strptime(
                    f"{fecha_iso} {hora_txt}",
                    "%Y-%m-%d %H:%M"
                ).replace(tzinfo=TZ)
            except Exception:
                ses_dt = None

            if ses_dt and ses_dt >= now:
                proximas.append(f)
            elif ses_dt:
                pasadas.append(f)
            else:
                d = datetime.strptime(fecha_iso, "%Y-%m-%d").date()
                if d >= now.date():
                    proximas.append(f)
                else:
                    pasadas.append(f)

        proximas.sort(key=lambda f: (f["fecha_iso"], f.get("hora") or "00:00"))

        print(
            f"[DEBUG] {sala}: total={len(funcs)} "
            f"· proximas={len(proximas)} · pasadas={len(pasadas)}"
        )

        out[sala] = {
            "table": {
                "headers": [
                    "Fecha", "Hora", "Vendidas", "FechaISO",
                    "Capacidad", "Stock"
                ],
                "rows": build_rows(proximas),
            },
            "proximas": {
                "table": {
                    "headers": [
                        "Fecha", "Hora", "Vendidas", "FechaISO",
                        "Capacidad", "Stock"
                    ],
                    "rows": build_rows(proximas),
                }
            },
        }

    return {
        "generated_at": datetime.now(TZ).isoformat(),
        "eventos": out,
    }


# ================== MAIN ================== #

if __name__ == "__main__":
    current: dict[str, list[dict]] = {}

    for sala, url in DINATICKET_EVENTS.items():
        try:
            funcs = fetch_functions_dinaticket(url)
        except Exception as e:
            print(f"ERROR Dinaticket {sala}: {e}")
            funcs = []

        current[sala] = funcs
        print(f"{sala}: {len(funcs)} funciones Dinaticket extraídas")

    payload = build_payload(current)

    write_html(payload)
    write_schedule_json(payload)
