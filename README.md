# kz-fires

Satellite fire hotspots for Kazakhstan: filter out permanent heat sources, group detections into fire events, and send a Telegram alert to people within a chosen radius, with distance, wind and data age.

The point of the project is the measurement, not the bot. Every number below comes with `n`, `k` and a 95 % interval, parameters were chosen on one season and tested once on two later seasons.

> Unofficial. Not an emergency service. In danger, call 112.

## Problem

NASA FIRMS publishes VIIRS hotspots several times a day. In Kazakhstan a large share of them are not fires: gas flares, refineries, steel and ferroalloy plants, cement works, burning coal seams. In a quiet year they are about a third of all detections. A naive "hotspot near you" alert is mostly noise.

## Pipeline

```
FIRMS NRT/SP ──► permanent-source mask ──► events (space-time clustering)
                                                │
Open-Meteo wind ──► threat per subscriber zone ─┤ distance, bearing, wind toward/across/away
                                                ▼
                                   alert decision ──► Telegram
                    (min points, max data age, 3 msgs/24h, no repeats, hysteresis)
```

| Part | What it does | Code |
|---|---|---|
| Ingest | FIRMS area API, idempotent load (`INSERT … ON CONFLICT DO NOTHING`), map key never logged | `sources/firms.py`, `ingest.py` |
| Mask | 2 km grid cells (EPSG:6933, plus 1 neighbouring cell) with ≥ 5 detections in ≥ 3 different months during the 3 years before the season | `core/mask.py` |
| Events | space-time connected components, deterministic ids, late points join existing events | `core/events.py` |
| Threat | haversine distance, bearing, wind class from Open-Meteo `wind_direction_10m` | `core/threat.py` |
| Decision | alert / update / suppress with a logged reason | `core/decision.py` |
| Freshness | API reachability and "no new rows" gap tracked separately | `core/freshness.py` |
| Evaluation | Wilson intervals, stratified weighting, bootstrap | `eval/` |

## Results

### Filter on labeled points (TEST: seasons 2024 + 2025, n = 200)

Each point was labeled from Sentinel-2 L2A images before and after the overpass (true color, SWIR composite, dNBR), ESA WorldCover land cover and nearby OSM industrial objects. FIRMS fields (`confidence`, `type`, FRP) and detection history were hidden during labeling. Classes: vegetation fire, agricultural burn, permanent source, unclear.

| Filter | Permanent sources removed | Real fires removed | Precision of kept points |
|---|---|---|---|
| none | 0/60 | 0/107 | 107/167 = 64 % [57–71] |
| FIRMS `confidence = low` | 3/60 = 5 % [2–14] | 6/107 = 6 % [3–12] | 101/158 = 64 % [56–71] |
| FIRMS `type = 2` (static source, SP only) | 25/60 = 42 % [30–54] | 0/107 = 0 % [0–3] | 107/142 = 75 % [68–82] |
| **this mask** | **44/60 = 73 % [61–83]** | **7/107 = 7 % [3–13]** | **100/116 = 86 % [79–91]** |

Precision weighted by stratum size in the full season: 90 % [82–97] for the mask vs 63 % [49–77] unfiltered.

How it was kept honest:
- mask parameters were picked on the 2023 season (100 labeled points) by a rule written down before the grid search, then frozen with a hash;
- 2024 and 2025 labels were committed and tagged (`labels-v1`) before the mask was run on them;
- TEST was evaluated once.

On the 2023 tuning season the same mask looked better (29/32 sources, 2/54 fires). The drop on TEST is the expected cost of choosing 1 configuration out of 54 on 86 points. The 5 % target for removed fires was missed (7 %, interval 3–13 %). Of the 7 fires the mask removed, 4 were reed marshes in river deltas and 2 were rice paddies: places that burn every year and in several months, so by detection history alone they look like a permanent source.

### Load on subscribers (replay of 2024 and 2025)

300 virtual subscribers placed at random OSM settlements, 15 km radius.

| Season | Messages | Subscriber-days with an alert | p95 per day (days with alerts) | Max per day | Messages per subscriber per season (median / p95) |
|---|---|---|---|---|---|
| 2024 | 871 | 0.7 % | 2 | 3 | 1 / 12 |
| 2025 | 2 233 | 1.6 % | 3 | 3 | 4 / 27 |

2025 was a heavy fire year; the 3-per-day cap was hit 323 times.

### Case study: Abai region, 8 June 2023

Replay on archived data, settlements within 30 km of the first detections:
- first alerts at 06:08 UTC on 8 June to 2 villages ~14 km away, from an event of 2 points, about 14 hours before the main burn (20:00–21:00 UTC);
- 2 more villages at 08:39, 3 at 20:01 and 6 at 20:52 UTC, when the event grew to ~2 000 detections;
- 13 of 23 settlements received an alert; with a 3-hour data delay the same alerts arrive 3 hours later.

This is a replay on Standard Processing data, not on what was actually available in real time in 2023.

### A live finding

On 17 November 2026 the SNPP near-real-time stream had no data after 2 November, while the API itself answered normally. "Last successful request" alone would report the source as healthy; tracking the gap since the last new row flags it as degraded.

## Run

Requires Python 3.12 and Docker.

```
make setup          # venv and dependencies
make db-up          # PostGIS + migrations
make border         # Kazakhstan border into the DB
make test           # tests run on fixtures, no network
make lint

make ingest         # one FIRMS NRT poll (needs FIRMS_MAP_KEY in .env)
make live           # poll every 3 minutes
make bot            # Telegram bot (needs TELEGRAM_BOT_TOKEN); /start, /status, /demo, /stop
make report         # filter metrics on the 2023 season
make retro          # 2024/2025 replay and the Abai case
```

Annual FIRMS country files go to `data/archive/` (`viirs-snpp_{year}_Kazakhstan.csv`, `viirs-jpss1_{year}_Kazakhstan.csv`).

## Bot messages

Every alert states the satellite time and data age, says it is unofficial and points to 112, and names the data source; the template cannot render without them (tested). No probabilities, no "reaches you in N hours", no instructions. Subscriber location is rounded to 0.01°, never written to logs, and deleted by `/stop`.

## Limitations

- Demand was not validated: no interviews with residents or emergency services.
- 300 labeled points, one labeling pass, no independent second labeler. Satellite images alone cannot always separate a controlled field burn from a field fire; both count as fires in the main metric.
- The mask cannot tell a factory from a reed marsh that burns every spring; see the 7 removed fires above.
- Unclear points (no cloud-free image, snow) are excluded from precision. With them counted as errors precision is 71 %, as fires 89 %.
- Replays use Standard Processing data; the live system uses NRT data, which arrives later and can differ.
- Wind is taken from Open-Meteo at 0.25° cells, not local measurements.
- The bot runs on a laptop; there is no hosted deployment and no uptime record.

## Data and licenses

- NASA FIRMS VIIRS 375 m (SNPP, NOAA-20). Attribution: NASA FIRMS.
- Copernicus Sentinel-2 L2A via Element 84 Earth Search. Contains modified Copernicus Sentinel data.
- ESA WorldCover 2021, CC BY 4.0.
- Open-Meteo, CC BY 4.0.
- OpenStreetMap contributors, ODbL.
- Natural Earth 10 m boundaries, public domain.
