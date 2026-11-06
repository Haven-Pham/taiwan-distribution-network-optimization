"""Builds one demand point per township from official population and boundary files, and a road matrix from OSRM."""
import io
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
USER_AGENT = "taiwan-distribution-network-optimization (portfolio project)"
VILLAGE_POPULATION = 77132
VILLAGE_BOUNDARIES = 7438
TOWN_BOUNDARIES = 7441
OSRM_TABLE = "https://router.project-osrm.org/table/v1/driving/"
OFFSHORE_COUNTIES = {"澎湖縣", "金門縣", "連江縣"}
OFFSHORE_TOWNS = {"蘭嶼鄉", "綠島鄉", "琉球鄉"}


COUNTY_EN = {"臺北市": "Taipei", "新北市": "New Taipei", "桃園市": "Taoyuan", "臺中市": "Taichung", "臺南市": "Tainan", "高雄市": "Kaohsiung", "基隆市": "Keelung", "新竹市": "Hsinchu City",
             "嘉義市": "Chiayi City", "新竹縣": "Hsinchu County", "苗栗縣": "Miaoli", "彰化縣": "Changhua", "南投縣": "Nantou", "雲林縣": "Yunlin", "嘉義縣": "Chiayi County", "屏東縣": "Pingtung",
             "宜蘭縣": "Yilan", "花蓮縣": "Hualien", "臺東縣": "Taitung"}


def english_label(county, town_en):
    """A readable English name. Several cities share names such as "East District", so those carry their city."""
    if town_en.split()[0] in ("East", "West", "North", "South", "Central", "Xinyi", "Zhongzheng", "Zhongshan", "Da'an", "Datong", "Daan"):
        return f"{COUNTY_EN[county]} {town_en}"
    return town_en


def _get(url, timeout=300):
    parts = urllib.parse.urlsplit(url)
    url = urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path)))
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        return response.read()


def _distribution(dataset):
    return json.loads(_get(f"https://data.gov.tw/api/v2/rest/dataset/{dataset}"))["result"]["distribution"]


def latest_resource(distribution):
    """The newest monthly file, by the ROC year-month that opens its description. The portal does not sort them."""
    dated = []
    for item in distribution:
        match = re.match(r"\s*(\d{5})", item.get("resourceDescription") or "")
        if match:
            dated.append((int(match.group(1)), item))
    if not dated:
        raise ValueError("No dated resource found")
    return max(dated, key=lambda pair: pair[0])


