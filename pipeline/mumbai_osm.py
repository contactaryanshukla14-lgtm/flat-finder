"""Match Mumbai registry entries to named OSM features and measure nearby places."""

import re
from collections import defaultdict

from build_city import Grid
from geo import dist_m
from osm_fetch import height_of, poi_kind

OSM_SOURCE = 'https://www.openstreetmap.org/copyright'
RESIDENTIAL = {'apartments', 'residential', 'house', 'detached', 'bungalow', 'yes'}
HOUSE_TYPES = {'house', 'detached', 'bungalow'}
MAPPED_AREAS = {'residential', 'apartments'}
CONTEXT_LIMIT = 3000


def normalized_name(value):
    name = re.sub(r'[^\w]+', ' ', value.casefold()).strip()
    name = re.sub(r'^(?:proposed redevelopment of|redevelopment of|redevelopment project of)\s+', '', name)
    name = re.sub(r'\b(?:(?:co operative|cooperative)\s+)?(?:housing society|hsg soc)(?: limited| ltd)?$', '', name)
    return re.sub(r'\b(?:chsl?|c h s l?)(?: limited| ltd)?$', '', name).strip()


def explicit_names(tags):
    for key in ('name:en', 'name', 'official_name', 'alt_name', 'addr:housename'):
        for name in tags.get(key, '').split(';'):
            normalized = normalized_name(name)
            if len(normalized) >= 7:
                yield normalized


def candidate_is_residential(tags):
    return tags.get('building') in RESIDENTIAL or tags.get('landuse') == 'residential'


def read_osm(path, bbox, wanted, references):
    import osmium
    from shapely.geometry import mapping
    from shapely.wkb import loads

    south, west, north, east = bbox
    candidates, pois, context, referenced = defaultdict(list), [], [], {}
    wkb = osmium.geom.WKBFactory()
    processor = osmium.FileProcessor(path).with_locations().with_areas()
    for obj in processor:
        tags = obj.tags
        kind = poi_kind(tags)
        building = tags.get('building')
        height = height_of(tags) if obj.is_area() and building in RESIDENTIAL else None
        if height is not None and not 0 < height < 450:
            height = None
        names = sorted(wanted.intersection(explicit_names(tags))) if candidate_is_residential(tags) else []
        context_building = obj.is_area() and (building in MAPPED_AREAS or (height or 0) >= 12)
        if obj.is_node():
            source = f'https://www.openstreetmap.org/node/{obj.id}'
        elif obj.is_area():
            osm_type = 'way' if obj.from_way() else 'relation'
            source = f'https://www.openstreetmap.org/{osm_type}/{obj.orig_id()}'
        else:
            continue
        pinned = source in references
        if pinned and obj.is_area() and building is None and tags.get('building:levels'):
            building = 'yes'
            height = height_of(tags)
            if height is not None and not 0 < height < 450:
                height = None
        if not kind and not names and not context_building and not pinned:
            continue
        outline = None
        if obj.is_node() and obj.location.valid():
            lng, lat = obj.location.lon, obj.location.lat
        elif obj.is_area():
            try:
                outline = loads(wkb.create_multipolygon(obj), hex=True)
                if outline.is_empty or not outline.is_valid:
                    continue
                pt = outline.representative_point()
                lng, lat = pt.x, pt.y
            except (osmium.InvalidLocationError, RuntimeError, ValueError):
                continue
        else:
            continue
        if not (south <= lat <= north and west <= lng <= east):
            continue
        coords = (round(lng, 6), round(lat, 6))
        if kind:
            pois.append({'coords': coords, 'kind': kind, 'name': tags.get('name:en') or tags.get('name'),
                         'source': source})
        if names or context_building or pinned:
            candidate = {'coords': coords, 'pin': tags.get('addr:postcode', '').strip(), 'source': source,
                         'outline': mapping(outline.simplify(0.000005, preserve_topology=True)) if outline else None,
                         'building': building in RESIDENTIAL, 'height': height,
                         'site': tags.get('landuse') == 'residential',
                         'estimated_height': 6 if building in HOUSE_TYPES else 14}
            for name in names:
                candidates[name].append(candidate)
            if context_building:
                context.append(candidate)
            if pinned:
                referenced[source] = candidate
    return candidates, pois, context, referenced


