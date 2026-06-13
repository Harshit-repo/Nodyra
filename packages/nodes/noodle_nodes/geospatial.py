"""Geospatial and mapping nodes.

Geospatial libraries are optional and are imported lazily inside each node so
the base node registry remains fast and lightweight.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from noodle.artifacts import is_artifact_ref, read_bytes, write_text
from noodle.datasets import is_dataset_ref
from noodle.sdk import node
from noodle_nodes.datasets import materialize_dataset, records_to_dataset

GEO_CATEGORY = "Geospatial"


def _to_records(value: Any, *, cap: int = 100_000) -> list[dict[str, Any]]:
    if is_dataset_ref(value):
        return materialize_dataset(value, cap=cap, allow_truncate=True)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("records"), list):
            return [row for row in value["records"] if isinstance(row, dict)]
        return [value]
    return []


def _ts() -> str:
    return datetime.now(tz=UTC).isoformat()


def _require_geopandas():
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "This geospatial node requires geopandas. Add geopandas to the "
            "workflow environment and rebuild it."
        ) from exc
    return gpd


def _points_gdf(records: list[dict[str, Any]], lat_column: str, lon_column: str, crs: str):
    gpd = _require_geopandas()
    try:
        from shapely.geometry import Point  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("geopandas requires shapely for geometry operations.") from exc

    if not lat_column or not lon_column:
        raise ValueError("lat_column and lon_column are required.")
    geometries = []
    valid_rows: list[dict[str, Any]] = []
    for row in records:
        try:
            lat = float(row.get(lat_column))
            lon = float(row.get(lon_column))
        except (TypeError, ValueError):
            continue
        valid_rows.append(row)
        geometries.append(Point(lon, lat))
    if not valid_rows:
        raise ValueError("No valid latitude/longitude rows found.")
    return gpd.GeoDataFrame(valid_rows, geometry=geometries, crs=crs or "EPSG:4326")


def _gdf_to_dataset(gdf: Any, *, name: str) -> dict[str, Any]:
    frame = gdf.copy()
    frame["geometry_wkt"] = frame.geometry.to_wkt()
    frame = frame.drop(columns=["geometry"])
    return records_to_dataset(frame.to_dict(orient="records"), name=name)


@node(
    name="Map Generate",
    id="map_generate",
    category=GEO_CATEGORY,
    icon="map",
    requirements=["folium>=0.15"],
    params={
        "lat_column": {"description": "Latitude column."},
        "lon_column": {"description": "Longitude column."},
        "label_column": {"description": "Optional marker label/popup column."},
        "map_type": {"choices": ["markers", "heatmap"]},
        "zoom_start": {"description": "Initial map zoom level."},
        "filename": {"description": "HTML artifact filename."},
    },
)
def map_generate(
    input: Any = None,
    lat_column: str = "lat",
    lon_column: str = "lon",
    label_column: str = "",
    map_type: str = "markers",
    zoom_start: int = 4,
    filename: str = "map.html",
) -> dict[str, Any]:
    """Generate an interactive Folium map artifact from point records."""
    try:
        import folium  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Map Generate requires folium>=0.15.") from exc

    rows = _to_records(input)
    points: list[tuple[float, float, str]] = []
    for row in rows:
        try:
            lat = float(row.get(lat_column))
            lon = float(row.get(lon_column))
        except (TypeError, ValueError):
            continue
        label = str(row.get(label_column) or "") if label_column else ""
        points.append((lat, lon, label))
    if not points:
        raise ValueError("No valid points found. Check lat_column/lon_column.")

    avg_lat = sum(p[0] for p in points) / len(points)
    avg_lon = sum(p[1] for p in points) / len(points)
    fmap = folium.Map(location=[avg_lat, avg_lon], zoom_start=int(zoom_start or 4))

    if map_type == "heatmap":
        try:
            from folium.plugins import HeatMap  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Folium HeatMap plugin is unavailable.") from exc
        HeatMap([[lat, lon] for lat, lon, _ in points]).add_to(fmap)
    else:
        for lat, lon, label in points:
            folium.Marker([lat, lon], popup=label or None).add_to(fmap)

    html = fmap.get_root().render()
    artifact = write_text(
        html,
        filename or "map.html",
        "text/html; charset=utf-8",
        metadata={"kind": "map", "points": len(points)},
    )
    return {
        "map": artifact,
        "point_count": len(points),
        "center": {"lat": avg_lat, "lon": avg_lon},
        "created_at": _ts(),
    }


@node(
    name="Geocode",
    id="geocode",
    category=GEO_CATEGORY,
    icon="map-pin",
    requirements=["geopy>=2.4"],
    output_kinds={"main": "dataset"},
    params={
        "address_column": {
            "description": "Address column for bulk input. Blank uses address param/input string.",
        },
        "address": {"description": "Single address when no dataset is wired."},
        "provider": {"choices": ["nominatim"]},
        "user_agent": {"description": "Nominatim requires a meaningful user agent."},
        "delay_seconds": {"description": "Delay between requests for rate limits."},
        "max_rows": {"description": "Maximum input rows to geocode."},
    },
)
def geocode(
    input: Any = None,
    address_column: str = "",
    address: str = "",
    provider: str = "nominatim",
    user_agent: str = "noodle-geocoder",
    delay_seconds: float = 1.1,
    max_rows: int = 100,
) -> dict[str, Any]:
    """Geocode addresses to latitude/longitude."""
    try:
        from geopy.geocoders import Nominatim  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Geocode requires geopy>=2.4.") from exc
    if provider != "nominatim":
        raise ValueError("Only provider='nominatim' is currently supported.")

    geocoder = Nominatim(user_agent=user_agent or "noodle-geocoder")
    rows = _to_records(input)
    if rows and address_column:
        work = rows[: max(1, int(max_rows or 100))]
    else:
        single = address or (str(input) if input is not None and not rows else "")
        if not single:
            raise ValueError("address or address_column is required.")
        work = [{"address": single}]
        address_column = "address"

    output: list[dict[str, Any]] = []
    for idx, row in enumerate(work):
        query = str(row.get(address_column) or "").strip()
        result = dict(row)
        if not query:
            result.update({"lat": None, "lon": None, "geocode_error": "blank address"})
            output.append(result)
            continue
        try:
            loc = geocoder.geocode(query)
            if loc is None:
                result.update({"lat": None, "lon": None, "geocode_error": "no match"})
            else:
                result.update(
                    {
                        "lat": float(loc.latitude),
                        "lon": float(loc.longitude),
                        "geocoded_address": loc.address,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            result.update({"lat": None, "lon": None, "geocode_error": str(exc)})
        output.append(result)
        if idx < len(work) - 1 and delay_seconds:
            time.sleep(float(delay_seconds))

    return records_to_dataset(output, name="geocoded.parquet")


@node(
    name="Reverse Geocode",
    id="reverse_geocode",
    category=GEO_CATEGORY,
    icon="locate",
    requirements=["geopy>=2.4"],
    output_kinds={"main": "dataset"},
    params={
        "lat_column": {"description": "Latitude column."},
        "lon_column": {"description": "Longitude column."},
        "provider": {"choices": ["nominatim"]},
        "user_agent": {"description": "Nominatim user agent."},
        "delay_seconds": {"description": "Delay between requests for rate limits."},
        "max_rows": {"description": "Maximum rows to reverse-geocode."},
    },
)
def reverse_geocode(
    input: Any = None,
    lat_column: str = "lat",
    lon_column: str = "lon",
    provider: str = "nominatim",
    user_agent: str = "noodle-geocoder",
    delay_seconds: float = 1.1,
    max_rows: int = 100,
) -> dict[str, Any]:
    """Reverse geocode latitude/longitude rows to addresses."""
    try:
        from geopy.geocoders import Nominatim  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Reverse Geocode requires geopy>=2.4.") from exc
    if provider != "nominatim":
        raise ValueError("Only provider='nominatim' is currently supported.")
    rows = _to_records(input)[: max(1, int(max_rows or 100))]
    if not rows:
        raise ValueError("input must contain rows with latitude/longitude columns.")

    geocoder = Nominatim(user_agent=user_agent or "noodle-geocoder")
    output: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        result = dict(row)
        try:
            lat = float(row.get(lat_column))
            lon = float(row.get(lon_column))
            loc = geocoder.reverse((lat, lon), exactly_one=True)
            result["address"] = loc.address if loc else None
        except Exception as exc:  # noqa: BLE001
            result["reverse_geocode_error"] = str(exc)
        output.append(result)
        if idx < len(rows) - 1 and delay_seconds:
            time.sleep(float(delay_seconds))
    return records_to_dataset(output, name="reverse-geocoded.parquet")


@node(
    name="Shapefile Read",
    id="shapefile_read",
    category=GEO_CATEGORY,
    icon="file-map",
    requirements=["geopandas>=0.14"],
    output_kinds={"main": "dataset"},
    params={
        "path": {
            "description": (
                "Local path to a shapefile/GeoJSON/GeoPackage. "
                "Used when no artifact is wired."
            ),
        },
        "max_features": {"description": "Maximum features to return (0 = all)."},
    },
)
def shapefile_read(
    input: Any = None,
    path: str = "",
    max_features: int = 0,
) -> dict[str, Any]:
    """Read a geospatial file into a DatasetRef with geometry_wkt."""
    gpd = _require_geopandas()
    source: str
    temp_path = None
    if is_artifact_ref(input):
        from tempfile import NamedTemporaryFile

        suffix = "." + str(input.get("name") or "data.geojson").split(".")[-1]
        tmp = NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(read_bytes(input))
        tmp.close()
        source = tmp.name
        temp_path = tmp.name
    else:
        source = path or (str(input) if input is not None else "")
    if not source:
        raise ValueError("path or geospatial artifact input is required.")
    try:
        gdf = gpd.read_file(source)
        if max_features and max_features > 0:
            gdf = gdf.head(int(max_features))
        return _gdf_to_dataset(gdf, name="geospatial-features.parquet")
    finally:
        if temp_path:
            try:
                import os

                os.unlink(temp_path)
            except OSError:
                pass


@node(
    name="Geospatial Join",
    id="geospatial_join",
    category=GEO_CATEGORY,
    icon="route",
    requirements=["geopandas>=0.14"],
    output_kinds={"main": "dataset"},
    params={
        "left_lat_column": {"description": "Left latitude column."},
        "left_lon_column": {"description": "Left longitude column."},
        "right_lat_column": {"description": "Right latitude column."},
        "right_lon_column": {"description": "Right longitude column."},
        "right_records_json": {"description": "Right records JSON array.", "multiline": True},
        "predicate": {"choices": ["intersects", "within", "contains", "nearest"]},
        "crs": {"description": "Input CRS, usually EPSG:4326."},
    },
)
def geospatial_join(
    input: Any = None,
    right: Any = None,
    left_lat_column: str = "lat",
    left_lon_column: str = "lon",
    right_lat_column: str = "lat",
    right_lon_column: str = "lon",
    right_records_json: str = "[]",
    predicate: str = "nearest",
    crs: str = "EPSG:4326",
) -> dict[str, Any]:
    """Spatially join two point datasets."""
    gpd = _require_geopandas()
    left_rows = _to_records(input)
    right_rows = _to_records(right)
    if not right_rows:
        parsed = json.loads(right_records_json or "[]")
        right_rows = [row for row in parsed if isinstance(row, dict)]
    left_gdf = _points_gdf(left_rows, left_lat_column, left_lon_column, crs)
    right_gdf = _points_gdf(right_rows, right_lat_column, right_lon_column, crs)
    if predicate == "nearest":
        joined = gpd.sjoin_nearest(left_gdf, right_gdf, how="left", distance_col="distance")
    else:
        joined = gpd.sjoin(left_gdf, right_gdf, how="left", predicate=predicate)
    return _gdf_to_dataset(joined, name="geospatial-join.parquet")


@node(
    name="Geospatial Buffer",
    id="geospatial_buffer",
    category=GEO_CATEGORY,
    icon="circle-dot",
    requirements=["geopandas>=0.14"],
    output_kinds={"main": "dataset"},
    params={
        "lat_column": {"description": "Latitude column."},
        "lon_column": {"description": "Longitude column."},
        "radius_meters": {"description": "Buffer radius in meters."},
        "crs": {"description": "Input CRS."},
    },
)
def geospatial_buffer(
    input: Any = None,
    lat_column: str = "lat",
    lon_column: str = "lon",
    radius_meters: float = 1000,
    crs: str = "EPSG:4326",
) -> dict[str, Any]:
    """Create buffer polygons around point rows."""
    rows = _to_records(input)
    gdf = _points_gdf(rows, lat_column, lon_column, crs)
    projected = gdf.to_crs("EPSG:3857")
    projected["geometry"] = projected.geometry.buffer(float(radius_meters or 1000))
    buffered = projected.to_crs(crs or "EPSG:4326")
    buffered["buffer_radius_meters"] = float(radius_meters or 1000)
    return _gdf_to_dataset(buffered, name="geospatial-buffer.parquet")


@node(
    name="Geospatial Distance",
    id="geospatial_distance",
    category=GEO_CATEGORY,
    icon="ruler",
    requirements=["geopandas>=0.14"],
    output_kinds={"main": "dataset"},
    params={
        "left_lat_column": {"description": "Left latitude column."},
        "left_lon_column": {"description": "Left longitude column."},
        "right_lat_column": {"description": "Right latitude column."},
        "right_lon_column": {"description": "Right longitude column."},
        "right_records_json": {"description": "Right records JSON array.", "multiline": True},
        "nearest_only": {"description": "Return only nearest right row for each left row."},
    },
)
def geospatial_distance(
    input: Any = None,
    right: Any = None,
    left_lat_column: str = "lat",
    left_lon_column: str = "lon",
    right_lat_column: str = "lat",
    right_lon_column: str = "lon",
    right_records_json: str = "[]",
    nearest_only: bool = True,
) -> dict[str, Any]:
    """Compute point-to-point distances in meters."""
    left_rows = _to_records(input)
    right_rows = _to_records(right)
    if not right_rows:
        parsed = json.loads(right_records_json or "[]")
        right_rows = [row for row in parsed if isinstance(row, dict)]
    left_gdf = _points_gdf(left_rows, left_lat_column, left_lon_column, "EPSG:4326")
    right_gdf = _points_gdf(right_rows, right_lat_column, right_lon_column, "EPSG:4326")
    left_m = left_gdf.to_crs("EPSG:3857")
    right_m = right_gdf.to_crs("EPSG:3857")
    rows: list[dict[str, Any]] = []
    for left_idx, left_geom in enumerate(left_m.geometry):
        distances = [
            (float(left_geom.distance(right_geom)), right_idx)
            for right_idx, right_geom in enumerate(right_m.geometry)
        ]
        distances.sort(key=lambda item: item[0])
        selected = distances[:1] if nearest_only else distances
        for distance, right_idx in selected:
            rows.append(
                {
                    "left_index": left_idx,
                    "right_index": right_idx,
                    "distance_meters": distance,
                    "left": left_rows[left_idx],
                    "right": right_rows[right_idx],
                }
            )
    return records_to_dataset(rows, name="geospatial-distances.parquet")


@node(
    name="Coordinate Transform",
    id="coordinate_transform",
    category=GEO_CATEGORY,
    icon="crosshair",
    requirements=["pyproj>=3.6"],
    output_kinds={"main": "dataset"},
    params={
        "lat_column": {"description": "Latitude/Y column."},
        "lon_column": {"description": "Longitude/X column."},
        "from_crs": {"description": "Source CRS, e.g. EPSG:4326."},
        "to_crs": {"description": "Target CRS, e.g. EPSG:3857."},
        "output_x_column": {"description": "Output transformed X column."},
        "output_y_column": {"description": "Output transformed Y column."},
    },
)
def coordinate_transform(
    input: Any = None,
    lat_column: str = "lat",
    lon_column: str = "lon",
    from_crs: str = "EPSG:4326",
    to_crs: str = "EPSG:3857",
    output_x_column: str = "x",
    output_y_column: str = "y",
) -> dict[str, Any]:
    """Transform coordinates between coordinate reference systems."""
    try:
        from pyproj import Transformer  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Coordinate Transform requires pyproj>=3.6.") from exc
    rows = _to_records(input)
    if not rows:
        raise ValueError("input must contain coordinate rows.")
    transformer = Transformer.from_crs(from_crs, to_crs, always_xy=True)
    output: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        try:
            x, y = transformer.transform(float(row.get(lon_column)), float(row.get(lat_column)))
            out[output_x_column or "x"] = x
            out[output_y_column or "y"] = y
        except (TypeError, ValueError):
            out["coordinate_transform_error"] = "invalid coordinate"
        output.append(out)
    return records_to_dataset(output, name="coordinate-transform.parquet")


@node(
    name="Isochrone Generate",
    id="isochrone_generate",
    category=GEO_CATEGORY,
    icon="route",
    requirements=["osmnx>=1.9", "geopandas>=0.14"],
    output_kinds={"main": "dataset"},
    params={
        "lat": {"description": "Origin latitude."},
        "lon": {"description": "Origin longitude."},
        "travel_time_minutes": {"description": "Travel-time contour in minutes."},
        "network_type": {"choices": ["drive", "walk", "bike"]},
        "speed_kph": {"description": "Assumed travel speed in km/h."},
    },
)
def isochrone_generate(
    input: Any = None,
    lat: float = 0.0,
    lon: float = 0.0,
    travel_time_minutes: int = 15,
    network_type: str = "drive",
    speed_kph: float = 40.0,
) -> dict[str, Any]:
    """Generate a rough OSM network isochrone polygon around an origin."""
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
        import networkx as nx  # type: ignore[import-not-found]
        import osmnx as ox  # type: ignore[import-not-found]
        from shapely.geometry import MultiPoint  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Isochrone Generate requires osmnx, networkx, geopandas, and shapely."
        ) from exc

    if isinstance(input, dict):
        lat = float(input.get("lat", lat))
        lon = float(input.get("lon", lon))
    # Validate range, not truthiness: lat=0 (equator) / lon=0 (prime meridian)
    # are valid coordinates and must not be rejected as "missing" (R-4).
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise ValueError("lat must be in [-90, 90] and lon in [-180, 180].")

    distance_m = float(speed_kph or 40.0) * 1000 * (int(travel_time_minutes) / 60.0)
    graph = ox.graph_from_point(
        (lat, lon),
        dist=max(distance_m * 1.2, 1000),
        network_type=network_type,
    )
    meters_per_minute = float(speed_kph or 40.0) * 1000 / 60.0
    for _, _, _, data in graph.edges(keys=True, data=True):
        data["travel_time"] = data.get("length", 0) / max(meters_per_minute, 1e-9)
    origin = ox.distance.nearest_nodes(graph, lon, lat)
    subgraph = nx.ego_graph(
        graph,
        origin,
        radius=int(travel_time_minutes or 15),
        distance="travel_time",
    )
    node_points = [
        (data["x"], data["y"])
        for _, data in subgraph.nodes(data=True)
        if "x" in data and "y" in data
    ]
    if not node_points:
        raise RuntimeError("No reachable OSM nodes found for the requested isochrone.")
    polygon = MultiPoint(node_points).convex_hull
    gdf = gpd.GeoDataFrame(
        [
            {
                "lat": lat,
                "lon": lon,
                "travel_time_minutes": int(travel_time_minutes or 15),
                "network_type": network_type,
                "speed_kph": float(speed_kph or 40.0),
            }
        ],
        geometry=[polygon],
        crs="EPSG:4326",
    )
    return _gdf_to_dataset(gdf, name="isochrone.parquet")
