"""Build Flat Finder's Mumbai data from the AskRera and India Post CSVs."""

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import median
from urllib.parse import quote

from build_city import WEB, write_csv
from verified import unit_from_verified


DISTRICTS = ('Mumbai City', 'Mumbai Suburban')
PROPERTY_KINDS = {'Residential / Group Housing': 'apartment', 'Mixed': 'mixed', 'Plotted': 'plot'}
ARANYA = 'https://www.piramalrealty.com/projects/residential-projects/flats-in-byculla'
REGISTRY = 'https://askrera.com/data/maharera-registered-projects/'
EMINENCE = 'https://marathon.in/eminence/'
MARQ = 'https://www.lodhagroup.com/projects/residential-property-in-mumbai/lodha-marq'
PLAN_FILE = WEB.parents[2] / 'data' / 'verified' / 'mumbai.json'
PROJECT_FILE = WEB.parents[2] / 'data' / 'mumbai-projects.json'
MARKET_FILE = WEB / 'mumbai-market.json'
MARKET_ALIASES = {'Malad West Dely': 'Malad West', 'Bhandup West': 'Bhandup', 'Bhandup East': 'Bhandup',
                  'Kurla East': 'Kurla', 'Kurla West': 'Kurla', 'Vikhroli East': 'Vikhroli',
                  'Vikhroli West': 'Vikhroli', 'Jogeshwari East': 'Jogeshwari', 'Jogeshwari West': 'Jogeshwari'}
ARANYA_AMENITIES = ['Gym', 'Library', 'Viewing deck']
DEVELOPER_DATA = {
    'P51900018039': dict(bhk=[2, 3, 4, 5], ready=True, source=ARANYA, locality='Byculla',
                        amenities=ARANYA_AMENITIES),
    'P51900020330': dict(bhk=[2, 3], ready=True, source=ARANYA, locality='Byculla',
                        amenities=ARANYA_AMENITIES),
    'P51900051735': dict(bhk=[2, 3, 4], source=ARANYA, locality='Byculla', amenities=ARANYA_AMENITIES),
    'P51900003324': dict(bhk=[2, 3, 4], ready=True, source=ARANYA, locality='Byculla',
                        amenities=ARANYA_AMENITIES),
    'P51800016063': dict(bhk=[1, 2], sizes={1: 445, 2: 569}, source=EMINENCE, locality='Mulund West',
                        coords=[72.955961, 19.175282], location_source='https://bit.ly/2HfPhQr',
                        amenities=['Gym', 'Kids play area', 'Indoor games', 'Podium garden']),
    'P51900046132': dict(bhk=[3, 4, 5], source=MARQ, locality='Tardeo',
                        amenities=['Lap pool', 'Indoor games', 'Kids play area', 'Sports court'],
                        note='The bedroom filter uses the main rectangles printed on the plan. Alcoves and staff rooms are excluded.'),
}
AREA_NAMES = {
    '400001': ('Fort', 'Ballard Estate', 'CST', 'CSMT'), '400005': ('Colaba', 'Cuffe Parade'),
    '400007': ('Grant Road', 'Tardeo'), '400010': ('Mazgaon', 'Mazagon'),
    '400013': ('Lower Parel', 'Delisle Road'), '400014': ('Dadar East', 'Dadar'),
    '400020': ('Marine Lines', 'Churchgate'), '400021': ('Nariman Point',),
    '400027': ('Byculla', 'Rani Baug'), '400028': ('Dadar West', 'Shivaji Park', 'Prabhadevi'),
    '400030': ('Worli', 'Worli Sea Face'), '400033': ('Byculla East', 'Byculla', 'Cotton Green'),
    '400043': ('Govandi', 'Shivaji Nagar'), '400051': ('Bandra East', 'Bandra Kurla Complex', 'BKC'),
    '400052': ('Khar West', 'Khar'), '400053': ('Andheri West', 'Lokhandwala', 'Oshiwara'),
    '400054': ('Santacruz West', 'Santa Cruz West'), '400055': ('Santacruz East', 'Santa Cruz East', 'Vakola'),
    '400056': ('Vile Parle West', 'Vile Parle'), '400057': ('Vile Parle East', 'Vile Parle'),
    '400058': ('Andheri West', 'Andheri Station', 'Amboli'), '400059': ('Marol', 'Andheri East'),
    '400061': ('Versova', 'Andheri West'), '400065': ('Aarey Colony', 'Goregaon East'),
    '400069': ('Andheri East',), '400072': ('Saki Naka', 'Sakinaka', 'Chandivali', 'Powai'),
    '400074': ('Chembur', 'Mahul'), '400075': ('Ghatkopar East', 'Pant Nagar'),
    '400076': ('Powai', 'IIT Bombay'), '400077': ('Ghatkopar East', 'Rajawadi'),
    '400083': ('Vikhroli East', 'Kannamwar Nagar', 'Tagore Nagar'),
    '400084': ('Ghatkopar West', 'Barve Nagar'), '400087': ('Powai', 'NITIE', 'IIM Mumbai'),
    '400088': ('Deonar', 'Govandi', 'Trombay'), '400089': ('Tilak Nagar', 'Chembur'),
    '400093': ('Chakala', 'Andheri East', 'MIDC'), '400095': ('Malad West', 'Malvani'),
    '400096': ('Andheri East', 'SEEPZ'), '400098': ('Kalina', 'Santacruz East'),
    '400099': ('Sahar', 'Andheri East'), '400103': ('Dahisar West', 'Borivali West'),
    '400104': ('Goregaon West', 'Bangur Nagar', 'Motilal Nagar'),
}


