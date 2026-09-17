#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KözigÁllás TUI Alkalmazás (kozig-app.py)
Terminálos felület közigazgatási álláshirdetések böngészéséhez,
görgetéséhez, rendezéséhez és böngészőben való megnyitásához.
Dinamikusan alkalmazkodik a terminál ablak méretéhez, levágja
a túl hosszú szövegeket, és részletes előnézetet biztosít.
DEFAULT_COUNTIES alapján vármegyékre szűrhető.
"""

import json
import asyncio
import webbrowser
from pathlib import Path
from typing import List, Dict, Any, Optional

import aiohttp
from selectolax.parser import HTMLParser

from textual.app import App, ComposeResult
from textual import work, on, events
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, DataTable, Static, Input, Select
from textual.coordinate import Coordinate


JOB_BASE_URL = "https://kozszolgallas.ksz.gov.hu/JobAd/Info/"
LIST_URL = "https://kozszolgallas.ksz.gov.hu/JobAd/List"

HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7',
    'Connection': 'keep-alive',
    'DNT': '1',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/121.0.0.0 Safari/537.36 OPR/108.0.0.0'
    ),
    'X-Requested-With': 'XMLHttpRequest',
}

DEFAULT_COUNTIES = {
    'county.bacskiskun': 'Bács-Kiskun',
    'county.baranya': 'Baranya',
    'county.bekes': 'Békés',
    'county.borsodabaujzemplen': 'Borsod-Abaúj-Zemplén',
    'county.budapest': 'Budapest',
    'county.csongrad': 'Csongrád-Csanád',
    'county.fejer': 'Fejér',
    'county.gyormosonsopron': 'Győr-Moson-Sopron',
    'county.hajdubihar': 'Hajdú-Bihar',
    'county.heves': 'Heves',
    'county.jasznagykunszolnok': 'Jász-Nagykun-Szolnok',
    'county.komaromesztergom': 'Komárom-Esztergom',
    'county.nograd': 'Nógrád',
    'county.pest': 'Pest-megye',
    'county.somogy': 'Somogy',
    'county.szabolcsszatmarbereg': 'Szabolcs-Szatmár-Bereg',
    'county.tolna': 'Tolna',
    'county.vas': 'Vas',
    'county.veszprem': 'Veszprém',
    'county.zala': 'Zala',
}

HUNGARIAN_CHAR_MAP = {
    'á': 'a\u0100', 'é': 'e\u0100', 'í': 'i\u0100', 'ó': 'o\u0100', 'ö': 'o\u0101', 'ő': 'o\u0102',
    'ú': 'u\u0100', 'ü': 'u\u0101', 'ű': 'u\u0102',
}


def hungarian_sort_key(val: Any) -> str:
    """Magyar ABC szerinti rendezés segédfüggvénye."""
    if not isinstance(val, str):
        return str(val or "")
    s = val.lower().strip()
    return "".join(HUNGARIAN_CHAR_MAP.get(c, c) for c in s)


def strip_accents(val: Any) -> str:
    """Ékezetek eltávolítása a kereséshez."""
    if not isinstance(val, str):
        return str(val or "")
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", val.lower())
        if unicodedata.category(c) != "Mn"
    )


def truncate_str(text: Any, max_len: int) -> str:
    """Levágja a szöveget a megadott karakterszámra, hogy a sorok ne nyúljanak túl."""
    s = " ".join(str(text or "").split())
    if max_len <= 1:
        return s[:max_len]
    if len(s) > max_len:
        return s[:max_len - 1] + "…"
    return s


def matches_county(job_megye: str, target_county: str) -> bool:
    """Megvizsgálja, hogy az álláshirdetés megyéje illeszkedik-e a kiválasztott vármegyére."""
    if target_county == "ALL":
        return True
    j_clean = strip_accents((job_megye or "").lower().strip())
    t_clean = strip_accents((target_county or "").lower().strip())
    if t_clean == "pest":
        return j_clean == "pest" or (j_clean.startswith("pest") and "budapest" not in j_clean)
    if t_clean == "budapest":
        return "budapest" in j_clean
    return t_clean in j_clean or j_clean in t_clean



async def fetch_page(session: aiohttp.ClientSession, page_num: int, county_code: Optional[str] = None):
    """Lekér egyetlen oldalt a kozszolgallas portálról."""
    params = {
        'page': str(page_num),
        'sort': 'Created DESC',
    }
    if county_code:
        params['countyCode'] = county_code

    try:
        async with session.get(LIST_URL, params=params, timeout=aiohttp.ClientTimeout(total=12)) as response:
            if response.status != 200:
                return None, response.status
            text = await response.text()
            return HTMLParser(text), response.status
    except Exception:
        return None, 0


async def scrape_county_jobs(county_code: str, county_name: str, max_pages: int = 50) -> List[Dict[str, str]]:
    """Lekéri a megadott vármegye állásait 5 oldalas kötegekben."""
    jobs = []
    page = 1
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        while page <= max_pages:
            tasks = [fetch_page(session, page + i, county_code) for i in range(5)]
            results = await asyncio.gather(*tasks)

            stop_processing = False
            for parser, status in results:
                if not parser or status != 200:
                    stop_processing = True
                    break

                divs = parser.css('div.jobad')
                if not divs:
                    stop_processing = True
                    break

                for job in divs:
                    try:
                        div_id = job.css_first("div")
                        job_id = div_id.attributes.get("id", "") if div_id else ""
                        hiv = f"{JOB_BASE_URL}{job_id}" if job_id else ""

                        mit_elem = job.css_first('h4 strong')
                        if mit_elem:
                            raw_mit = mit_elem.text()
                            mit = raw_mit.split('\n')[1].strip().upper() if '\n' in raw_mit else raw_mit.strip().upper()
                        else:
                            mit = ""

                        kinel_elem = job.css_first('h5 strong')
                        kinel = kinel_elem.text(strip=True) if kinel_elem else ""

                        lis = job.css('li')
                        hol = lis[2].text(strip=True) if len(lis) > 2 else ""

                        h6s = job.css('div h6')
                        meddig = h6s[1].text(strip=True) if len(h6s) > 1 else ""

                        # Megtisztítjuk a whitespace-ektől
                        mit = " ".join(mit.split())
                        kinel = " ".join(kinel.split())
                        hol = " ".join(hol.split())
                        meddig = " ".join(meddig.split())

                        if hiv and mit:
                            jobs.append({
                                "id": job_id,
                                "hiv": hiv,
                                "mit": mit,
                                "kinél": kinel,
                                "hol": hol,
                                "meddig": meddig,
                                "megye": county_name
                            })
                    except Exception:
                        continue

            if stop_processing:
                break
            page += 5
    return jobs


def load_from_json_files() -> List[Dict[str, str]]:
    """Betölti a lokális fájlokból (data.json vagy kozig_cache.json) az adatokat."""
    cwd = Path.cwd()
    cache_file = cwd / "kozig_cache.json"
    data_file = cwd / "data.json"

    items: List[Dict[str, str]] = []

    # 1. Először a friss cache-t ellenőrizzük
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list) and loaded:
                    return loaded
        except Exception:
            pass

    # 2. Ha nincs cache, nézzük meg a data.json-t
    if data_file.exists():
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            raw_list = raw_data if isinstance(raw_data, list) else raw_data.get('Items', [])
            for item in raw_list:
                item_id = str(item.get("Id", ""))
                deadline = str(item.get("SubmissionDeadline") or "")
                if "T" in deadline:
                    deadline = deadline.split("T")[0]

                creator = " ".join(str(item.get("CreatorOrganizationName") or "").replace("+", " ").split())
                work_type = " ".join(str(item.get("WorkTypeName") or "").replace("+", " ").split())
                city = " ".join(str(item.get("CityName") or "").replace("+", " ").split())
                speciality = " ".join(str(item.get("Speciality") or "").replace("+", " ").split())
                group = " ".join(str(item.get("CityGroup") or "").replace("+", " ").split())

                link = f"{JOB_BASE_URL}{item_id}" if item_id else ""
                items.append({
                    "id": item_id,
                    "hiv": link,
                    "mit": speciality.upper(),
                    "kinél": creator,
                    "hol": city,
                    "meddig": deadline,
                    "megye": group
                })
        except Exception:
            pass

    return items


def save_to_cache(jobs: List[Dict[str, str]]) -> None:
    """Elmenti az állásokat gyorsítótárba."""
    try:
        cache_file = Path.cwd() / "kozig_cache.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class KozigApp(App):
    """KözigÁllás TUI Böngésző és Kereső Alkalmazás."""

    TITLE = "KözigÁllás TUI Böngésző"
    SUB_TITLE = "Álláshirdetések görgetése, rendezése és megnyitása"

    CSS = """
    Screen {
        background: #0f172a;
        color: #f8fafc;
    }

    Header {
        background: #1e293b;
        color: #38bdf8;
        dock: top;
        height: 3;
    }

    #top-bar {
        background: #1e293b;
        border-bottom: solid #334155;
        height: 4;
        padding: 0 1;
        layout: horizontal;
        align-vertical: middle;
    }

    #top-bar.compact {
        height: 3;
    }

    #status-info {
        width: 1fr;
        color: #94a3b8;
        content-align: left middle;
        text-style: bold;
    }

    #controls-box {
        width: auto;
        layout: horizontal;
        align-vertical: middle;
        dock: right;
    }

    #county-select {
        width: 40;
        margin-right: 1;
        height: 3;
    }

    #search-input {
        background: #0f172a;
        border: tall #0284c7;
        color: #38bdf8;
        padding: 0 1;
        height: 3;
        width: 28;
    }

    #search-input:focus {
        border: tall #38bdf8;
        background: #1e293b;
    }

    #table-container {
        height: 1fr;
        padding: 0 1;
    }

    DataTable {
        background: #0f172a;
        border: solid #1e293b;
        height: 100%;
    }

    DataTable > .datatable--header {
        background: #1e293b;
        color: #38bdf8;
        text-style: bold;
    }

    /* Az aktuálisan rendezett oszlop fejlécének kiemelése */
    DataTable > .datatable--header .sorted-column {
        background: #334155;
        color: #ffffff;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #0284c7;
        color: #ffffff;
        text-style: bold;
    }

    DataTable > .datatable--hover {
        background: #1e293b;
    }

    #preview-bar {
        dock: bottom;
        height: 3;
        background: #1e293b;
        border-top: solid #0284c7;
        padding: 0 1;
        color: #e2e8f0;
    }

    #loading-bar {
        dock: bottom;
        height: 1;
        background: #0369a1;
        color: #ffffff;
        text-align: center;
        display: none;
    }

    #loading-bar.visible {
        display: block;
    }

    Footer {
        background: #1e293b;
        color: #cbd5e1;
        dock: bottom;
    }
    """

    BINDINGS = [
        Binding("enter", "open_link", "Megnyitás", show=True, priority=True),
        Binding("f1", "sort_mit", "Pozíció", show=True),
        Binding("1", "sort_mit", "Pozíció", show=False),
        Binding("f2", "sort_kinel", "Munkáltató", show=True),
        Binding("2", "sort_kinel", "Munkáltató", show=False),
        Binding("f3", "sort_hol", "Település", show=True),
        Binding("3", "sort_hol", "Település", show=False),
        Binding("f4", "sort_meddig", "Határidő", show=True),
        Binding("4", "sort_meddig", "Határidő", show=False),
        Binding("f5", "toggle_sort_direction", "Irány", show=True),
        Binding("5", "toggle_sort_direction", "Irány", show=False),
        Binding("f6", "refresh_data", "Frissítés", show=True),
        Binding("r", "refresh_data", "Frissítés", show=False),
        Binding("f7", "focus_search", "Keresés", show=True),
        Binding("slash", "focus_search", "Keresés", show=False),
        Binding("f8", "toggle_density", "Nézetméret", show=True),
        Binding("z", "toggle_density", "Nézetméret", show=False),
        Binding("f9", "cycle_county", "Vármegye", show=True),
        Binding("m", "cycle_county", "Vármegye", show=False),
        Binding("escape", "clear_search", "Szűrők törlése", show=True, priority=True),
        Binding("q", "quit", "Kilépés", show=True),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.all_jobs: List[Dict[str, str]] = []
        self.displayed_jobs: List[Dict[str, str]] = []
        self.sort_field: str = "mit"
        self.sort_descending: bool = False
        self.search_filter: str = ""
        self.county_filter: str = "ALL"
        self.row_index_map: List[Dict[str, str]] = []
        self.compact_mode: bool = False
        self.current_col_widths: Dict[str, Any] = {}
        self._last_width: int = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        county_options = [("Összes vármegye", "ALL")] + [
            (name, name) for name in DEFAULT_COUNTIES.values()
        ]
        with Horizontal(id="top-bar"):
            yield Static("Betöltés...", id="status-info")
            with Horizontal(id="controls-box"):
                yield Select(county_options, value="ALL", allow_blank=False, id="county-select")
                yield Input(placeholder="🔍 Keresés... (ESC = táblázat)", id="search-input")
        with Vertical(id="table-container"):
            yield DataTable(id="jobs-table", cursor_type="row", zebra_stripes=True)
        yield Static("Válassz ki egy állást a listából az előnézethez.", id="preview-bar")
        yield Static("⏳ Friss adatok letöltése folyamatban...", id="loading-bar")
        yield Footer()

    async def on_mount(self) -> None:
        """Alkalmazás indulásakor inicializálja a táblázatot és betölti az adatokat."""
        self.setup_table_columns()

        # 1. Először megpróbáljuk betölteni a meglévő helyi adatokat az azonnali megjelenítéshez
        local_data = load_from_json_files()
        if local_data:
            self.all_jobs = local_data
            self.apply_filter_and_sort()
            self.update_status_bar()
        else:
            self.query_one("#status-info", Static).update("Nincs helyi adat. Letöltés...")
            self.fetch_fresh_jobs()

        table = self.query_one(DataTable)
        table.focus()

    def compute_column_widths(self, total_width: Optional[int] = None) -> Dict[str, Any]:
        """Kiszámítja az oszlopok szélességét úgy, hogy soha ne lógjanak ki a képernyőről."""
        if total_width is None or total_width <= 0:
            total_width = self.size.width or 100

        # Levonjuk a szegélyeket, margókat és a görgetősáv helyét (~6-8 karakter)
        padding_deduction = 4 if self.compact_mode else 6
        net_w = max(45, total_width - padding_deduction)

        # Keskeny képernyőn (< 92) elrejtjük a Megye oszlopot, hogy a táblázat elférjen
        show_megye = (net_w >= 92)

        col_id = 4
        col_date = 10 if net_w < 110 else 11
        col_city = 12 if net_w < 110 else 15
        col_county = 14 if (show_megye and net_w < 120) else (18 if show_megye else 0)

        fixed_total = col_id + col_date + col_city + col_county + (8 if not self.compact_mode else 5)
        remaining = max(24, net_w - fixed_total)

        # A maradék hely elosztása: 55% pozíció, 45% munkáltató
        col_pos = max(14, int(remaining * 0.55))
        col_emp = max(10, remaining - col_pos)

        return {
            "show_megye": show_megye,
            "id": col_id,
            "mit": col_pos,
            "kinél": col_emp,
            "hol": col_city,
            "meddig": col_date,
            "megye": col_county,
            "net_w": net_w,
        }

    def setup_table_columns(self, total_width: Optional[int] = None) -> None:
        """Beállítja a táblázat oszlopait az ablak méretéhez igazítva."""
        table = self.query_one(DataTable)
        table.clear(columns=True)

        w = self.compute_column_widths(total_width)
        self.current_col_widths = w

        table.add_column("#", width=w["id"], key="col_id")
        table.add_column("Pozíció / Megnevezés", width=w["mit"], key="col_mit")
        table.add_column("Munkáltató", width=w["kinél"], key="col_kinel")
        table.add_column("Település", width=w["hol"], key="col_hol")
        table.add_column("Határidő", width=w["meddig"], key="col_meddig")
        if w["show_megye"]:
            table.add_column("Megye / Térség", width=w["megye"], key="col_megye")

        self.highlight_sorted_column()

    def highlight_sorted_column(self) -> None:
        """Világosabb háttérrel kiemeli az aktuálisan rendezett oszlop fejlécét."""
        table = self.query_one(DataTable)

        # Először minden fejléc visszaállítása az alap háttérre.
        for column_key in ("col_mit", "col_kinel", "col_hol", "col_meddig", "col_megye"):
            try:
                table.get_column(column_key).label.stylize = None
            except Exception:
                pass

        # Textual verziók között eltérhet a fejléc belső objektumának API-ja,
        # ezért a biztos megoldás a fejléc cellájának CSS class alapú jelölése.
        try:
            header = table.query_one(".datatable--header")
            for child in header.query(".datatable--header-cell"):
                child.remove_class("sorted-column")
        except Exception:
            pass

        # Az aktuális oszlop fejlécét jelöljük.
        column_map = {
            "mit": "col_mit",
            "kinél": "col_kinel",
            "hol": "col_hol",
            "meddig": "col_meddig",
            "megye": "col_megye",
        }
        active_key = column_map.get(self.sort_field)
        if not active_key:
            return

        try:
            header = table.query_one(".datatable--header")
            for child in header.query(".datatable--header-cell"):
                if getattr(child, "column_key", None) == active_key:
                    child.add_class("sorted-column")
        except Exception:
            pass

    def on_resize(self, event: events.Resize) -> None:
        """Ablak átméretezésekor újraszámolja az oszlopszélességeket a kilógás megakadályozására."""
        new_width = event.size.width
        if abs(self._last_width - new_width) >= 2:
            self._last_width = new_width
            self.setup_table_columns(new_width)
            self.apply_filter_and_sort()

    def update_status_bar(self) -> None:
        """Frissíti a fejléc alatti státusz szöveget az aktuális szűrésről és rendezésről."""
        field_labels = {
            "mit": "Pozíció",
            "kinél": "Munkáltató",
            "hol": "Település",
            "meddig": "Határidő",
            "megye": "Megye"
        }
        dir_symbol = "🔽 Csökkenő" if self.sort_descending else "🔼 Növekvő"
        sort_name = field_labels.get(self.sort_field, self.sort_field)

        count_text = f"Találatok: {len(self.displayed_jobs)} / {len(self.all_jobs)}"
        c_disp = "Összes" if self.county_filter == "ALL" else self.county_filter
        county_text = f" | Vármegye: [bold green]{c_disp}[/]"
        filter_text = f" | Szűrő: '{self.search_filter}'" if self.search_filter else ""
        density_text = " | [bold yellow]Sűrű nézet[/]" if self.compact_mode else ""
        text = f"📋 {count_text}{county_text}{filter_text} | Rendezés: [bold cyan]{sort_name}[/] ({dir_symbol}){density_text}"

        self.query_one("#status-info", Static).update(text)

    def update_preview_bar(self, job: Optional[Dict[str, str]] = None) -> None:
        """Megjeleníti a kijelölt állás teljes, csonkítatlan adatait az alsó előnézeti sávban."""
        bar = self.query_one("#preview-bar", Static)
        if not job:
            bar.update("🔍 [dim]Válassz ki egy sort a nyilakkal, nyomj Enter-t a webes megnyitáshoz[/]")
            return

        mit = job.get("mit", "-")
        kinel = job.get("kinél", "-")
        hol = job.get("hol", "-")
        megye = job.get("megye", "-")
        meddig = job.get("meddig", "-")

        line1 = f"📌 [bold cyan]{mit}[/]"
        line2 = f"🏛️ [bold white]{kinel}[/]  |  📍 {hol} ({megye})  |  📅 Határidő: [bold yellow]{meddig}[/]  |  [bold green]⏎ ENTER: Megnyitás[/]"
        bar.update(f"{line1}\n{line2}")

    def apply_filter_and_sort(self) -> None:
        """Leszűri, levágja és rendezi az adatokat a táblázatban."""
        table = self.query_one(DataTable)

        # 1. Vármegye szűrés (DEFAULT_COUNTIES alapján)
        if self.county_filter != "ALL":
            county_filtered = [
                j for j in self.all_jobs
                if matches_county(j.get("megye", ""), self.county_filter)
            ]
        else:
            county_filtered = list(self.all_jobs)

        # 2. Szöveges keresési szűrő (ékezet-független)
        # A keresés mindig az éppen rendezett oszlopban történik.
        filter_query = strip_accents(self.search_filter.strip())
        if filter_query:
            self.displayed_jobs = [
                j for j in county_filtered
                if filter_query in strip_accents(j.get(self.sort_field, ""))
            ]
        else:
            self.displayed_jobs = county_filtered

        # 3. Rendezés
        self.displayed_jobs.sort(
            key=lambda item: hungarian_sort_key(item.get(self.sort_field, "")),
            reverse=self.sort_descending
        )

        # Tábla újratöltése levágott szövegekkel a túlnyúlás ellen
        table.clear()
        self.row_index_map = []
        w = self.current_col_widths or self.compute_column_widths(self.size.width)

        for idx, job in enumerate(self.displayed_jobs, 1):
            row_key = f"job_{idx}"
            pos_text = truncate_str(job.get("mit", ""), w["mit"])
            emp_text = truncate_str(job.get("kinél", ""), w["kinél"])
            city_text = truncate_str(job.get("hol", ""), w["hol"])
            date_text = truncate_str(job.get("meddig", ""), w["meddig"])

            if w.get("show_megye", True):
                county_text = truncate_str(job.get("megye", ""), w["megye"])
                table.add_row(
                    str(idx),
                    pos_text,
                    emp_text,
                    city_text,
                    date_text,
                    county_text,
                    key=row_key
                )
            else:
                table.add_row(
                    str(idx),
                    pos_text,
                    emp_text,
                    city_text,
                    date_text,
                    key=row_key
                )
            self.row_index_map.append(job)

        if self.displayed_jobs:
            table.move_cursor(row=0, column=1)
            self.update_preview_bar(self.displayed_jobs[0])
        else:
            self.update_preview_bar(None)

        self.update_status_bar()

    # --- Billentyű eseménykezelők / Akciók ---

    def action_sort_mit(self) -> None:
        """Rendezés Pozíció / Megnevezés alapján (F1)."""
        if self.sort_field == "mit":
            self.sort_descending = not self.sort_descending
        else:
            self.sort_field = "mit"
            self.sort_descending = False
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(f"Rendezve Pozíció szerint ({'Csökkenő' if self.sort_descending else 'Növekvő'})", timeout=2)

    def action_sort_kinel(self) -> None:
        """Rendezés Munkáltató alapján (F2)."""
        if self.sort_field == "kinél":
            self.sort_descending = not self.sort_descending
        else:
            self.sort_field = "kinél"
            self.sort_descending = False
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(f"Rendezve Munkáltató szerint ({'Csökkenő' if self.sort_descending else 'Növekvő'})", timeout=2)

    def action_sort_hol(self) -> None:
        """Rendezés Település alapján (F3)."""
        if self.sort_field == "hol":
            self.sort_descending = not self.sort_descending
        else:
            self.sort_field = "hol"
            self.sort_descending = False
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(f"Rendezve Település szerint ({'Csökkenő' if self.sort_descending else 'Növekvő'})", timeout=2)

    def action_sort_meddig(self) -> None:
        """Rendezés Határidő alapján (F4)."""
        if self.sort_field == "meddig":
            self.sort_descending = not self.sort_descending
        else:
            self.sort_field = "meddig"
            self.sort_descending = False
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(f"Rendezve Határidő szerint ({'Csökkenő' if self.sort_descending else 'Növekvő'})", timeout=2)

    def action_toggle_sort_direction(self) -> None:
        """Rendezési irány megfordítása (F5)."""
        self.sort_descending = not self.sort_descending
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(f"Rendezési irány: {'Csökkenő 🔽' if self.sort_descending else 'Növekvő 🔼'}", timeout=2)

    def action_toggle_density(self) -> None:
        """F8 vagy Z: Sűrű / normál nézetméret váltása."""
        self.compact_mode = not self.compact_mode
        table = self.query_one(DataTable)
        top_bar = self.query_one("#top-bar")

        if self.compact_mode:
            table.cell_padding = 0
            top_bar.add_class("compact")
            mode_name = "Sűrű / Kompakt (több sor fér ki)"
        else:
            table.cell_padding = 1
            top_bar.remove_class("compact")
            mode_name = "Normál nézet"

        self.setup_table_columns()
        self.apply_filter_and_sort()
        self.notify(
            f"Nézet: {mode_name}\n(Tipp: Betűméret állítás: Ctrl + Görgetés vagy Ctrl +/-)",
            title="Nézetméret váltás",
            timeout=3
        )

    def action_cycle_county(self) -> None:
        """F9 vagy M: Váltás a DEFAULT_COUNTIES vármegyék között körforgásban."""
        options = ["ALL"] + list(DEFAULT_COUNTIES.values())
        curr_val = self.county_filter
        curr_idx = options.index(curr_val) if curr_val in options else 0
        next_idx = (curr_idx + 1) % len(options)
        new_val = options[next_idx]
        self.county_filter = new_val

        # Select dropdown szinkronizálása
        select = self.query_one("#county-select", Select)
        select.value = new_val

        self.apply_filter_and_sort()
        disp_name = "Összes vármegye" if new_val == "ALL" else new_val
        self.notify(f"Vármegye szűrő: {disp_name}", title="Vármegye szűrő", timeout=2)

    @on(Select.Changed, "#county-select")
    def on_county_select_changed(self, event: Select.Changed) -> None:
        """Amikor a felhasználó a legördülő menüből választ vármegyét."""
        val = str(event.value) if event.value else "ALL"
        if val != self.county_filter:
            self.county_filter = val
            self.apply_filter_and_sort()

    def action_focus_search(self) -> None:
        """Keresőmező fókuszálása (F7 vagy /)."""
        search_input = self.query_one("#search-input", Input)
        search_input.focus()

    def action_clear_search(self) -> None:
        """Keresés és szűrők törlése, minden találat megjelenítése (ESC)."""
        # 1. Keresőszöveg törlése
        self.search_filter = ""
        try:
            search_input = self.query_one("#search-input", Input)
            search_input.value = ""
        except Exception:
            pass

        # 2. Vármegye szűrő visszaállítása Összes-re
        self.county_filter = "ALL"
        try:
            county_select = self.query_one("#county-select", Select)
            county_select.value = "ALL"
        except Exception:
            pass

        # 3. Szűrés újrafuttatása és táblázatra fókuszálás
        self.apply_filter_and_sort()
        table = self.query_one(DataTable)
        table.focus()
        self.notify("Keresés és szűrők törölve: minden állás megjelenítve.", title="Szűrők törölve", timeout=2)

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Élő keresés a táblázatban beíráskor."""
        self.search_filter = event.value
        self.apply_filter_and_sort()

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """Ha a felhasználó rákattint egy sorra vagy lenyomja az Entert a táblázatban."""
        self.open_selected_job()

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Amikor a kurzor egy sorra lép, az alsó előnézeti sáv azonnal frissül a teljes szöveggel."""
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self.row_index_map):
            job = self.row_index_map[row_idx]
            self.update_preview_bar(job)

    def action_open_link(self) -> None:
        """Enter leütése esetén megnyitja a kijelölt álláshirdetést."""
        self.open_selected_job()

    def open_selected_job(self) -> None:
        """Kijelölt álláshirdetés megnyitása az alapértelmezett böngészőben."""
        table = self.query_one(DataTable)
        cursor_row = table.cursor_row
        if cursor_row is None or cursor_row < 0 or cursor_row >= len(self.row_index_map):
            self.notify("Nincs kijelölt sor a táblázatban!", severity="warning", timeout=2)
            return

        job = self.row_index_map[cursor_row]
        url = job.get("hiv")
        title = job.get("mit", "Állásajánlat")

        if url:
            try:
                webbrowser.open(url)
                self.notify(
                    f"Megnyitás böngészőben:\n{title}\n{url}",
                    title="🌐 Böngésző megnyitva",
                    severity="information",
                    timeout=3
                )
            except Exception as e:
                self.notify(f"Hiba a böngésző megnyitásakor: {e}", severity="error")
        else:
            self.notify("Ehhez a sorhoz nem található érvényes webcím!", severity="error")

    def action_refresh_data(self) -> None:
        """F6: Friss adatok letöltése az internetről a kozig_allas scraperrel."""
        self.fetch_fresh_jobs()

    @work(exclusive=True)
    async def fetch_fresh_jobs(self) -> None:
        """Háttérfolyamat az adatok webről történő letöltéséhez."""
        loading_bar = self.query_one("#loading-bar", Static)
        loading_bar.add_class("visible")
        self.notify("Álláshirdetések letöltése folyamatban...", title="Letöltés", timeout=3)

        scraped_results = []
        try:
            for code, name in DEFAULT_COUNTIES.items():
                loading_bar.update(f"⏳ Letöltés: {name}...")
                county_jobs = await scrape_county_jobs(code, name)
                scraped_results.extend(county_jobs)

            if scraped_results:
                self.all_jobs = scraped_results
                save_to_cache(scraped_results)
                self.setup_table_columns()
                self.apply_filter_and_sort()
                self.notify(
                    f"Sikeresen letöltve {len(scraped_results)} hirdetés!",
                    title="Letöltés befejezve",
                    severity="information",
                    timeout=3
                )
            else:
                self.notify("Nem sikerült új hirdetést letölteni.", severity="warning")
        except Exception as e:
            self.notify(f"Hiba a letöltés során: {e}", severity="error")
        finally:
            loading_bar.remove_class("visible")
            self.update_status_bar()


def main():
    """Fő belépési pont."""
    app = KozigApp()
    app.run()


if __name__ == "__main__":
    main()
