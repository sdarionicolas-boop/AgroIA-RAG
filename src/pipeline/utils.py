import sys
import geopandas as gpd
import warnings
from shapely.validation import explain_validity

# Fix Unicode output on Windows cp1252 terminals
if sys.stdout.encoding and sys.stdout.encoding.lower().startswith('cp'):
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')

def validar_shapefile(shp_path):
    """Valida el shapefile y extrae información básica."""
    warnings_list = []
    try:
        lote = gpd.read_file(shp_path)
    except Exception as e:
        raise ValueError("No se pudo leer el shapefile.\n   Error: " + str(e) +
                         "\n   Verificar que .shp, .shx, .dbf y .prj esten completos.")
    if lote.empty:
        raise ValueError("El shapefile esta vacio. No contiene geometrias.")
    if lote.crs is None:
        raise ValueError("El shapefile no tiene CRS definido.\n   Falta el archivo .prj.")
    
    if len(lote) > 1:
        warnings_list.append(f"El shapefile tiene {len(lote)} poligonos. Se analizara solo el de mayor area.")
        # Usar CRS proyectado para calcular area real para el ordenamiento
        lote_proyectado = lote.to_crs(lote.estimate_utm_crs())
        lote["_area"] = lote_proyectado.geometry.area
        lote = lote.sort_values("_area", ascending=False).head(1).drop(columns="_area").reset_index(drop=True)
    
    if lote.crs.to_epsg() != 4326:
        lote = lote.to_crs("EPSG:4326")
    
    geom = lote.geometry.iloc[0]
    if geom.geom_type == "MultiPolygon":
        warnings_list.append("MultiPolygon detectado. Se usara el poligono de mayor area.")
        geom = max(geom.geoms, key=lambda p: p.area)
        lote.at[0, "geometry"] = geom
    elif geom.geom_type != "Polygon":
        raise ValueError("Tipo de geometria no soportado: " + geom.geom_type)
    
    if not geom.is_valid:
        motivo = explain_validity(geom)
        warnings_list.append("Geometria con errores topologicos: " + motivo + ". Corrigiendo con buffer 0.")
        lote.at[0, "geometry"] = geom.buffer(0)
        geom = lote.geometry.iloc[0]
        
    zona_utm   = int((geom.centroid.x + 180) / 6) + 1
    hemisferio = 32700 if geom.centroid.y < 0 else 32600
    epsg_utm   = hemisferio + zona_utm
    lote_utm   = lote.to_crs(f"EPSG:{epsg_utm}")
    hectareas  = lote_utm.geometry.area.sum() / 10_000
    
    if hectareas < 1:
        raise ValueError(f"Superficie demasiado pequena: {hectareas:.2f} ha. Minimo 1 ha.")
    
    print(f"✓ Shapefile valido: {hectareas:.2f} ha | {geom.geom_type} | EPSG:{epsg_utm}")
    for w in warnings_list:
        print(f"  ⚠ {w}")
    return lote, warnings_list, epsg_utm
