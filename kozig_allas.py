import aiohttp
import asyncio
from selectolax.parser import HTMLParser
import xlsxwriter
from tqdm.asyncio import tqdm
jobUrl = "https://kozszolgallas.ksz.gov.hu/JobAd/Info/"


HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7',
    'Connection': 'keep-alive',
    'DNT': '1',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 OPR/108.0.0.0 (Edition developer)',
    'X-Requested-With': 'XMLHttpRequest',
    'sec-ch-ua': '"Not A(Brand";v="99", "Opera";v="108", "Chromium";v="121"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-gpc': '1',
}


async def get_page(session, page_num, county_code):
    params = {
        'page': f'{page_num}',
        'sort': 'Created DESC',
        'countyCode': county_code
    }
    try:
        async with session.get(
                'https://kozszolgallas.ksz.gov.hu/JobAd/List', params=params) as response:
            if response.status != 200:
                return None, response.status
            text = await response.text()
            return HTMLParser(text), response.status
    except aiohttp.ClientError:
        return None, 0


async def main(county_code, county_name, position):
    master_list = []
    n_page = 1
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        with tqdm(total=None, desc=f"[{county_name}]", position=position, unit=" oldal", leave=False) as pbar:
            while True:
                # 5 oldalas kötegekben kérdezzük le a gyorsaság érdekében
                tasks = [get_page(session, n_page + i, county_code)
                         for i in range(5)]
                results = await asyncio.gather(*tasks)
                pbar.update(len(results))

                stop_processing = False
                for h, status in results:
                    if not h or status != 200:
                        stop_processing = True
                        break

                    divs = h.css('div.jobad')
                    if not divs:
                        stop_processing = True
                        break

                    for job in divs:
                        try:
                            data = {}
                            div_id = job.css_first("div")
                            data["hiv"] = jobUrl + \
                                div_id.attributes["id"] if div_id and "id" in div_id.attributes else ""

                            mit_elem = job.css_first('h4 strong')
                            if mit_elem:
                                mit_text = mit_elem.text()
                                data["mit"] = mit_text.split('\n')[1].strip().upper(
                                ) if '\n' in mit_text else mit_text.strip().upper()
                            else:
                                data["mit"] = ""

                            kinel_elem = job.css_first('h5 strong')
                            data["kinél"] = kinel_elem.text(
                                strip=True) if kinel_elem else ""

                            lis = job.css('li')
                            data["hol"] = lis[2].text(
                                strip=True) if len(lis) > 2 else ""

                            h6s = job.css('div h6')
                            data["meddig"] = h6s[1].text(
                                strip=True) if len(h6s) > 1 else ""

                            if data["hiv"]:
                                master_list.append(data)
                        except Exception:
                            continue

                if stop_processing:
                    break
                n_page += 5
    return master_list


def save_all_to_excel(all_data, filename):
    """Az összes megye adatát egyetlen Excel fájlba menti, külön munkalapokra."""
    try:
        with xlsxwriter.Workbook(filename) as workbook:
            # Formátum a 8-as betűmérethez
            small_font_format = workbook.add_format({'font_size': 8})
            # Fejléc formátum
            header_format = workbook.add_format(
                {'bold': True, 'align': 'center', 'valign': 'vcenter'})

            data_saved = False

            for county_name, master_list in all_data:
                if not master_list:
                    print(
                        f"\nNem található állásajánlat a következőhöz: {county_name}")
                    continue

                data_saved = True
                # Munkalap neve (max 31 karakter lehet Excelben)
                sheet_name = county_name[:31]
                worksheet = workbook.add_worksheet(sheet_name)

                # Adatok rendezése
                master_list.sort(key=lambda item: item.get("mit", ""))

                # Fejlécek előkészítése
                headers = [h.upper() for h in master_list[0].keys()]
                columns = [{'header': h} for h in headers]

                # Adatsorok kiírása
                for row_num, data in enumerate(master_list, 1):
                    worksheet.write_url(
                        row_num, 0, data.get("hiv", ""), string="Megnézem")
                    worksheet.write(row_num, 1, data.get("mit", ""))
                    worksheet.write(
                        row_num, 2, data.get("kinél", ""), small_font_format)
                    worksheet.write(
                        row_num, 3, data.get("hol", ""), small_font_format)
                    worksheet.write(
                        row_num, 4, data.get("meddig", ""), small_font_format)

                # Táblázat hozzáadása (egyedi névvel, szóközök nélkül)
                table_name = f"Table_{sheet_name.replace(' ', '_')}"
                worksheet.add_table(0, 0, len(master_list), len(headers) - 1, {
                    'name': table_name,
                    'columns': columns,
                    'style': 'Table Style Medium 9'
                })

                # Fejlécek formázása
                for col_num, header in enumerate(headers):
                    worksheet.write(0, col_num, header, header_format)

                # Oszlopszélességek beállítása
                worksheet.set_column('A:A', 11)
                worksheet.set_column('B:B', 60)
                worksheet.set_column('C:C', 50)
                worksheet.set_column('D:D', 13)
                worksheet.set_column('E:E', 10)

            if data_saved:
                print(f"\nSikeresen elmentve az adatok a '{filename}' fájlba.")
            else:
                print("\nNem volt menthető adat.")

    except Exception as e:
        print(
            f"\nHiba történt a '{filename}' fájl mentése közben (lehet, hogy nyitva van?): {e}")


async def process_county(code, name, position):
    county_display_name = name.replace('_', ' ').title()
    scraped_data = await main(code, county_display_name, position)
    return county_display_name, scraped_data


if __name__ == "__main__":
    counties_to_scrape = {
        'county.gyormosonsopron': 'gyor_moson_sopron',
        'county.vas': 'vas'
        # 'county.budapest': 'budapest',
        # 'county.pest': 'pest',
    }

    async def run_parallel():
        tasks = [process_county(code, name, idx)
                 for idx, (code, name) in enumerate(counties_to_scrape.items())]
        results = await asyncio.gather(*tasks)
        save_all_to_excel(results, "kozig_allas_osszesito.xlsx")

    asyncio.run(run_parallel())
