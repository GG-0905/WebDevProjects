from flask import Flask, render_template, request
import ee
import folium
import geemap.foliumap as geemap
import branca
import os
from datetime import datetime, timedelta

# Authenticate and initialize EE
ee.Authenticate()
ee.Initialize(project='isrofirstproject')

app = Flask(__name__)

def get_ndwi_and_water_count(lat, lon, date_str):
    point = ee.Geometry.Point(lon, lat)

    # ±15 day window
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    start_date = (date_obj - timedelta(days=15)).strftime("%Y-%m-%d")
    end_date = (date_obj + timedelta(days=15)).strftime("%Y-%m-%d")

    collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
        .filterBounds(point) \
        .filterDate(start_date, end_date) \
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40)) \
        .sort('CLOUDY_PIXEL_PERCENTAGE')

    if collection.size().getInfo() == 0:
        collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(point) \
            .filterDate(start_date, end_date) \
            .sort('CLOUDY_PIXEL_PERCENTAGE')

    if collection.size().getInfo() == 0:
        raise Exception("No image found for the given date range and location.")

    image = collection.first()
    green = image.select('B3')
    nir = image.select('B8')
    ndwi = green.subtract(nir).divide(green.add(nir)).rename('NDWI')

    # NDWI threshold and mask
    water_mask = ndwi.gt(0.3).selfMask()

    # Connected components
    connected = water_mask.connectedComponents(ee.Kernel.plus(1), maxSize=128)
    sizes = connected.select('labels').connectedPixelCount(128, True)
    filtered = connected.updateMask(sizes.gte(100))  # keep only ≥100 pixels

    # Count distinct water bodies
    water_bodies = filtered.select('labels').reduceRegion(
        reducer=ee.Reducer.countDistinctNonNull(),
        geometry=point.buffer(15000),
        scale=10,
        maxPixels=1e9
    )

    count = water_bodies.getNumber('labels').getInfo()

    # Visualization layers
    rgb = image.visualize(bands=['B4', 'B3', 'B2'], min=0, max=3000)
    water_vis = water_mask.visualize(palette=['0000FF'], opacity=0.6)

    m = folium.Map(location=[lat, lon], zoom_start=12)
    m.add_child(geemap.ee_tile_layer(rgb, {}, 'RGB'))
    m.add_child(geemap.ee_tile_layer(water_vis, {}, 'Water Mask'))

    roi = ee.Geometry.Point(lon, lat).buffer(15000)
    folium.GeoJson(
        geemap.ee_to_geojson(roi),
        name='Region of Interest',
        style_function=lambda x: {
            'fillColor': '#FF0000',
            'color': '#FF0000',
            'weight': 2,
            'fillOpacity': 0.1,
        }
    ).add_to(m)

    colormap = branca.colormap.linear.YlGnBu_09.scale(-1, 1)
    colormap.caption = 'NDWI Water Confidence'
    m.add_child(colormap)
    folium.LayerControl().add_to(m)

    # Save map
    map_path = os.path.join('templates', 'map.html')
    m.save(map_path)

    return count

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        lat = float(request.form['latitude'])
        lon = float(request.form['longitude'])
        date = request.form['single_date']

        try:
            count = get_ndwi_and_water_count(lat, lon, date)
            return render_template('result.html', count=count)
        except Exception as e:
            return render_template('index.html', error=str(e))
    
    return render_template('index.html')

@app.route('/map')
def map_view():
    return render_template('map.html')

if __name__ == '__main__':
    app.run(debug=True)
