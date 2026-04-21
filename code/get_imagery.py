## Get imagery for NISAR model development
## Author: Jillian
## Feb 24, 2026

# import packages
import ee 
import geemap
import geopandas as gpd
import webbrowser
import os
import requests
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

# Visualize shp for confirmation
# Map = geemap.Map()
# Map.add_basemap('HYBRID')
# Map.addLayer(nisar_boundary, {}, 'Boundary')
# Map.centerObject(nisar_boundary)
# Map.to_html("map.html")
# webbrowser.open('map.html')


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

for i in range(n_images):

    image = ee.Image(image_list.get(i))
    image = image.select(['B1', 'B2', 'B3', 'B4', 'B6', 'B7', 'B8', 'B8A'])
    image = image.clip(nisar_boundary)

    date = ee.Date(image.get('system:time_start')).format('YYYY-MM-dd').getInfo()
    tile = image.get('MGRS_TILE').getInfo()
    
    filename = os.path.join(
        out_dir,
        f"S2_SR_{date}_T{tile}.tif"
    )
    
    print(f"Downloading {filename} ...")
    
    # Check and skip empty images
    valid_pixels = image.reduceRegion(
        reducer=ee.Reducer.count(),
        geometry=nisar_boundary.geometry(),
        scale=30,
        maxPixels=1e13
    ).getInfo()

    if not any(valid_pixels.values()):
        print(f"Skipping {date} — fully masked.")
        continue

    url = image.getDownloadURL({
        'scale': 100,
        'region': nisar_boundary.geometry(),
        'format': 'GEO_TIFF',
        'crs': 'EPSG:4326'
    })
    
    r = requests.get(url, stream=True)
    with open(filename, 'wb') as f:
        f.write(r.content)

    print("Done.\n")

print("All downloads finished.")