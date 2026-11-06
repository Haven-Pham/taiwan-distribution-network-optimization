# Data

## What is committed

| File | Content | Source and license |
|---|---|---|
| `processed/townships.csv` | 349 mainland townships: names, population (September 2026) and a representative point | Ministry of the Interior via data.gov.tw, [Open Government Data License 1.0](https://data.gov.tw/license) |
| `processed/township_outlines.json` | Simplified township outlines for maps | Same |
| `processed/road_km.csv.gz`, `processed/road_hours.csv.gz` | Road distance and car driving time between all townships | Derived from [OpenStreetMap](https://www.openstreetmap.org/copyright) through the public OSRM server. © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/). These two files are shared under the ODbL, not the MIT license of the code |
| `processed/networks.json` | The chosen network for each DC count | This project |

## Rebuilding

`python scripts/build_geography.py` downloads the official files into `data/raw/`, which is not committed, and rebuilds the township table. Add `--roads` to rebuild the road matrix. The public OSRM server allows at most one request a second and is for non-commercial use, so the script keeps to that limit, and the matrix is a saved snapshot. There is no need to rebuild it to reproduce the analysis.

| Dataset | data.gov.tw id |
|---|---|
| Village households and population by age, with area codes | 77132 |
| Village boundaries (TWD97 longitude and latitude) | 7438 |
| Township boundaries (TWD97 longitude and latitude) | 7441 |

## How a township becomes a point

1. Each village's point is the centre of its largest polygon.
2. A township's point is the village nearest the population-weighted centre, so it is a place where people live.
3. If that point is over 1 km from a road, the township moves to the most central village that is within 500 m of a road and reached from the county's largest township at 20 km/h or more. Without this rule, 13 mountain townships sat far from any road, and one route ran at 13 km/h along a forest track. If no village qualifies, the fastest option near a road is used.

Two points remain far from a road: Zhuoxi (9.2 km) and Xiulin (2.6 km). Travel times to both are plausible, and they hold 0.1% of the population.

## Checks

- Township names and populations match the official township table for the previous year, with a 0.37% difference in total.
- 19 offshore townships holding 1.2% of the population are excluded: Penghu, Kinmen, Lienchiang, Lanyu, Green Island and Liuqiu.
- The village boundary file and the population file agree on 97.4% of villages and all of the population.
- Each archive holds a one-record correction file beside the main shapefile, and the portal does not sort monthly population files by date. The code chooses the right file in both cases, and a test covers the date rule.

## Benchmark instances

`tests/data` holds four capacitated routing benchmarks with known best values, copied from the MIT-licensed [VRPLIB](https://github.com/PyVRP/VRPLIB) repository. They come from Augerat et al., Christofides et al., and Uchoa et al. (2017).