def unique_matches(projects, candidates, referenced):
    proposals = defaultdict(list)
    pinned_sources = {p['osm_feature'] for p in projects if 'osm_feature' in p}
    for project in projects:
        pinned = referenced.get(project.get('osm_feature'))
        if pinned:
            yield project, pinned
            continue
        if project['location_precision'] != 'postal':
            continue
        postal = (project['lng'], project['lat'])
        name = normalized_name(project['name'])
        plausible = [
            c for c in candidates.get(name, [])
            if c['source'] not in pinned_sources and (not c['pin'] or c['pin'] == project['pincode'])
            and dist_m(c['coords'], postal) <= (4000 if c['pin'] else 2000)
        ]
        # Same-name node and footprint can describe one development. Prefer its outline.
        plausible.sort(key=lambda c: (c['outline'] is None, c['source']))
        if plausible and all(dist_m(c['coords'], plausible[0]['coords']) <= 75 for c in plausible):
            proposals[plausible[0]['source']].append((project, plausible[0]))
    for items in proposals.values():
        if len(items) == 1:
            yield items[0]


def nearest_places(project, grid):
    point = (project['lng'], project['lat'])
    nearby = {}
    for item in grid.near(point, r=3):
        distance = dist_m(point, item['coords'])
        kind = item['kind']
        if distance <= 3000 and (kind not in nearby or distance < nearby[kind]['m']):
            nearby[kind] = {'m': round(distance), 'name': item['name'], 'source': item['source']}
    return nearby


def enrich(projects, path, bbox):
    from shapely.geometry import Point, shape

    wanted = {normalized_name(project['name']) for project in projects}
    references = {p['osm_feature'] for p in projects if 'osm_feature' in p}
    candidates, pois, context, referenced = read_osm(path, bbox, wanted, references)
    if not candidates or not pois:
        raise ValueError('The map extract must contain named Mumbai projects and nearby places.')
    shapes = []
    used_buildings = set()
    building_grid = Grid(context, lambda b: b['coords'])
    matches = list(unique_matches(projects, candidates, referenced))
    individual_buildings = {match['source'] for _, match in matches if match['building']}
    for project, match in matches:
        project['lng'], project['lat'] = match['coords']
        project['location_precision'] = 'project'
        project['location_source'] = match['source']
        if match['outline']:
            project['shape'] = 'osm'
            props = {'sid': project['id'], 'role': 'site'}
            shapes.append({'type': 'Feature', 'properties': props, 'geometry': match['outline']})
            buildings = []
            if match['building']:
                buildings = [match]
            elif match['site']:
                site = shape(match['outline'])
                # A named estate can contain several real tower footprints. Never place towers from a postcode.
                buildings = [b for b in building_grid.near(match['coords'], r=1)
                             if b['source'] not in individual_buildings and site.covers(Point(b['coords']))
                             and site.covers(shape(b['outline']))]
            if buildings:
                project['display_height_estimated'] = any(b['height'] is None for b in buildings)
            for building in buildings:
                used_buildings.add(building['source'])
                props = {'sid': project['id'], 'role': 'bld', 'h': building['height'] or building['estimated_height']}
                shapes.append({'type': 'Feature', 'properties': props, 'geometry': building['outline']})

    # Retain a bounded number of genuine footprints for city context before vector tiles show buildings.
    context.sort(key=lambda b: (b['height'] is None, -(b['height'] or 0), b['source']))
    cells = defaultdict(int)
    shown = 0
    for building in context:
        if building['source'] in used_buildings:
            continue
        cell = tuple(int(coord * 100) for coord in building['coords'])
        if cells[cell] >= 28:
            continue
        cells[cell] += 1
        shapes.append({'type': 'Feature', 'properties': {
            'sid': building['source'], 'role': 'context', 'h': building['height'] or building['estimated_height'],
        }, 'geometry': building['outline']})
        shown += 1
        if shown >= CONTEXT_LIMIT:
            break

    grid = Grid(pois, lambda p: p['coords'])
    for project in projects:
        if project['location_precision'] == 'project':
            project['nearby'] = nearest_places(project, grid)
            project['nearby_source'] = OSM_SOURCE
    return shapes
