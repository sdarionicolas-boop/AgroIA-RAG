# src/pipeline/validator.py
import os
import json
import pandas as pd
import geopandas as gpd
from datetime import datetime
from pathlib import Path
from tqdm import tqdm

from src.utils.config import settings
from src.pipeline.gee_extractor import init_gee
from src.pipeline.eventualidades import run_eventualidades, cargar_poligono_desde_gdf
from Poligonizacion.poligonizador_final import AgroIAPipeline

class AgroIAValidator:
    """
    Motor de certificación de precisión para AgroIA.
    Compara reportes de campo (Ground Truth) con el pipeline SAM + Satelital.
    """
    def __init__(self, dataset_path, target_col='Daño_Pond', id_col='Name'):
        self.dataset_path = dataset_path
        self.target_col = target_col
        self.id_col = id_col
        self.results = []
        
    def run_certification(self, limit=10):
        print(f"🛡️  Iniciando Proceso de Certificación - Dataset: {Path(self.dataset_path).name}")
        
        # 1. Preparar Entorno
        init_gee(project_id=settings.gee_project_id)
        
        # 2. Cargar Ground Truth
        gdf_truth = gpd.read_file(self.dataset_path)
        if limit: gdf_truth = gdf_truth.head(limit)
        
        # 3. Preparar entrada para SAM
        temp_csv = "val_input_tmp.csv"
        df_sam_in = pd.DataFrame({
            'id': gdf_truth[self.id_col],
            'lat': gdf_truth.geometry.y,
            'lon': gdf_truth.geometry.x,
            'fecha': gdf_truth['Fecha_De_S'].apply(lambda x: datetime.strptime(x, '%d/%m/%Y').strftime('%Y-%m-%d'))
        })
        df_sam_in.to_csv(temp_csv, index=False)
        
        try:
            # 4. PASO A: Delineación Estructural (SAM)
            print("--- PASO A: Generando Geometrías de Certificación (SAM) ---")
            poly_pipe = AgroIAPipeline()
            poly_pipe.cargar_datos(temp_csv)
            out_prefix = "val_sam_tmp"
            poly_pipe.ejecutar(output_prefix=out_prefix)
            
            gdf_sam = gpd.read_file(f"{out_prefix}.geojson")
            
            # 5. PASO B: Análisis Satelital sobre Polígonos Reales
            print("--- PASO B: Corriendo Peritajes de Auditoría ---")
            for _, row in tqdm(gdf_sam.iterrows(), total=len(gdf_sam), desc="Validando"):
                lote_id = row['id']
                truth_row = gdf_truth[gdf_truth[self.id_col] == lote_id].iloc[0]
                
                geom_ee, _ = cargar_poligono_desde_gdf(gdf_sam[gdf_sam['id'] == lote_id])
                
                res = run_eventualidades(
                    geom_ee=geom_ee,
                    fecha_evento=df_sam_in[df_sam_in['id'] == lote_id].iloc[0]['fecha'],
                    cultivo=str(truth_row.get('Cultivo', 'maiz')).lower(),
                    tipo_evento='granizo',
                    caso_nombre=f"VALIDACION_{lote_id}"
                )
                
                self.results.append({
                    'id': lote_id,
                    'area_sam': row['area_ha'],
                    'truth_damage': truth_row[self.target_col],
                    'agroia_damage': res['dano_pond_val'],
                    'error_abs': abs(truth_row[self.target_col] - res['dano_pond_val']),
                    'confianza': res['confianza']
                })
                
            # 6. Consolidar Reporte
            df_final = pd.DataFrame(self.results)
            metric_score = 100 - df_final['error_abs'].mean()
            
            print("\n" + "█"*60)
            print(f"  RESULTADO DE CERTIFICACIÓN AGROIA")
            print("█"*60)
            print(f"  Lotes Procesados:  {len(df_final)}")
            print(f"  Error Medio Abs:   {df_final['error_abs'].mean():.2f}%")
            print(f"  Score de Fidelidad: {metric_score:.2f}/100")
            print("█"*60)
            
            return df_final

        finally:
            # Limpiar temporales
            for f in [temp_csv, "val_sam_tmp.geojson", "val_sam_tmp_log.csv", 
                      "val_sam_tmp_mapa.html", "val_sam_tmp_reporte.pdf"]:
                if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    # Test rápido del validador
    validator = AgroIAValidator("Cordoba/Mayor a 60 y menor o igual a 80.shp")
    validator.run_certification(limit=3)
