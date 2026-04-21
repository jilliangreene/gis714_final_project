import folium
import matplotlib.cm as cm
import matplotlib.colors as mcolors

# Folium needs EPSG:4326
patches_wgs84 = patches.to_crs("EPSG:4326")
 
# Colour patches by area_ha using YlOrRd
area     = patches_wgs84["area_ha"]
vmin, vmax = area.min(), area.max()
cmap_fn  = cm.get_cmap("YlOrRd")
norm     = mcolors.LogNorm(vmin=max(vmin, 0.01), vmax=vmax)  # log scale handles skew
 
def area_to_hex(val):
    rgba = cmap_fn(norm(val))
    return mcolors.to_hex(rgba)
 
# Centre map on patch centroid cloud
map_center = [
    patches_wgs84.geometry.centroid.y.mean(),
    patches_wgs84.geometry.centroid.x.mean(),
]
 
m = folium.Map(
    location=map_center,
    zoom_start=11,
    tiles="CartoDB positron",   # clean basemap 
)
 
# Optional: add a satellite layer the user can toggle - no API key required!
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr="Esri World Imagery",
    name="Satellite",
    overlay=False,
    control=True,
).add_to(m)
 
# Add patches as a GeoJson layer with tooltips
def style_fn(feature):
    val = feature["properties"]["area_ha"]
    return {
        "fillColor":   area_to_hex(val),
        "color":       "steelblue",
        "weight":      0.6,
        "fillOpacity": 0.7,
    }
 
tooltip_fields = (
    ["patch_id", "area_ha", "perimeter_m", "compactness", "shape_index"]
    + [c for c in patches_wgs84.columns if c.startswith("lc_") and c.endswith("_pct")]
    + ["nhd_ftype", "nhd_fname"]
)
# Keep only columns that actually exist
tooltip_fields = [f for f in tooltip_fields if f in patches_wgs84.columns]
 
tooltip_aliases = [f.replace("_", " ").replace("lc ", "").replace(" pct", " %").title()
                   for f in tooltip_fields]
 
folium.GeoJson(
    patches_wgs84[tooltip_fields + ["geometry"]],
    name="Water patches",
    style_function=style_fn,
    tooltip=folium.GeoJsonTooltip(
        fields=tooltip_fields,
        aliases=tooltip_aliases,
        localize=True,
        sticky=True,
        labels=True,
    ),
).add_to(m)
 
# Colour bar as a custom legend
legend_html = """
<div style="
    position: fixed; bottom: 40px; left: 40px; z-index: 1000;
    background: white; padding: 10px 14px; border-radius: 8px;
    border: 1px solid #ccc; font-size: 12px; font-family: sans-serif;
    box-shadow: 2px 2px 6px rgba(0,0,0,.2);">
  <b>Patch area (ha)</b><br>
  <svg width="160" height="16" style="margin-top:4px">
    <defs>
      <linearGradient id="lg" x1="0" x2="1" y1="0" y2="0">
        <stop offset="0%"   stop-color="#ffffb2"/>
        <stop offset="33%"  stop-color="#fecc5c"/>
        <stop offset="66%"  stop-color="#fd8d3c"/>
        <stop offset="100%" stop-color="#800026"/>
      </linearGradient>
    </defs>
    <rect width="160" height="16" fill="url(#lg)" rx="3"/>
  </svg>
  <div style="display:flex; justify-content:space-between; width:160px; margin-top:2px">
    <span>{:.1f}</span><span>(log scale)</span><span>{:.0f}</span>
  </div>
</div>
""".format(vmin, vmax)
 
m.get_root().html.add_child(folium.Element(legend_html))
folium.LayerControl().add_to(m)
 
print(f"Patches: {len(patches_wgs84):,}  |  "
      f"Area range: {vmin:.2f} – {vmax:.1f} ha")
 
m.save("water_patches_map.html")