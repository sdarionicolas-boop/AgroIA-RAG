import xml.etree.ElementTree as ET
import zipfile
import os
from shapely.geometry import Polygon, mapping

def parse_kml_manual(file_path):
    """
    Parser manual de KML/KMZ para cuando fallan los drivers de GDAL/Fiona.
    Extrae polígonos simples y los convierte en formato compatible con GeoJSON.
    """
    kml_content = None
    
    # Si es KMZ, extraer el doc.kml
    if file_path.lower().endswith('.kmz'):
        with zipfile.ZipFile(file_path, 'r') as z:
            # Buscar el archivo .kml principal
            kml_files = [f for f in z.namelist() if f.lower().endswith('.kml')]
            if kml_files:
                kml_content = z.read(kml_files[0])
    else:
        with open(file_path, 'rb') as f:
            kml_content = f.read()
            
    if not kml_content:
        return None

    try:
        root = ET.fromstring(kml_content)
        # Manejar namespaces de KML
        ns = {'kml': 'http://www.opengis.net/kml/2.2'}
        
        features = []
        # Buscar todos los Placemarks que tengan polígonos
        for pm in root.findall('.//kml:Placemark', ns):
            name = pm.find('kml:name', ns)
            name_str = name.text if name is not None else "Lote_KML"
            
            # Buscar coordenadas de polígono
            coords_node = pm.find('.//kml:coordinates', ns)
            if coords_node is not None:
                coords_text = coords_node.text.strip()
                # KML: lon,lat,alt lon,lat,alt ...
                coords = []
                for p in coords_text.split():
                    parts = p.split(',')
                    if len(parts) >= 2:
                        coords.append((float(parts[0]), float(parts[1])))
                
                if len(coords) >= 3:
                    poly = Polygon(coords)
                    features.append({
                        "type": "Feature",
                        "properties": {"id": name_str, "cultivo": "maiz"},
                        "geometry": mapping(poly)
                    })
        
        if not features:
            return None
            
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        print(f"Error parsing KML manually: {e}")
        return None
