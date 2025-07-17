from flask import Flask, render_template, request, send_file
import ee
import geemap
import os
import json
from datetime import datetime, timedelta

# Authenticate and Initialize Earth Engine
ee.Authenticate()
ee.Initialize(project='isrofirstproject')

app = Flask(__name__)

def get_mndwi_water_from_bounds(bounds, date_str):
    coords = bounds['coordinates'][0]
    roi = ee.Geometry.Polygon(coords)

    # Date range
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    start_date = (date_obj - timedelta(days=15)).strftime("%Y-%m-%d")
    end_date = (date_obj + timedelta(days=15)).strftime("%Y-%m-%d")

    # Sentinel-2 Collection
    collection = ee.ImageCollection('COPERNICUS/S2_HARMONIZED') \
        .filterBounds(roi) \
        .filterDate(start_date, end_date) \
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30)) \
        .map(lambda img: img.select(['B3', 'B11']))

    if collection.size().getInfo() == 0:
        raise Exception("No Sentinel-2 images found in date range.")

    # Compute MNDWI
    image = collection.median()
    green = image.select('B3')
    swir = image.select('B11')
    mndwi = green.subtract(swir).divide(green.add(swir)).rename('MNDWI')
    water_mask = mndwi.gt(0).selfMask()

    # Calculate total water area
    pixel_area = water_mask.multiply(ee.Image.pixelArea())
    total_area = pixel_area.reduceRegion(
        ee.Reducer.sum(), roi, 10, maxPixels=1e9
    ).get('MNDWI')
    area_km2 = ee.Number(total_area).divide(1e6).getInfo()

    # Convert water mask to polygons
    vectors = water_mask.reduceToVectors(
        geometry=roi,
        scale=10,
        geometryType='polygon',
        reducer=ee.Reducer.countEvery(),
        maxPixels=1e9
    )

    # ✅ Filter small areas (< 100,000 m² = 10 hectares)
    vectors = vectors.map(
        lambda f: f.set({'area_m2': f.geometry().area(maxError=1)})
    ).filter(ee.Filter.gt('area_m2', 100000))

    # ✅ Merge fragments into single water bodies
    dissolved = vectors.union(maxError=1)

    # ✅ Convert to GeoJSON
    geojson = geemap.ee_to_geojson(dissolved)
    os.makedirs("static/geojson", exist_ok=True)
    with open("static/geojson/water.geojson", "w") as f:
        json.dump(geojson, f)

    # ✅ Final waterbody count
    count = len(geojson['features'])

    # Create map
    center = roi.centroid().coordinates().getInfo()[::-1]
    m = geemap.Map(center=center, zoom=13)

    rgb_vis = image.visualize(bands=['B3', 'B11', 'B3'], min=0, max=3000)
    water_vis = water_mask.visualize(palette=['0000FF'], opacity=0.5)

    m.add_layer(rgb_vis, {}, "RGB")
    m.add_layer(water_vis, {}, "Water")

    # ✅ Show ROI outline
    roi_feature = ee.Feature(roi)
    roi_fc = ee.FeatureCollection([roi_feature])
    roi_style = {'color': 'red', 'fillColor': '00000000', 'width': 2}
    m.add_layer(roi_fc.style(**roi_style), {}, "ROI")

    m.to_html("templates/map.html")

    return count, round(area_km2, 2)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            bounds = json.loads(request.form['bounds'])
            date = request.form['date']
            count, area = get_mndwi_water_from_bounds(bounds, date)
            return render_template("result.html", count=count, area=area)
        except Exception as e:
            return render_template("index.html", error=str(e))
    return render_template("index.html")

@app.route("/map")
def map_page():
    return render_template("map.html")

@app.route("/download")
def download_geojson():
    return send_file("static/geojson/water.geojson", as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)