def records(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        yield from csv.DictReader(file)


def post_offices(path):
    offices = defaultdict(list)
    for row in records(path):
        if row['StateName'].casefold() != 'maharashtra':
            continue
        if not row['Pincode'].startswith('4000') and not row['Pincode'].startswith('4001'):
            continue
        try:
            lat, lng = float(row['Latitude']), float(row['Longitude'])
        except ValueError:
            continue
        if not (18.87 < lat < 19.30 and 72.77 < lng < 73.01):
            continue
        name = re.sub(r'\s+[SBH]\.?\s*O\.?(?:\s*\(?Mumbai\)?)?$', '', row['OfficeName'], flags=re.I).strip()
        name = re.sub(r'\s*\((East|West)\)', r' \1', name, flags=re.I)
        name = ' '.join(name.split())
        if name.isupper() and len(name) > 4:
            name = name.title()
        offices[row['Pincode']].append((name, lng, lat, row['Delivery'] == 'Delivery'))
    areas = {}
    for pin, entries in offices.items():
        delivery = [row for row in entries if row[3]] or entries
        names = sorted({row[0] for row in entries}, key=str.casefold)
        # Use a real post office nearest the median, rather than a synthetic building point.
        middle = (median(row[1] for row in delivery), median(row[2] for row in delivery))
        name, lng, lat, _ = min(delivery, key=lambda row: (row[1] - middle[0])**2 + (row[2] - middle[1])**2)
        aliases = AREA_NAMES.get(pin, ())
        areas[pin] = {'name': aliases[0] if aliases else name,
                      'aliases': sorted(set(names) | set(aliases), key=str.casefold),
                      'coords': [round(lng, 5), round(lat, 5)]}
    return areas


STATUS = {'ongoing': 'Ongoing', 'ongoing_extended': 'Ongoing (extended)', 'completed': 'Completed',
          'lapsed': 'Lapsed', 'abeyance': 'In abeyance', 'deregistered': 'Deregistered'}


def society(row, area, plans, projects):
    identifier = row['rera_id'].strip().upper()
    name = row['project_name'].strip()
    extra = {**DEVELOPER_DATA.get(identifier, {}), **projects.get(identifier, {})}
    pin = row['pincode'].strip()
    locality = extra.get('locality') or (area or {}).get('name') or row['taluka'].strip() or row['district']
    coords = extra.get('coords') or (area or {}).get('coords') or [None, None]
    precision = 'project' if extra.get('coords') else 'postal' if area else 'unmapped'
    registered_units = row['total_units'].strip()
    verified_layouts = plans.get(identifier, [])
    verified_bhk = {unit['bhk'] for unit in verified_layouts}
    sourced_layouts = extra.get('layouts', [])
    sourced_bhk = {unit['bhk'] for unit in sourced_layouts}
    layouts = verified_layouts + [dict(unit, bedrooms=[], rooms_complete=False, confidence=unit.get('confidence', 'developer'),
                                     source=unit.get('source') or extra['source']) for unit in sourced_layouts] + [
        {'label': f'{bhk} BHK', 'bhk': bhk, 'size_sqft': extra.get('sizes', {}).get(bhk),
         'bedrooms': [], 'rooms_complete': False, 'confidence': 'developer', 'source': extra['source']}
        for bhk in extra.get('bhk', []) if bhk not in verified_bhk and bhk not in sourced_bhk
    ]
    aliases = sorted({row['district'], row['taluka'].strip(), pin, *((area or {}).get('aliases') or []),
                      *extra.get('aliases', [])} - {'', locality})
    if row['district'] == 'Mumbai City':
        aliases.append('South Mumbai')
    kind = extra.get('kind') or PROPERTY_KINDS[row['project_type']]
    project = {
        'id': identifier, 'rera_id': identifier, 'name': name, 'developer': row['promoter_name'].strip(),
        'locality': locality, 'district': row['district'], 'pincode': pin, 'locality_aliases': aliases,
        'lng': coords[0], 'lat': coords[1], 'location_precision': precision,
        'location_source': extra.get('location_source'),
        'status': 'Ready to move' if extra.get('ready') else STATUS.get(row['status']),
        'possession': row['proposed_completion'] or None,
        'units': int(registered_units) if registered_units.isdigit() else None, 'towers': extra.get('towers'), 'acres': extra.get('acres'),
        'price_min': None, 'price_max': None, 'kind': kind,
        'config_text': next(name for name, value in PROPERTY_KINDS.items() if value == kind),
        'amenities': extra.get('amenities', []),
        'unit_types': layouts, 'verified': any(unit['rooms_complete'] for unit in verified_layouts), 'note': extra.get('note'),
        'amen_facts': {}, 'nearby': {}, 'prices': {},
        'rating': None, 'rating_count': None, 'review_count': None,
        'sale_count': None, 'rent_count': None, 'psf_now': None, 'listing_source': None,
        'url': f'https://askrera.com/projects/view/?rera={quote(identifier)}',
        'url_label': 'MahaRERA project record', 'developer_url': extra.get('source'),
        'gmaps': 'https://www.google.com/maps/search/?api=1&query=' + quote(f'{name}, {locality}, Mumbai'),
    }
    if extra.get('osm_feature'):
        project['osm_feature'] = f"https://www.openstreetmap.org/{extra['osm_feature']}"
    if extra.get('images'):
        project['photo_count'] = len(extra['images'])
    return project


def add_market(project, market):
    locality = project['locality']
    key = locality if locality in market else MARKET_ALIASES.get(locality)
    if project['kind'] == 'plot' or not key or key not in market:
        return
    project['market_key'] = key
    if project['location_precision'] != 'project' and not project['developer_url']:
        return  # postal areas give context but an approximate locality must not drive a property budget
    area_data = market[key]
    sizes = defaultdict(set)
    for unit in project['unit_types']:
        if unit.get('carpet_sqft'):
            sizes[unit['bhk']].add(unit['carpet_sqft'])
    for bhk, areas in sizes.items():
        area = median(areas)
        prices = {}
        for mode, metric, step in [('buy', 'resale_psf', 100000), ('rent', 'rent_psf_mo', 1000)]:
            rate = area_data.get(metric)
            if rate:
                prices[f'{mode}_est'] = {'value': round(rate * area / step) * step,
                                         'basis': f"{key} median asking rate × {area:g} sq ft reported carpet area ({area_data['as_of']})"}
        if prices:
            project['prices'][bhk] = prices


def build(registry, pincodes, output=WEB, osm=None):
    areas = post_offices(pincodes)
    plan_data = json.loads(PLAN_FILE.read_text(encoding='utf-8')) if PLAN_FILE.exists() else {}
    plans = {identifier: [unit_from_verified(unit) for unit in units] for identifier, units in plan_data.items()}
    extras = json.loads(PROJECT_FILE.read_text(encoding='utf-8'))
    market = json.loads(MARKET_FILE.read_text(encoding='utf-8'))
    projects = {}
    as_of = ''
    for row in records(registry):
        identifier = row['rera_id'].strip().upper()
        name = row['project_name'].strip()
        if row['district'] not in DISTRICTS or not identifier or not name:
            continue
        if row['project_type'] not in PROPERTY_KINDS and not extras.get(identifier, {}).get('kind'):
            continue
        as_of = max(as_of, row['as_of'])
        projects[identifier] = society(row, areas.get(row['pincode'].strip()), plans, extras)
    if not projects or {p['district'] for p in projects.values()} != set(DISTRICTS):
        raise ValueError('The registry export must contain housing projects in both Mumbai districts.')
    projects = sorted(projects.values(), key=lambda p: (not p['unit_types'], p['name'].casefold(), p['id']))
    config = json.loads((WEB.parents[2] / 'data' / 'cities.json').read_text(encoding='utf-8'))['mumbai']
    shapes = []
    sources = [REGISTRY, 'https://data.gov.in/resource/all-india-pincode-directory-till-last-month']
    if osm:
        from mumbai_osm import OSM_SOURCE, enrich
        shapes = enrich(projects, osm, config['bbox'])
        sources.append(OSM_SOURCE)
    events = [
        {'sid': p['id'], 'step': 'MahaRERA registration', 'finding': f"{p['rera_id']} · {p['locality']}", 'url': p['url']}
        for p in projects
    ]
    details = {}
    for p in projects:
        add_market(p, market['areas'])
        extra = extras.get(p['id'], {})
        detail = {k: extra[k] for k in ('images', 'description', 'description_source', 'developer_quote') if k in extra}
        if detail:
            details[p['id']] = detail
        if p['location_source']:
            events.append({'sid': p['id'], 'step': 'Mapped project', 'finding': p['locality'], 'url': p['location_source']})
        if p['amenities'] and p['developer_url']:
            events.append({'sid': p['id'], 'step': 'Checked developer amenities', 'finding': ', '.join(p['amenities'][:4]),
                           'url': p['developer_url']})
        if p.get('photo_count'):
            events.append({'sid': p['id'], 'step': 'Matched building photo', 'finding': p['locality'],
                           'url': detail['images'][0]['full']})
        for unit in p['unit_types']:
            if unit.get('confidence') == 'primary':
                source = unit['source'] + f"#page={unit['page']}"
                events.append({'sid': p['id'], 'step': f"Read floor plan p. {unit['page']}",
                               'finding': f"{unit['label']} · {', '.join(unit['bedrooms_printed'])}",
                               'url': source, 'verified': True, 'unit': unit['label']})
    on_map = sum(p['lat'] is not None for p in projects)
    data = {
        'city': {'id': 'mumbai', 'name': config['name'], 'center': config['center'], 'zoom': config['zoom']},
        'dataset': 'maharera', 'as_of': as_of,
        'counts': {'listed': len(projects), 'on_map': on_map, 'not_located': len(projects) - on_map,
                   'project_pins': sum(p['location_precision'] == 'project' for p in projects),
                   'project_buildings': sum(f['properties']['role'] == 'bld' for f in shapes),
                   'context_buildings': sum(f['properties']['role'] == 'context' for f in shapes),
                   'verified': sum(p['verified'] for p in projects), 'photos': sum(bool(p.get('photo_count')) for p in projects),
                   'priced': sum(bool(p['prices']) for p in projects),
                   'amenities': sum(bool(p['amenities']) for p in projects)},
        'sources': sources,
        'societies': projects,
        'events': events,
    }
    output.mkdir(parents=True, exist_ok=True)

    def write(name, contents):
        (output / name).write_text(json.dumps(contents, separators=(',', ':')), encoding='utf-8')

    write('mumbai.json', data)
    write('mumbai-details.json', details)
    write('mumbai-shapes.geojson', {'type': 'FeatureCollection', 'features': shapes})
    write_csv(output / 'mumbai-societies.csv', output / 'mumbai-units.csv', projects, source_column='project_source_url')
    cities_file = output / 'cities.json'
    cities = json.loads(cities_file.read_text(encoding='utf-8')) if cities_file.exists() else {}
    cities['mumbai'] = {'name': config['name']}
    write('cities.json', cities)
    print(f"{len(projects)} projects, {data['counts']['project_pins']} project pins, {len(shapes)} map shapes, "
          f"{data['counts']['verified']} with verified plans, as of {as_of}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('registry', type=Path)
    parser.add_argument('pincodes', type=Path)
    parser.add_argument('--osm', type=Path, help='Optional Mumbai OpenStreetMap extract (.osm.pbf)')
    parser.add_argument('--output', type=Path, default=WEB)
    args = parser.parse_args()
    build(args.registry, args.pincodes, args.output, args.osm)