def download_raw(raw_dir=RAW_DIR):
    """Download the village population and the boundary files, and return the population month."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    month, item = latest_resource(_distribution(VILLAGE_POPULATION))
    (raw_dir / "village_population.csv").write_bytes(_get(item["resourceDownloadUrl"]))
    for dataset, folder in ((VILLAGE_BOUNDARIES, "village"), (TOWN_BOUNDARIES, "town")):
        url = next(d["resourceDownloadUrl"] for d in _distribution(dataset) if d.get("resourceFormat") == "SHP")
        with zipfile.ZipFile(io.BytesIO(_get(url))) as bundle:
            bundle.extractall(raw_dir / folder)
    return month


def ring_area_centroid(points):
    """Signed area and centroid of a closed ring, by the shoelace formula."""
    xy = np.asarray(points, dtype=float)
    x, y = xy[:, 0], xy[:, 1]
    cross = x[:-1] * y[1:] - x[1:] * y[:-1]
    area = cross.sum() / 2
    if area == 0:
        return 0.0, float(x.mean()), float(y.mean())
    return float(area), float(((x[:-1] + x[1:]) * cross).sum() / (6 * area)), float(((y[:-1] + y[1:]) * cross).sum() / (6 * area))


def largest_part_centroid(shape):
    """Centroid of the largest part of a polygon, so a small islet cannot pull the point out to sea."""
    bounds = list(shape.parts) + [len(shape.points)]
    best = (0.0, None, None)
    for start, end in zip(bounds[:-1], bounds[1:]):
        area, x, y = ring_area_centroid(shape.points[start:end])
        if abs(area) >= abs(best[0]):
            best = (area, x, y)
    return best[1], best[2]


def representative_point(lon, lat, population):
    """Index of the village nearest the population-weighted centre.

    Using a real village, not the weighted average, keeps the point somewhere people live even when a township
    has two valleys with a mountain between them.
    """
    weights = np.asarray(population, dtype=float)
    if weights.sum() <= 0:
        weights = np.ones_like(weights)
    lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    centre_lon, centre_lat = np.average(lon, weights=weights), np.average(lat, weights=weights)
    squared = ((lon - centre_lon) * np.cos(np.radians(centre_lat))) ** 2 + (lat - centre_lat) ** 2
    return int(np.argmin(squared))


def is_offshore(county, town):
    return county in OFFSHORE_COUNTIES or town in OFFSHORE_TOWNS


def _read_shapes(folder, prefix):
    import shapefile

    # Each archive also holds a one-record correction file, so the main file must be chosen by name.
    path = next(p for p in folder.rglob("*.shp") if p.name.upper().startswith(prefix))
    reader = shapefile.Reader(str(path), encoding="utf-8")
    return reader, path.name


def build_townships(raw_dir=RAW_DIR, log=print):
    """One row per mainland township: official names, population and a representative point."""
    raw_dir = Path(raw_dir)
    population = pd.read_csv(raw_dir / "village_population.csv", encoding="utf-8-sig", dtype=str, usecols=range(6))
    population.columns = ["month", "code", "area", "village", "households", "population"]
    population = population[population["code"].str.fullmatch(r"\d{11}", na=False)].copy()
    population["population"] = population["population"].astype(int)
    population["town_code"] = population["code"].str[:8]

    villages, village_file = _read_shapes(raw_dir / "village", "VILLAGE_NLSC")
    rows = []
    for record, shape in zip(villages.iterRecords(), villages.iterShapes()):
        lon, lat = largest_part_centroid(shape)
        rows.append({"code": record["VILLCODE"], "town_code": record["TOWNCODE"], "lon": lon, "lat": lat})
    points = pd.DataFrame(rows)

    towns, town_file = _read_shapes(raw_dir / "town", "TOWN_MOI")
    names = pd.DataFrame([{"town_code": r["TOWNCODE"], "county": r["COUNTYNAME"], "town": r["TOWNNAME"], "town_en": r["TOWNENG"]} for r in towns.iterRecords()])
    log(f"{town_file}: {len(names)} townships; {village_file}: {len(points)} villages; population month {population['month'].iloc[0]}")

    joined = points.merge(population[["code", "population"]], on="code", how="left")
    matched = joined["population"].notna()
    log(f"Villages with a population match: {matched.mean() * 100:.1f}%; population matched: {joined['population'].sum() / population['population'].sum() * 100:.1f}%")
    joined["population"] = joined["population"].fillna(0)

    out = []
    for town_code, group in joined.groupby("town_code"):
        index = representative_point(group["lon"], group["lat"], group["population"])
        out.append({"town_code": town_code, "lon": round(float(group["lon"].iloc[index]), 5), "lat": round(float(group["lat"].iloc[index]), 5)})
    totals = population.groupby("town_code")["population"].sum().rename("population")
    table = names.merge(pd.DataFrame(out), on="town_code", how="left").merge(totals, on="town_code", how="left")
    table["population"] = table["population"].fillna(0).astype(int)
    offshore = [is_offshore(c, t) for c, t in zip(table["county"], table["town"])]
    log(f"Offshore townships excluded: {sum(offshore)}, with {table.loc[offshore, 'population'].sum() / table['population'].sum() * 100:.1f}% of population")
    table = table[~np.array(offshore)]
    empty = table["population"] == 0
    if empty.any():
        log(f"Townships without population dropped: {table.loc[empty, 'town'].tolist()}")
    return table[~empty].sort_values("town_code").reset_index(drop=True), joined


def _osrm_json(url, pause=1.1):
    """One request to the public server, at its limit of one a second, waiting longer after a failure."""
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=120) as response:
                payload = json.loads(response.read())
            if payload.get("code") != "Ok":
                raise ValueError(payload.get("code"))
            time.sleep(pause)
            return payload
        except (OSError, ValueError):
            if attempt == 4:
                raise
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("unreachable")


def snap_distances(lons, lats, chunk=90):
    """Metres from each point to the nearest road, using one table request per block."""
    coordinates = [f"{lon:.5f},{lat:.5f}" for lon, lat in zip(lons, lats)]
    out = []
    for start in range(0, len(coordinates), chunk):
        block = coordinates[start:start + chunk]
        query = urllib.parse.urlencode({"destinations": "0", "annotations": "distance"})
        payload = _osrm_json(OSRM_TABLE + ";".join(block) + "?" + query)
        out.extend(source.get("distance", 0.0) for source in payload["sources"])
    return np.array(out)


def village_access(group, reference, chunk=90):
    """Metres to the nearest road, and km/h from a reference township, for each village in a group."""
    origin = f"{reference['lon']:.5f},{reference['lat']:.5f}"
    snap, speed = [], []
    for start in range(0, len(group), chunk):
        block = group.iloc[start:start + chunk]
        points = ";".join(f"{lon:.5f},{lat:.5f}" for lon, lat in zip(block["lon"], block["lat"]))
        query = urllib.parse.urlencode({"sources": "0", "annotations": "distance,duration"})
        payload = _osrm_json(OSRM_TABLE + origin + ";" + points + "?" + query)
        distance, duration = np.array(payload["distances"][0][1:], dtype=float), np.array(payload["durations"][0][1:], dtype=float)
        snap.extend(point.get("distance", 0.0) for point in payload["destinations"][1:])
        speed.extend(distance / 1000 / np.maximum(duration / 3600, 1e-6))
    return np.array(snap), np.array(speed)


def accessible_village(lon, lat, population, snap, speed, max_snap=500, min_speed=20):
    """Index of the most central village that is close to a usable road.

    A village polygon's centre can lie deep in forest. Routing to it then follows a track and gives absurd times, and
    being near a road is not enough, because that road may itself be a track. So a village qualifies only if it is near
    a road and the trip from the county's largest township is at a plausible speed. If none qualifies, the fastest
    village near a road is used, then the fastest of all.
    """
    snap, speed = np.asarray(snap, dtype=float), np.asarray(speed, dtype=float)
    near_road = np.flatnonzero((snap <= max_snap) & (speed >= min_speed))
    if len(near_road) == 0:
        close = np.flatnonzero(snap <= max_snap)
        options = close if len(close) else np.arange(len(snap))
        return int(options[np.argmax(speed[options])])
    lon, lat, population = (np.asarray(v, dtype=float) for v in (lon, lat, population))
    weights = population if population.sum() > 0 else np.ones_like(population)
    centre_lon, centre_lat = np.average(lon, weights=weights), np.average(lat, weights=weights)
    squared = ((lon[near_road] - centre_lon) * np.cos(np.radians(centre_lat))) ** 2 + (lat[near_road] - centre_lat) ** 2
    return int(near_road[np.argmin(squared)])


def refine_points(table, villages, threshold=1000, log=print):
    """Move each township whose point is far from a road to an accessible village, by one rule for all townships."""
    table = table.copy()
    snap = snap_distances(table["lon"], table["lat"])
    flagged = np.flatnonzero(snap > threshold)
    moved = []
    for index in flagged:
        group = villages[villages["town_code"] == table["town_code"].iloc[index]].reset_index(drop=True)
        county = table[table["county"] == table["county"].iloc[index]]
        reference = county.loc[county["population"].idxmax()]
        village_snap, village_speed = village_access(group, reference)
        pick = accessible_village(group["lon"], group["lat"], group["population"], village_snap, village_speed)
        before = snap[index]
        table.loc[table.index[index], ["lon", "lat"]] = [round(float(group["lon"].iloc[pick]), 5), round(float(group["lat"].iloc[pick]), 5)]
        moved.append({"township": table["county"].iloc[index] + table["town"].iloc[index], "snap_before_m": round(float(before)), "snap_after_m": round(float(village_snap[pick])), "kmh": round(float(village_speed[pick]))})
    log(f"Points moved to an accessible village: {moved}")
    table["point_moved"] = False
    table.loc[table.index[flagged], "point_moved"] = True
    return table


def road_matrix(table, chunk=50, pause=1.1, log=print):
    """Road distance (km) and driving time (hours) between all points, from the public OSRM server.

    The server allows at most one request a second and is for non-commercial use, so this runs once and the
    result is saved. A car profile is used; truck times are adjusted in the cost model.
    """
    n = len(table)
    distance, duration = np.full((n, n), np.nan), np.full((n, n), np.nan)
    snapped = np.zeros(n)
    coordinates = [f"{lon:.5f},{lat:.5f}" for lon, lat in zip(table["lon"], table["lat"])]
    blocks = [list(range(i, min(i + chunk, n))) for i in range(0, n, chunk)]
    total = len(blocks) ** 2
    done = 0
    for sources in blocks:
        for targets in blocks:
            shared = sorted(set(sources) | set(targets))
            position = {node: k for k, node in enumerate(shared)}
            query = urllib.parse.urlencode({"sources": ";".join(str(position[i]) for i in sources), "destinations": ";".join(str(position[j]) for j in targets),
                                            "annotations": "distance,duration"})
            url = OSRM_TABLE + ";".join(coordinates[i] for i in shared) + "?" + query
            payload = _osrm_json(url, pause)
            distance[np.ix_(sources, targets)] = np.array(payload["distances"], dtype=float) / 1000
            duration[np.ix_(sources, targets)] = np.array(payload["durations"], dtype=float) / 3600
            for i, source in zip(sources, payload["sources"]):
                snapped[i] = source.get("distance", 0.0)
            done += 1
            if done % 10 == 0 or done == total:
                log(f"{done}/{total} blocks")
    return distance, duration, snapped


def simplify_line(points, tolerance):
    """Ramer-Douglas-Peucker simplification, for the map outlines."""
    points = np.asarray(points, dtype=float)
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    line = end - start
    length = np.hypot(*line)
    if length == 0:
        distance = np.hypot(*(points - start).T)
    else:
        distance = np.abs(line[0] * (points[:, 1] - start[1]) - line[1] * (points[:, 0] - start[0])) / length
    index = int(np.argmax(distance))
    if distance[index] <= tolerance:
        return np.array([start, end])
    return np.vstack([simplify_line(points[:index + 1], tolerance)[:-1], simplify_line(points[index:], tolerance)])


def township_outlines(table, raw_dir=RAW_DIR, tolerance=0.004, min_area=0.0005):
    """Simplified township outlines for maps: {town_code: [ring of [lon, lat], ...]}."""
    towns, _ = _read_shapes(Path(raw_dir) / "town", "TOWN_MOI")
    wanted = set(table["town_code"])
    outlines = {}
    for record, shape in zip(towns.iterRecords(), towns.iterShapes()):
        if record["TOWNCODE"] not in wanted:
            continue
        bounds = list(shape.parts) + [len(shape.points)]
        rings = []
        for start, end in zip(bounds[:-1], bounds[1:]):
            area, _, _ = ring_area_centroid(shape.points[start:end])
            if abs(area) >= min_area:
                # a ring is split in two so that its start and end cannot collapse together
                middle = (end - start) // 2
                ring = np.asarray(shape.points[start:end])
                simple = np.vstack([simplify_line(ring[:middle + 1], tolerance)[:-1], simplify_line(ring[middle:], tolerance)])
                rings.append(np.round(simple, 3).tolist())
        if not rings:
            biggest = max(zip(bounds[:-1], bounds[1:]), key=lambda pair: abs(ring_area_centroid(shape.points[pair[0]:pair[1]])[0]))
            rings.append(np.round(simplify_line(np.asarray(shape.points[biggest[0]:biggest[1]]), tolerance), 3).tolist())
        outlines[record["TOWNCODE"]] = rings
    return outlines


def save_matrix(path, matrix, codes):
    """Write a labelled square matrix to a compressed CSV."""
    pd.DataFrame(np.round(matrix, 2), index=codes, columns=codes).to_csv(path, compression="gzip")


def load_geography(processed_dir=PROCESSED_DIR):
    """Townships with road distance and time matrices in the same order."""
    processed_dir = Path(processed_dir)
    table = pd.read_csv(processed_dir / "townships.csv", dtype={"town_code": str})
    distance = pd.read_csv(processed_dir / "road_km.csv.gz", index_col=0, dtype={0: str})
    duration = pd.read_csv(processed_dir / "road_hours.csv.gz", index_col=0, dtype={0: str})
    codes = table["town_code"].tolist()
    if [str(c) for c in distance.index] != codes or list(distance.columns) != codes:
        raise ValueError("The road matrix and the township table are not in the same order")
    return table, distance.to_numpy(), duration.to_numpy()
