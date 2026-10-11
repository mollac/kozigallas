# -*- coding: utf-8 -*-
from textual.widgets import DataTable, Footer, Header, Input, Select, Static
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual.app import App, ComposeResult
from textual import events, on, work
from selectolax.lexbor import LexborHTMLParser
from rich.text import Text
"KözigÁllás TUI alkalmazás álláshirdetések böngészéséhez."""
import asyncio
import json
import logging
import ssl
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp
import truststore
truststore.inject_into_ssl()
JOB_BASE_URL = "https://kozszolgallas.ksz.gov.hu/JobAd/Info/"
LIST_URL = "https://kozszolgallas.ksz.gov.hu/JobAd/List"
LOG_FILE = Path(__file__).resolve().with_name("kozig-app.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("kozig-app")

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "DNT": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36 OPR/108.0.0.0"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

DEFAULT_COUNTIES = {
    "county.bacskiskun": "Bács-Kiskun",
    "county.baranya": "Baranya",
    "county.bekes": "Békés",
    "county.borsodabaujzemplen": "Borsod-Abaúj-Zemplén",
    "county.budapest": "Budapest",
    "county.csongrad": "Csongrád-Csanád",
    "county.fejer": "Fejér",
    "county.gyormosonsopron": "Győr-Moson-Sopron",
    "county.hajdubihar": "Hajdú-Bihar",
    "county.heves": "Heves",
    "county.jasznagykunszolnok": "Jász-Nagykun-Szolnok",
    "county.komaromesztergom": "Komárom-Esztergom",
    "county.nograd": "Nógrád",
    "county.pest": "Pest-megye",
    "county.somogy": "Somogy",
    "county.szabolcsszatmarbereg": "Szabolcs-Szatmár-Bereg",
    "county.tolna": "Tolna",
    "county.vas": "Vas",
    "county.veszprem": "Veszprém",
    "county.zala": "Zala",
}

HUNGARIAN_CHAR_MAP = {
    "á": "a\u0100", "é": "e\u0100", "í": "i\u0100", "ó": "o\u0100",
    "ö": "o\u0101", "ő": "o\u0102", "ú": "u\u0100", "ü": "u\u0101",
    "ű": "u\u0102",
}


def hungarian_sort_key(val: Any) -> str:
    if not isinstance(val, str):
        return str(val or "")
    return "".join(HUNGARIAN_CHAR_MAP.get(c, c) for c in val.lower().strip())


def strip_accents(val: Any) -> str:
    if not isinstance(val, str):
        return str(val or "")
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", val.lower())
        if unicodedata.category(c) != "Mn"
    )


def truncate_str(text: Any, max_len: int) -> str:
    value = " ".join(str(text or "").split())
    if max_len <= 1:
        return value[:max_len]
    return value[:max_len - 1] + "…" if len(value) > max_len else value


def matches_county(job_megye: str, target_county: str) -> bool:
    if target_county == "ALL":
        return True
    job_clean = strip_accents((job_megye or "").lower().strip())
    target_clean = strip_accents((target_county or "").lower().strip())
    if target_clean == "pest":
        return job_clean == "pest" or (job_clean.startswith("pest") and "budapest" not in job_clean)
    if target_clean == "budapest":
        return "budapest" in job_clean
    return target_clean in job_clean or job_clean in target_clean


