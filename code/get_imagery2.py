## Get imagery for NISAR model development
## Author: Jillian Greene
## Feb 24, 2026

# import packages
import ee 
import geemap
import geopandas as gpd
import webbrowser
import os
import requests
import rasterio
import glob
from rasterio.merge import merge
from collections import defaultdict
print("Packages imported!")


# Initialize GEE
try:
    ee.Initialize(project='jgreene4', opt_url="https://earthengine-highvolume.googleapis.com")
    print("GEE Initialized.")
except Exception:
    ee.Authenticate()  # follow the link; choose/allow the project when prompted
    ee.Initialize(project='jgreene4', opt_url="https://earthengine-highvolume.googleapis.com")
    print("GEE Initialized.")

# Set imagery criteria

# shp boundary
shp_path = 'nisar_sample_data/nisar_shp.shp'
gdf = gpd.read_file(shp_path)
# Ensure it's in WGS84 (EPSG:4326)
if gdf.crs != 'EPSG:4326':
    gdf = gdf.to_crs('EPSG:4326')

nisar_boundary = geemap.geopandas_to_ee(gdf)

# --------------------------------------------------
# Create tiling grid over boundary
# --------------------------------------------------

def create_grid(aoi, dx=0.3, dy=0.3):

    bounds = aoi.geometry().bounds()
    coords = ee.List(bounds.coordinates().get(0))  # outer ring list

    # Extract corners safely
    ll = ee.List(coords.get(0))  # lower-left
    ur = ee.List(coords.get(2))  # upper-right

    xmin = ee.Number(ll.get(0))
    ymin = ee.Number(ll.get(1))
    xmax = ee.Number(ur.get(0))
    ymax = ee.Number(ur.get(1))

    xseq = ee.List.sequence(xmin, xmax, dx)
    yseq = ee.List.sequence(ymin, ymax, dy)

    def make_cell(x, y):
        return ee.Feature(
            ee.Geometry.Rectangle(
                [x, y, ee.Number(x).add(dx), ee.Number(y).add(dy)],
                proj='EPSG:4326',
                geodesic=False
            )
        )

    def make_row(y):
        y = ee.Number(y)
        return xseq.map(lambda x: make_cell(x, y))

    grid = ee.FeatureCollection(yseq.map(make_row).flatten())

    # Only keep cells intersecting AOI
    return grid.filterBounds(aoi)


# adjust dx/dy depending on AOI size
grid = create_grid(nisar_boundary, dx=0.3, dy=0.3)

n_tiles = grid.size().getInfo()
print(f"Created {n_tiles} tiles.")

# date range
min = '2025-11-02'
max = '2025-11-04'

# initialize collections
# Sentinel-2 SR collection
s2_sr = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
         .filterBounds(nisar_boundary)
         .filterDate(min, max))

# S2 Cloud Probability collection (s2cloudless)
s2_clouds = (ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY')
             .filterBounds(nisar_boundary)
             .filterDate(min, max))

# Join S2 SR with cloud probability
s2 = ee.ImageCollection(ee.Join.saveFirst('cloud_mask').apply(
    primary=s2_sr,
    secondary=s2_clouds,
    condition=ee.Filter.equals(
        leftField='system:index',
        rightField='system:index'
    )
))

# s2cloudless mask from web
def mask_s2_clouds_and_shadows(image):
    
    # ----- PARAMETERS -----
    CLOUD_PROB_THRESHOLD = 50      # cloud probability %
    NIR_DARK_THRESHOLD = 0.15      # for shadow detection (scaled reflectance)
    SHADOW_PROJ_DISTANCE = 1       # km
    BUFFER = 50                    # meters
    
    # ----- CLOUD PROBABILITY -----
    cloud_prob = ee.Image(image.get('cloud_mask')).select('probability')
    clouds = cloud_prob.gt(CLOUD_PROB_THRESHOLD)
    
    # ----- SOLAR GEOMETRY -----
    azimuth = ee.Number(image.get('MEAN_SOLAR_AZIMUTH_ANGLE'))
    
    # ----- DARK PIXELS (potential shadows) -----
    nir = image.select('B8').divide(10000)  # scale reflectance
    dark_pixels = nir.lt(NIR_DARK_THRESHOLD)
    
    # ----- CLOUD PROJECTION (shadow direction) -----
    shadow_azimuth = ee.Number(90).subtract(azimuth)

    scale = 20  # meters
    max_distance_pixels = (SHADOW_PROJ_DISTANCE * 1000) / scale

    cloud_projection = (clouds
                        .directionalDistanceTransform(
                            shadow_azimuth,
                            max_distance_pixels)
                        .reproject(crs=image.select(0).projection(), scale=scale)
                        .select('distance')
                        .mask())
    
    shadows = cloud_projection.And(dark_pixels)
    
    # ----- COMBINE CLOUDS + SHADOWS -----
    cloud_shadow_mask = clouds.Or(shadows)
    # cloud_shadow_mask = clouds

    # ----- BUFFER MASK -----
    cloud_shadow_mask = (cloud_shadow_mask
                         .focal_min(2)
                         .focal_max(BUFFER/20)
                         .reproject(crs=image.select(0).projection(), scale=20))
    
    # ----- APPLY MASK -----
    return (image
            .updateMask(cloud_shadow_mask.Not())
            .copyProperties(image, image.propertyNames()))

