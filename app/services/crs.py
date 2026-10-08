from collections.abc import Iterable

from pyproj import CRS
from pyproj.exceptions import CRSError

from app.core.errors import InvalidCRSError, MissingCRSError

WGS84 = CRS.from_epsg(4326)
MIXED = "MIXED"


def parse_crs(value: str) -> CRS:
    """Parse a client-supplied CRS such as "EPSG:32643", a WKT string or a PROJ string."""
    try:
        crs = CRS.from_user_input(value)
    except CRSError as exc:
        raise InvalidCRSError(f"'{value}' is not a valid CRS.") from exc
    check_measurable(crs)
    return crs


def resolve_layer_crs(layer: str, crs: CRS | None, fallback: CRS | None) -> CRS:
    """Use the layer's own CRS, then the client's source_crs. Never guess."""
    if crs is not None:
        check_measurable(crs)
        return crs
    if fallback is not None:
        return fallback
    raise MissingCRSError(
        f"Layer '{layer}' has no CRS because the shapefile has no .prj file. "
        "Send a source_crs form field, for example EPSG:32643.",
        {"layer": layer},
    )


def check_measurable(crs: CRS) -> None:
    if not (crs.is_geographic or crs.is_projected):
        raise InvalidCRSError(
            f"CRS '{crs.name}' is neither geographic nor projected, so it cannot be "
            "converted to longitude and latitude.",
            {"crs": crs.to_wkt()},
        )


def crs_label(crs: CRS) -> str:
    """A short label for responses: the authority code, or the CRS name when nothing matches."""
    authority = crs.to_authority()
    return ":".join(authority) if authority else crs.name


def crs_wkt(crs: CRS) -> str:
    return crs.to_wkt()


def summarize_crs(crs_list: Iterable[CRS]) -> str | None:
    """One label for a file: the shared CRS, or MIXED when layers differ.

    Compares CRS objects rather than labels, since two different CRSs can share a name.
    """
    unique: list[CRS] = []
    for crs in crs_list:
        if not any(crs.equals(seen) for seen in unique):
            unique.append(crs)
    if len(unique) > 1:
        return MIXED
    return crs_label(unique[0]) if unique else None
