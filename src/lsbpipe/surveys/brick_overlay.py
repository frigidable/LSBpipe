"""Deterministic sky geometry for the viewer-only brick boundary overlay.

Sky coverage and valid NEXP must be established separately from FITS;
a brick outline does not assert pixel-level survey coverage.
"""
from __future__ import annotations

import json
import math

from .legacy import BrickHit, BRICK_RE


def brick_shapes(hits: list[BrickHit]) -> tuple[list[dict], list[str | None]]:
    """Return deduplicated display polygons and row-to-polygon keys.

    Group identical release records sharing the same region/brick and geometry;
    distinct geometry is never silently coalesced. Coordinates are unwrapped
    across RA=0 so that the overlay doesn't cross 359 degrees the wrong way.
    """
    features = []
    by_geom = {}
    rows = []
    for hit in hits:
        values = (hit.ra1, hit.ra2, hit.dec1, hit.dec2)
        if (not BRICK_RE.fullmatch(hit.name) or
            hit.region not in ("north", "south") or
            not all(math.isfinite(float(v)) for v in values) or
            not -90 <= hit.dec1 < hit.dec2 <= 90):
            rows.append(None)
            continue
        length = (hit.ra2 - hit.ra1) % 360.
        if not 0 < length <= 180:
            rows.append(None)
            continue
        start = hit.ra1 % 360
        stop = start + length
        fingerprint = (hit.region, hit.name, *(round(v, 9) for v in
            (start, stop, hit.dec1, hit.dec2)))
        if fingerprint in by_geom:
            key = by_geom[fingerprint]
            match = next(f for f in features if f["key"] == key)
            if hit.release not in match["releases"]:
                match["releases"].append(hit.release)
            rows.append(key)
            continue
        key = str(len(features))
        polygon = [
            [start, hit.dec1], [stop, hit.dec1],
            [stop, hit.dec2], [start, hit.dec2],
        ]
        features.append({"key": key, "brick": hit.name,
                         "region": hit.region, "releases": [hit.release],
                         "polygon": polygon})
        by_geom[fingerprint] = key
        rows.append(key)
    return features, rows


# The host site's source includes global Leaflet `map`, `L`,
# `dec2lat`, and `ra2long_C` (Imagine/templates/index.html).
# This is a private integration guarded by strict URL origin and readiness.
# No network requests or cross-origin script dependencies are added.
_INSTALL = r"""(function(){
  "use strict";
  if (location.origin !== "https://www.legacysurvey.org" ||
      !location.pathname.startsWith("/viewer/") ||
      typeof L === "undefined" || typeof map === "undefined" ||
      typeof ra2long_C !== "function" || typeof dec2lat !== "function") {
    return "unavailable";
  }
  var data = __FEATURES__;
  var old = window.__lsbpipeBricks;
  if (old && old.group) map.removeLayer(old.group);
  var group = L.layerGroup().addTo(map);
  var polygons = {};
  var selected = null, hovered = null;
  function restyle(){
    Object.keys(polygons).forEach(function(key){
      var shape = polygons[key];
      var highlight = (key === hovered || key === selected);
      shape.setStyle({
        color: highlight ? "#f5b942" : "#4cd7de",
        weight: highlight ? 2.4 : 1.6,
        opacity: 0.95,
        fillColor: "#f5b942",
        fillOpacity: key === hovered ? 0.36 :
                     key === selected ? 0.23 : 0.015
      });
      if (highlight) shape.bringToFront();
    });
  }
  data.forEach(function(feature){
    var currentLongitude = map.getCenter().lng;
    var points = feature.polygon.map(function(pair){
      var ra = ((pair[0] % 360) + 360) % 360;
      var lng = ra2long_C(ra, currentLongitude);
      return [dec2lat(pair[1]), lng];
    });
    var item = L.polygon(points, {
      color:"#4cd7de", weight:1.6, opacity:0.95,
      fillColor:"#f5b942", fillOpacity:0.015,
      bubblingMouseEvents:false
    }).addTo(group);
    polygons[feature.key] = item;
    var label = feature.brick + " · " + feature.region + " · " +
      feature.releases.map(function(r){return r.toUpperCase();}).join("/");
    item.bindTooltip(label, {sticky:true, direction:"top"});
    item.on("mouseover", function(){hovered=feature.key; restyle();});
    item.on("mouseout", function(){hovered=null; restyle();});
    item.on("click", function(e){
      selected=feature.key;
      restyle();
      if (e && e.originalEvent) L.DomEvent.stopPropagation(e.originalEvent);
    });
  });
  window.__lsbpipeBricks = {
    group: group,
    select: function(key){selected=key; restyle();},
    hover: function(key){hovered=key; restyle();},
    clear: function(){hovered=null; selected=null; restyle();},
    count: Object.keys(polygons).length
  };
  restyle();
  return "ready:" + window.__lsbpipeBricks.count;
})()"""


def installation_script(features: list[dict]) -> str:
    """JSON-only interpolation: never eval untrusted archive field values."""
    payload = json.dumps(features, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":"))
    return _INSTALL.replace("__FEATURES__", payload)


def highlight_script(key: str | None, *, hover: bool = False) -> str:
    payload = json.dumps(key)
    method = "hover" if hover else "select"
    return ("(function(){if(window.__lsbpipeBricks){"
            f"window.__lsbpipeBricks.{method}({payload});"
            "return true;}return false;})()")
