"""Read map coordinates from MahaRERA's public project search cards."""

import argparse
import csv
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


SEARCH_URL = 'https://www.maharera.maharashtra.gov.in/projects-search-result'
DISTRICTS = {'mumbai city': 'Mumbai City', 'mumbai suburban': 'Mumbai Suburban'}
USER_AGENT = 'MumbaiFlatFinder/1.0 (https://github.com/contactaryanshukla14-lgtm/flat-finder)'
CARD_SELECTOR = '.row.shadow.p-3.mb-5.bg-body.rounded'


def pin_pages(registry, output, cached):
    with registry.open(encoding='utf-8-sig', newline='') as stream:
        pins = Counter(row['pincode'] for row in csv.DictReader(stream)
                       if row['district'].casefold() in DISTRICTS and re.fullmatch(r'400[01]\d\d', row['pincode']))
    if not pins:
        raise ValueError('No Mumbai registrations in the registry input.')
    lock = Lock()
    next_request = 0.0

    def read_page(pin, page):
        nonlocal next_request
        address = f"{SEARCH_URL}?{urlencode({'project_state': '27', 'project_location': pin, 'page': page})}"
        for attempt in range(2):
            with lock:
                sleep(max(0, next_request - monotonic()))
                next_request = monotonic() + 0.4
            try:
                request = Request(address, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html'})
                with urlopen(request, timeout=14) as response:
                    page_data = response.read()
                return parse_cards(page_data, pin)
            except HTTPError as error:
                if error.code not in (500, 502, 503, 504) or attempt:
                    raise
            except (URLError, TimeoutError):
                if attempt:
                    raise
            sleep(1)

    seen = set(cached['pages'])
    rows = cached['projects']
    tasks = [(pin, 1) for pin, _ in pins.most_common()]

    def save():
        temporary = output.with_suffix('.tmp')
        temporary.write_text(json.dumps({'pages': sorted(seen), 'projects': rows}, separators=(',', ':')), encoding='utf-8')
        temporary.replace(output)

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            for pin, first_page in tasks:
                key = f'{pin}:{first_page}'
                if key not in seen:
                    first, pages = read_page(pin, first_page)
                    rows.update(first)
                    seen.add(key)
                    seen.add(f'{pin}:pages:{pages}')
                    save()
                else:
                    pages = next((int(k.split(':')[-1]) for k in seen if k.startswith(f'{pin}:pages:')), 1)

                pending = [p for p in range(2, pages + 1) if f'{pin}:{p}' not in seen]
                for page, (matches, _) in zip(pending, pool.map(lambda p: read_page(pin, p), pending)):
                    rows.update(matches)
                    seen.add(f'{pin}:{page}')
                save()
                print(f'{pin} · {len(rows)} project coordinates across {len(seen)} cached page records', flush=True)
    finally:
        save()


def parse_cards(raw, pin):
    soup = BeautifulSoup(raw, 'html.parser')
    pages = soup.select_one('.pagesCount')
    cards = soup.select(CARD_SELECTOR)
    if not cards:
        if soup.select_one('#edit-project-state') and re.search(r'no (?:projects|record|results|data)', soup.get_text(' ', strip=True), re.I):
            return {}, 1
        raise ValueError(f'Could not recognize MahaRERA search results for PIN {pin}.')
    result = {}
    for card in cards:
        heading = card.select_one('p.p-0')
        location = card.select_one('ul.listingList a[href*="maps/search"]')
        if not heading or not location:
            continue
        registration = heading.get_text(strip=True).removeprefix('#').strip().upper()
        if not re.fullmatch(r'P(?:R)?\d{11,14}', registration):
            continue
        fields = {title.get_text(' ', strip=True): value.get_text(' ', strip=True)
                  for title in card.select('div.greyColor') if (value := title.find_next_sibling('p')) is not None}
        district = DISTRICTS.get(fields.get('District', '').casefold())
        if not district or fields.get('Pincode') != pin:
            continue
        try:
            lat, lng = (float(side) for side in parse_qs(urlparse(location['href']).query)['query'][0].split(','))
        except (KeyError, ValueError):
            continue
        if not 18.88 < lat < 19.32 or not 72.77 < lng < 73.04:
            continue
        result[registration] = {'coords': [round(lng, 6), round(lat, 6)], 'district': district,
                                'pin': pin, 'modified': fields.get('Last Modified', '')}
    total_pages = int(pages.get('data-current-data', '1')) if pages else 1
    return result, total_pages


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('registry', type=Path)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--export', type=Path, help='Write the coordinate records without the pagination cache.')
    args = parser.parse_args()
    cache = json.loads(args.snapshot.read_text(encoding='utf-8')) if args.snapshot.exists() else {'pages': [], 'projects': {}}
    pin_pages(args.registry, args.snapshot, cache)
    if args.export is not None:
        args.export.write_text(json.dumps(cache['projects'], sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')