# apply mask
collection = s2.map(mask_s2_clouds_and_shadows)

# local download params
out_dir = os.path.join(os.getcwd(), 'image_downloads')
if not os.path.exists(out_dir):
    os.makedirs(out_dir)

# Convert collection to list
image_list = collection.toList(collection.size())
n_images = collection.size().getInfo()

print(f"Found {n_images} image(s) to export.")

tile_list = grid.toList(grid.size())

for i in range(n_images):

    image = ee.Image(image_list.get(i))
    image = image.select(['B1', 'B2', 'B3', 'B4', 'B6', 'B7', 'B8', 'B8A'])
    image = image.clip(nisar_boundary)

    date = ee.Date(image.get('system:time_start')).format('YYYY-MM-dd').getInfo()
    mgrs = image.get('MGRS_TILE').getInfo()

    print(f"\nProcessing image {date} T{mgrs}")

    for t in range(n_tiles):

        tile_feat = ee.Feature(tile_list.get(t))
        tile_geom = tile_feat.geometry().intersection(
            nisar_boundary.geometry(), 1
        )

        filename = os.path.join(
            out_dir,
            f"S2_SR_{date}_T{mgrs}_tile{t}.tif"
        )

        print(f"  Downloading tile {t}...")

        try:
            url = image.getDownloadURL({
                'scale': 30,
                'region': tile_geom,
                'format': 'GEO_TIFF',
                'crs': 'EPSG:4326'
            })

            r = requests.get(url, stream=True)
            with open(filename, 'wb') as f:
                f.write(r.content)

            print("Done")

        except Exception as e:
            print(f"ailed: {e}")

print("All downloads finished.")


# Step 2:
# Mosaic all images from same dates using rasterio
# This is necessary because of size limits from GEE 
download_dir = os.path.join(os.getcwd(), "image_downloads")
final_dir = os.path.join(os.getcwd(), "final_images")

if not os.path.exists(final_dir):
    os.makedirs(final_dir)

# Group files by date

files = glob.glob(os.path.join(download_dir, "*.tif"))

files_by_date = defaultdict(list)

for f in files:
    # Example filename:
    # S2_SR_2025-11-02_T14TPP_tile3.tif
    
    basename = os.path.basename(f)
    parts = basename.split("_")
    
    date = parts[2]   # 2025-11-02
    files_by_date[date].append(f)

print(f"Found {len(files_by_date)} unique dates to mosaic.\n")

# Mosaic per date

for date, tif_list in files_by_date.items():

    print(f"Mosaicking {date} ({len(tif_list)} tiles)...")

    src_files = []
    
    for tif in tif_list:
        src = rasterio.open(tif)
        src_files.append(src)

    # Merge tiles
    mosaic, out_transform = merge(src_files)

    # Copy metadata from first tile
    out_meta = src_files[0].meta.copy()

    out_meta.update({
        "driver": "GTiff",
        "height": mosaic.shape[1],
        "width": mosaic.shape[2],
        "transform": out_transform
    })

    out_path = os.path.join(final_dir, f"S2_SR_{date}_mosaic.tif")

    with rasterio.open(out_path, "w", **out_meta) as dest:
        dest.write(mosaic)

    # Close all files
    for src in src_files:
        src.close()

    print(f"✓ Saved {out_path}\n")

print("All mosaics complete.")