async def fetch_page(
    session: aiohttp.ClientSession,
    page_num: int,
    county_code: Optional[str] = None,
):
    """Lekér egy oldalt; HTTP-, hálózati és parserhibát naplóz."""
    params = {"page": str(page_num), "sort": "Created DESC"}
    if county_code:
        params["countyCode"] = county_code
    try:
        async with session.get(
            LIST_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as response:
            if response.status != 200:
                logger.warning(
                    "HTTP hiba: megye=%s, oldal=%s, státusz=%s, URL=%s",
                    county_code or "összes", page_num, response.status, response.url,
                )
                return None, response.status
            html = await response.text()
            return LexborHTMLParser(html), response.status
    except Exception:
        logger.exception(
            "Lekérési vagy HTML-parser hiba: megye=%s, oldal=%s",
            county_code or "összes", page_num,
        )
        return None, 0


async def scrape_county_jobs(
    county_code: str,
    county_name: str,
    max_pages: int = 50,
) -> List[Dict[str, str]]:
    """Lekéri a megye állásait ötoldalas kötegekben."""
    jobs: List[Dict[str, str]] = []
    page = 1
    # Use the Windows certificate store, including locally trusted enterprise roots.
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        while page <= max_pages:
            results = await asyncio.gather(*[
                fetch_page(session, page + offset, county_code)
                for offset in range(5)
            ])
            stop_processing = False
            for offset, (parser, status) in enumerate(results):
                page_num = page + offset
                if not parser or status != 200:
                    logger.warning(
                        "Oldal feldolgozása leáll: megye=%s, oldal=%s, státusz=%s",
                        county_name, page_num, status,
                    )
                    stop_processing = True
                    break
                try:
                    divs = parser.css("div.jobad")
                except Exception:
                    logger.exception(
                        "HTML selector hiba: megye=%s, oldal=%s",
                        county_name, page_num,
                    )
                    stop_processing = True
                    break
                if not divs:
                    logger.warning(
                        "Nem található div.jobad: megye=%s, oldal=%s; üres oldal vagy HTML-változás",
                        county_name, page_num,
                    )
                    stop_processing = True
                    break
                for job_index, job in enumerate(divs, 1):
                    try:
                        div_id = job.css_first("div")
                        job_id = div_id.attributes.get(
                            "id", "") if div_id else ""
                        link = f"{JOB_BASE_URL}{job_id}" if job_id else ""
                        mit_elem = job.css_first("h4 strong")
                        if mit_elem:
                            raw_mit = mit_elem.text()
                            mit = raw_mit.split("\n")[1].strip().upper(
                            ) if "\n" in raw_mit else raw_mit.strip().upper()
                        else:
                            mit = ""
                        kinel_elem = job.css_first("h5 strong")
                        kinel = kinel_elem.text(
                            strip=True) if kinel_elem else ""
                        lis = job.css("li")
                        hol = lis[2].text(strip=True) if len(lis) > 2 else ""
                        h6s = job.css("div h6")
                        meddig = h6s[1].text(
                            strip=True) if len(h6s) > 1 else ""
                        mit, kinel, hol, meddig = (
                            " ".join(value.split()) for value in (mit, kinel, hol, meddig)
                        )
                        if link and mit:
                            jobs.append({
                                "id": job_id,
                                "hiv": link,
                                "mit": mit,
                                "kinél": kinel,
                                "hol": hol,
                                "meddig": meddig,
                                "megye": county_name,
                            })
                    except Exception:
                        logger.exception(
                            "Hirdetés-parszolási hiba: megye=%s, oldal=%s, kártya=%s",
                            county_name, page_num, job_index,
                        )
            if stop_processing:
                break
            page += 5
    logger.info("Megye lekérdezése kész: %s, hirdetések=%s",
                county_name, len(jobs))
    return jobs


def load_from_json_files() -> List[Dict[str, str]]:
    """Betölti a cache-t vagy az adatfájlt."""
    cwd = Path.cwd()
    cache_file = cwd / "kozig_cache.json"
    data_file = cwd / "data.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as file:
                loaded = json.load(file)
            if isinstance(loaded, list) and loaded:
                return loaded
        except Exception:
            logger.exception(
                "Nem sikerült beolvasni a cache fájlt: %s", cache_file)

    items: List[Dict[str, str]] = []
    if data_file.exists():
        try:
            with open(data_file, "r", encoding="utf-8") as file:
                raw_data = json.load(file)
            raw_list = raw_data if isinstance(
                raw_data, list) else raw_data.get("Items", [])
            for item in raw_list:
                item_id = str(item.get("Id", ""))
                deadline = str(item.get("SubmissionDeadline") or "")
                if "T" in deadline:
                    deadline = deadline.split("T")[0]
                creator = " ".join(
                    str(item.get("CreatorOrganizationName") or "").replace("+", " ").split())
                city = " ".join(
                    str(item.get("CityName") or "").replace("+", " ").split())
                speciality = " ".join(
                    str(item.get("Speciality") or "").replace("+", " ").split())
                group = " ".join(
                    str(item.get("CityGroup") or "").replace("+", " ").split())
                items.append({
                    "id": item_id,
                    "hiv": f"{JOB_BASE_URL}{item_id}" if item_id else "",
                    "mit": speciality.upper(),
                    "kinél": creator,
                    "hol": city,
                    "meddig": deadline,
                    "megye": group,
                })
        except Exception:
            logger.exception(
                "Nem sikerült beolvasni vagy feldolgozni az adatfájlt: %s", data_file)
    return items


def save_to_cache(jobs: List[Dict[str, str]]) -> None:
    """Elmenti az állásokat a cache-be."""
    cache_file = Path.cwd() / "kozig_cache.json"
    try:
        with open(cache_file, "w", encoding="utf-8") as file:
            json.dump(jobs, file, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception(
            "Nem sikerült elmenteni a cache fájlt: %s", cache_file)


class KozigApp(App):
    """KözigÁllás TUI böngésző és kereső."""

    TITLE = "KözigÁllás TUI Böngésző"
    SUB_TITLE = "Álláshirdetések görgetése, rendezése és megnyitása"

    CSS = """
    Screen { background: #0f172a; color: #f8fafc; }
    Header { background: #1e293b; color: #38bdf8; dock: top; height: 3; }
    #top-bar { background: #1e293b; border-bottom: solid #334155; height: 4; padding: 0 1; layout: horizontal; align-vertical: middle; }
    #top-bar.compact { height: 3; }
    #status-info { width: 1fr; color: #94a3b8; content-align: left middle; text-style: bold; }
    #controls-box { width: auto; layout: horizontal; align-vertical: middle; dock: right; }
    #county-select { width: 40; margin-right: 1; height: 3; }
    #search-input { background: #0f172a; border: tall #0284c7; color: #38bdf8; padding: 0 1; height: 3; width: 28; }
    #search-input:focus { border: tall #38bdf8; background: #1e293b; }
    #table-container { height: 1fr; padding: 0 1; }
    DataTable { background: #0f172a; border: solid #1e293b; height: 100%; }
    DataTable > .datatable--header { background: #1e293b; color: #38bdf8; text-style: bold; }
    DataTable > .datatable--cursor { background: #0284c7; color: #ffffff; text-style: bold; }
    DataTable > .datatable--hover { background: #1e293b; }
    #preview-bar { dock: bottom; height: 3; background: #1e293b; border-top: solid #0284c7; padding: 0 1; color: #e2e8f0; }
    #loading-bar { dock: bottom; height: 1; background: #0369a1; color: #ffffff; text-align: center; display: none; }
    #loading-bar.visible { display: block; }
    Footer { background: #1e293b; color: #cbd5e1; dock: bottom; }
    """

    BINDINGS = [
        Binding("enter", "open_link", "Megnyitás", show=True, priority=True),
        Binding("f1", "sort_mit", "Pozíció", show=True), Binding(
            "1", "sort_mit", "Pozíció", show=False),
        Binding("f2", "sort_kinel", "Munkáltató", show=True), Binding(
            "2", "sort_kinel", "Munkáltató", show=False),
        Binding("f3", "sort_hol", "Település", show=True), Binding(
            "3", "sort_hol", "Település", show=False),
        Binding("f4", "sort_meddig", "Határidő", show=True), Binding(
            "4", "sort_meddig", "Határidő", show=False),
        Binding("f5", "toggle_sort_direction", "Irány", show=True), Binding(
            "5", "toggle_sort_direction", "Irány", show=False),
        Binding("f6", "refresh_data", "Frissítés", show=True), Binding(
            "r", "refresh_data", "Frissítés", show=False),
        Binding("f7", "focus_search", "Keresés", show=True), Binding(
            "slash", "focus_search", "Keresés", show=False),
        Binding("f8", "toggle_density", "Nézetméret", show=True), Binding(
            "z", "toggle_density", "Nézetméret", show=False),
        Binding("f9", "cycle_county", "Megye", show=True), Binding(
            "m", "cycle_county", "Megye", show=False),
        Binding("escape", "clear_search", "Szűrők törlése",
                show=True, priority=True),
        Binding("q", "quit", "Kilépés", show=True),
    ]

    COLUMN_LABELS = {
        "col_id": "#", "col_mit": "Pozíció / Megnevezés", "col_kinel": "Munkáltató",
        "col_hol": "Település", "col_meddig": "Határidő", "col_megye": "Megye / Térség",
    }
    SORT_FIELD_TO_COL = {
        "mit": "col_mit", "kinél": "col_kinel", "hol": "col_hol",
        "meddig": "col_meddig", "megye": "col_megye",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.all_jobs: List[Dict[str, str]] = []
        self.displayed_jobs: List[Dict[str, str]] = []
        self.sort_field = "mit"
        self.sort_descending = False
        self.search_filter = ""
        self.county_filter = "ALL"
        self.row_index_map: List[Dict[str, str]] = []
        self.compact_mode = False
        self.current_col_widths: Dict[str, Any] = {}
        self._last_width = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        options = [("Összes megye", "ALL")] + [(name, name)
                                               for name in DEFAULT_COUNTIES.values()]
        with Horizontal(id="top-bar"):
            yield Static("Betöltés...", id="status-info")
            with Horizontal(id="controls-box"):
                yield Select(options, value="ALL", allow_blank=False, id="county-select")
                yield Input(placeholder="🔍 Keresés... (ESC = táblázat)", id="search-input")
        with Vertical(id="table-container"):
            yield DataTable(id="jobs-table", cursor_type="row", zebra_stripes=True)
        yield Static("Válassz ki egy állást a listából az előnézethez.", id="preview-bar")
        yield Static("⏳ Friss adatok letöltése folyamatban...", id="loading-bar")
        yield Footer()

    async def on_mount(self) -> None:
        self.setup_table_columns()
        local_data = load_from_json_files()
        if local_data:
            self.all_jobs = local_data
            self.apply_filter_and_sort()
        else:
            self.query_one(
                "#status-info", Static).update("Nincs helyi adat. Letöltés...")
            self.fetch_fresh_jobs()
        self.query_one(DataTable).focus()

    def compute_column_widths(self, total_width: Optional[int] = None) -> Dict[str, Any]:
        if total_width is None or total_width <= 0:
            total_width = self.size.width or 100
        net_w = max(45, total_width - (4 if self.compact_mode else 6))
        show_county = net_w >= 92
        col_id = 4
        col_date = 10 if net_w < 110 else 11
        col_city = 12 if net_w < 110 else 15
        col_county = 14 if show_county and net_w < 120 else (
            18 if show_county else 0)
        fixed_total = col_id + col_date + col_city + \
            col_county + (8 if not self.compact_mode else 5)
        remaining = max(24, net_w - fixed_total)
        col_pos = max(14, int(remaining * 0.55) + 8)
        col_emp = max(10, remaining - col_pos)
        return {"show_megye": show_county, "id": col_id, "mit": col_pos, "kinél": col_emp,
                "hol": col_city, "meddig": col_date, "megye": col_county, "net_w": net_w}

    def setup_table_columns(self, total_width: Optional[int] = None) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        widths = self.compute_column_widths(total_width)
        self.current_col_widths = widths
        table.add_column("#", width=widths["id"], key="col_id")
        table.add_column("Pozíció / Megnevezés",
                         width=widths["mit"], key="col_mit")
        table.add_column("Munkáltató", width=widths["kinél"], key="col_kinel")
        table.add_column("Település", width=widths["hol"], key="col_hol")
        table.add_column("Határidő", width=widths["meddig"], key="col_meddig")
        if widths["show_megye"]:
            table.add_column("Megye / Térség",
                             width=widths["megye"], key="col_megye")
        self.highlight_sorted_column()

    def highlight_sorted_column(self) -> None:
        table = self.query_one(DataTable)
        active_key = self.SORT_FIELD_TO_COL.get(self.sort_field)
        arrow = " ▼" if self.sort_descending else " ▲"
        for key, base_label in self.COLUMN_LABELS.items():
            try:
                column = table.columns.get(key)
                if column is not None:
                    label = Text(f"{base_label}{arrow}" if key ==
                                 active_key else base_label)
                    if key == active_key:
                        label.stylize("bold #f97316")
                    column.label = label
            except Exception:
                logger.exception(
                    "Oszlop fejlécének frissítése sikertelen: %s", key)
        table.refresh()

    def on_resize(self, event: events.Resize) -> None:
        width = event.size.width
        if abs(self._last_width - width) >= 2:
            self._last_width = width
            self.setup_table_columns(width)
            self.apply_filter_and_sort()

    def update_status_bar(self) -> None:
        labels = {"mit": "Pozíció", "kinél": "Munkáltató",
                  "hol": "Település", "meddig": "Határidő", "megye": "Megye"}
        direction = "🔽 Csökkenő" if self.sort_descending else "🔼 Növekvő"
        count = f"Találatok: {len(self.displayed_jobs)} / {len(self.all_jobs)}"
        county = "Összes" if self.county_filter == "ALL" else self.county_filter
        search = f" | Szűrő: '{self.search_filter}'" if self.search_filter else ""
        density = " | [bold yellow]Sűrű nézet[/]" if self.compact_mode else ""
        self.query_one("#status-info", Static).update(
            f"📋 {count} | Megye: [bold green]{county}[/]{search} | "
            f"Rendezés: [bold cyan]{labels.get(self.sort_field, self.sort_field)}[/] ({direction}){density}"
        )

    def update_preview_bar(self, job: Optional[Dict[str, str]] = None) -> None:
        bar = self.query_one("#preview-bar", Static)
        if not job:
            bar.update(
                "🔍 [dim]Válassz ki egy sort a nyilakkal, nyomj Enter-t a webes megnyitáshoz[/]")
            return
        first = f"📌 [bold cyan]{job.get('mit', '-')}[/]"
        second = (
            f"🏛️ [bold white]{job.get('kinél', '-')}[/]  |  📍 {job.get('hol', '-')} "
            f"({job.get('megye', '-')})  |  📅 Határidő: [bold yellow]{job.get('meddig', '-')}[/]  |  "
            "[bold green]⏎ ENTER: Megnyitás[/]"
        )
        bar.update(f"{first}\n{second}")

    def apply_filter_and_sort(self) -> None:
        table = self.query_one(DataTable)
        if self.county_filter != "ALL":
            filtered = [job for job in self.all_jobs if matches_county(
                job.get("megye", ""), self.county_filter)]
        else:
            filtered = list(self.all_jobs)
        query = strip_accents(self.search_filter.strip())
        if query:
            self.displayed_jobs = [job for job in filtered if query in strip_accents(
                job.get(self.sort_field, ""))]
        else:
            self.displayed_jobs = filtered
        self.displayed_jobs.sort(key=lambda job: hungarian_sort_key(
            job.get(self.sort_field, "")), reverse=self.sort_descending)
        table.clear()
        self.row_index_map = []
        widths = self.current_col_widths or self.compute_column_widths(
            self.size.width)
        active_key = self.SORT_FIELD_TO_COL.get(self.sort_field)
        col_keys = ["col_id", "col_mit", "col_kinel", "col_hol", "col_meddig"]
        if widths.get("show_megye", True):
            col_keys.append("col_megye")
        for idx, job in enumerate(self.displayed_jobs, 1):
            raw = {
                "col_id": str(idx), "col_mit": truncate_str(job.get("mit", ""), widths["mit"]),
                "col_kinel": truncate_str(job.get("kinél", ""), widths["kinél"]),
                "col_hol": truncate_str(job.get("hol", ""), widths["hol"]),
                "col_meddig": truncate_str(job.get("meddig", ""), widths["meddig"]),
            }
            if widths.get("show_megye", True):
                raw["col_megye"] = truncate_str(
                    job.get("megye", ""), widths["megye"])
            cells = []
            for key in col_keys:
                value = raw.get(key, "")
                if key == active_key:
                    cell = Text(value)
                    cell.stylize("#f97316")
                    cells.append(cell)
                else:
                    cells.append(value)
            table.add_row(*cells, key=f"job_{idx}")
            self.row_index_map.append(job)
        if self.displayed_jobs:
            table.move_cursor(row=0, column=1)
            self.update_preview_bar(self.displayed_jobs[0])
        else:
            self.update_preview_bar(None)
        self.update_status_bar()

    def action_sort_mit(self) -> None:
        if self.sort_field == "mit":
            self.sort_descending = not self.sort_descending
        else:
            self.sort_field, self.sort_descending = "mit", False
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(
            f"Rendezve Pozíció szerint ({'Csökkenő' if self.sort_descending else 'Növekvő'})", timeout=2)

    def action_sort_kinel(self) -> None:
        if self.sort_field == "kinél":
            self.sort_descending = not self.sort_descending
        else:
            self.sort_field, self.sort_descending = "kinél", False
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(
            f"Rendezve Munkáltató szerint ({'Csökkenő' if self.sort_descending else 'Növekvő'})", timeout=2)

    def action_sort_hol(self) -> None:
        if self.sort_field == "hol":
            self.sort_descending = not self.sort_descending
        else:
            self.sort_field, self.sort_descending = "hol", False
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(
            f"Rendezve Település szerint ({'Csökkenő' if self.sort_descending else 'Növekvő'})", timeout=2)

    def action_sort_meddig(self) -> None:
        if self.sort_field == "meddig":
            self.sort_descending = not self.sort_descending
        else:
            self.sort_field, self.sort_descending = "meddig", False
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(
            f"Rendezve Határidő szerint ({'Csökkenő' if self.sort_descending else 'Növekvő'})", timeout=2)

    def action_toggle_sort_direction(self) -> None:
        self.sort_descending = not self.sort_descending
        self.apply_filter_and_sort()
        self.highlight_sorted_column()
        self.notify(
            f"Rendezési irány: {'Csökkenő 🔽' if self.sort_descending else 'Növekvő 🔼'}", timeout=2)

    def action_toggle_density(self) -> None:
        self.compact_mode = not self.compact_mode
        table = self.query_one(DataTable)
        top_bar = self.query_one("#top-bar")
        if self.compact_mode:
            table.cell_padding = 0
            top_bar.add_class("compact")
            mode = "Sűrű / Kompakt (több sor fér ki)"
        else:
            table.cell_padding = 1
            top_bar.remove_class("compact")
            mode = "Normál nézet"
        self.setup_table_columns()
        self.apply_filter_and_sort()
        self.notify(f"Nézet: {mode}\n(Tipp: Betűméret állítás: Ctrl + Görgetés vagy Ctrl +/-)",
                    title="Nézetméret váltás", timeout=3)

    def action_cycle_county(self) -> None:
        options = ["ALL"] + list(DEFAULT_COUNTIES.values())
        index = options.index(
            self.county_filter) if self.county_filter in options else 0
        self.county_filter = options[(index + 1) % len(options)]
        self.query_one("#county-select", Select).value = self.county_filter
        self.apply_filter_and_sort()
        name = "Összes megye" if self.county_filter == "ALL" else self.county_filter
        self.notify(f"Megye szűrő: {name}",
                    title="Megye szűrő", timeout=2)

    @on(Select.Changed, "#county-select")
    def on_county_select_changed(self, event: Select.Changed) -> None:
        value = str(event.value) if event.value else "ALL"
        if value != self.county_filter:
            self.county_filter = value
            self.apply_filter_and_sort()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_clear_search(self) -> None:
        self.search_filter = ""
        try:
            self.query_one("#search-input", Input).value = ""
            self.county_filter = "ALL"
            self.query_one("#county-select", Select).value = "ALL"
        except Exception:
            logger.exception("A szűrők törlése közben hiba történt")
        self.apply_filter_and_sort()
        self.query_one(DataTable).focus()
        self.notify("Keresés és szűrők törölve: minden állás megjelenítve.",
                    title="Szűrők törölve", timeout=2)

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        self.search_filter = event.value
        self.apply_filter_and_sort()

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        self.open_selected_job()

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        index = event.cursor_row
        if 0 <= index < len(self.row_index_map):
            self.update_preview_bar(self.row_index_map[index])

    def action_open_link(self) -> None:
        self.open_selected_job()

    def open_selected_job(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self.row_index_map):
            self.notify("Nincs kijelölt sor a táblázatban!",
                        severity="warning", timeout=2)
            return
        job = self.row_index_map[row]
        url = job.get("hiv")
        if not url:
            self.notify(
                "Ehhez a sorhoz nem található érvényes webcím!", severity="error")
            return
        try:
            webbrowser.open(url)
            self.notify(
                f"Megnyitás böngészőben:\n{job.get('mit', 'Állásajánlat')}\n{url}", title="🌐 Böngésző megnyitva", timeout=3)
        except Exception:
            logger.exception("Nem sikerült megnyitni a hirdetést: %s", url)
            self.notify("Hiba a böngésző megnyitásakor.", severity="error")

    def action_refresh_data(self) -> None:
        self.fetch_fresh_jobs()

    @work(exclusive=True)
    async def fetch_fresh_jobs(self) -> None:
        loading_bar = self.query_one("#loading-bar", Static)
        loading_bar.add_class("visible")
        self.notify("Álláshirdetések letöltése megkezdődött...",
                    title="Letöltés", timeout=3)
        scraped_results: List[Dict[str, str]] = []
        try:
            total_counties = len(DEFAULT_COUNTIES)
            for index, (code, name) in enumerate(DEFAULT_COUNTIES.items(), 1):
                message = f"⏳ Letöltés ({index}/{total_counties}): {name}..."
                loading_bar.update(message)
                self.notify(message, title="Folyamatban", timeout=2)
                county_jobs = await scrape_county_jobs(code, name)
                logger.info(
                    "Lekérdezés kész: megye=%s, hirdetések=%s", name, len(county_jobs))
                scraped_results.extend(county_jobs)
            if scraped_results:
                self.all_jobs = scraped_results
                save_to_cache(scraped_results)
                self.setup_table_columns()
                self.apply_filter_and_sort()
                self.notify(f"Sikeresen letöltve {len(scraped_results)} hirdetés!",
                            title="Letöltés befejezve", severity="information", timeout=3)
            else:
                logger.error(
                    "A frissítés üres eredménnyel zárult; a korábbi adatokat megtartottam.")
                self.notify(
                    f"Nem sikerült új hirdetést letölteni. Részletek: {LOG_FILE.name}", severity="warning")
        except Exception as error:
            logger.exception("Váratlan hiba a frissítés során")
            self.notify(
                f"Hiba a letöltés során: {error} (részletek: {LOG_FILE.name})", severity="error")
        finally:
            loading_bar.remove_class("visible")
            self.update_status_bar()


def main() -> None:
    KozigApp().run()


if __name__ == "__main__":
    main()
