# src/ingesta/api.py
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, UploadFile, File
from pydantic import BaseModel, Field, field_validator
from typing import Optional
import logging
import sys
import json
from datetime import date
from pathlib import Path

# === Definir rutas del proyecto ===
project_root = Path(__file__).resolve().parent.parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# === Forzar encoding UTF-8 para logs en Windows ===
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# === Logging configurado con encoding explícito ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(
            project_root / "logs" / "ingesta_api.log",
            encoding="utf-8",
            mode="a"
        ),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# === Importar configuración y funciones después de setup de paths ===
from utils.config import settings
from utils.loader import insertar_informe

# === Inicializar FastAPI ===
app = FastAPI(title="AgroIA Ingesta API", version="2.0")

# === Modelo de datos de entrada (COMPATIBLE CON V2) ===
class InformeAgricola(BaseModel):
    # Campos nuevos (prioritarios)
    lote_id: str = Field(..., min_length=1, max_length=100)
    fecha: str = Field(..., description="Fecha ISO 8601: YYYY-MM-DD")
    ndvi_promedio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    gdd_acumulados: Optional[float] = Field(default=None, ge=0.0)
    contenido_tecnico: Optional[str] = None
    metadata: Optional[dict] = None
    historial_anos: Optional[list] = None # ASCII ONLY

    # Campos legacy (para compatibilidad con payloads antiguos)
    ndvi: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    gdd: Optional[float] = Field(default=None, ge=0.0)
    precip: Optional[float] = 0.0
    estabilidad: Optional[float] = 0.0
    texto_informe: Optional[str] = None
    meta: Optional[dict] = None

    # ── Validación de formato de fecha ────────────────────────────────────────
    @field_validator("fecha")
    @classmethod
    def fecha_debe_ser_iso(cls, v: str) -> str:
        """Rechaza cualquier formato que no sea YYYY-MM-DD antes de tocar la BD."""
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(
                f"Formato de fecha inválido: '{v}'. Se requiere YYYY-MM-DD "
                f"(ej: '2026-04-16')."
            )
        return v

    # ── Normalización del payload ─────────────────────────────────────────────
    def to_payload(self) -> dict:
        """Unifica campos v2 y legacy en un dict estándar para el loader."""
        meta_final = self.metadata or self.meta or {}
        hist_final = self.historial_anos or []
        
        return {
            "lote_id": self.lote_id,
            "fecha": self.fecha,
            "ndvi_promedio": self.ndvi_promedio or self.ndvi or 0.0,
            "gdd_acumulados": self.gdd_acumulados or self.gdd or 0.0,
            "contenido_tecnico": self.contenido_tecnico or self.texto_informe or "",
            "metadata": meta_final,
            "historial_anos": hist_final,
        }

# === Health check ===
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "db": settings.db_name,
        "embedding_model": settings.embedding_model,
        "version": "2.0"
    }

# === Endpoint principal de ingesta (async con BackgroundTasks) ===
@app.post("/ingesta")
async def recibir_informe(
    informe: InformeAgricola,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None)
):
    if authorization != f"Bearer {settings.ingesta_secret_key}":
        logger.warning("[WARN] Intento de acceso no autorizado")
        raise HTTPException(status_code=401, detail="No autorizado")

    print(f"   [DEBUG_API] Historial recibido en Pydantic: {len(informe.historial_anos) if informe.historial_anos else 'None'}")
    payload = informe.to_payload()
    print(f"   [DEBUG_API] Historial en payload normalizado: {len(payload['historial_anos'])}")

    def procesar():
        try:
            logger.info(f"[INFO] Procesando: {payload['lote_id']}")
            new_id = insertar_informe(payload)
            logger.info(f"[OK] Guardado: {payload['lote_id']} | ID: {new_id}")
        except Exception as e:
            logger.error(f"[ERROR] {type(e).__name__}: {e}", exc_info=True)

    background_tasks.add_task(procesar)
    return {"status": "accepted", "lote_id": informe.lote_id, "fecha": informe.fecha}

# === Endpoint de Ingesta MASIVA GeoJSON (async) ===
@app.post("/ingesta/geojson")
async def recibir_geojson(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None)
):
    if authorization != f"Bearer {settings.ingesta_secret_key}":
        logger.warning("[WARN] Intento de acceso no autorizado al endpoint GeoJSON")
        raise HTTPException(status_code=401, detail="No autorizado")

    try:
        content = await file.read()
        geojson_data = json.loads(content)
        features = geojson_data.get("features", [])
        
        if not features:
            return {"status": "error", "message": "El GeoJSON no contiene features"}

        def procesar_masivo(features_list):
            logger.info(f"[INFO] Iniciando procesamiento masivo de {len(features_list)} features")
            exitos = 0
            errores = 0
            for feat in features_list:
                try:
                    props = feat.get("properties", {})
                    # Mapeo de campos GeoJSON -> InformeAgricola
                    lote_id = f"POLIGONO_{props.get('id', 'S_ID')}"
                    
                    payload = {
                        "lote_id": lote_id,
                        "fecha": date.today().isoformat(),
                        "ndvi_promedio": 0.0,
                        "gdd_acumulados": 0.0,
                        "contenido_tecnico": f"Ingesta masiva desde GeoJSON. Localidad: {props.get('localidad', 'N/A')}",
                        "metadata": props,
                        "historial_anos": [],
                    }
                    
                    insertar_informe(payload)
                    exitos += 1
                except Exception as e:
                    logger.error(f"[ERROR_BULK] Error procesando feature {feat.get('id')}: {e}")
                    errores += 1
            
            logger.info(f"[OK] Fin procesamiento masivo. Exitos: {exitos}, Errores: {errores}")

        background_tasks.add_task(procesar_masivo, features)
        
        return {
            "status": "accepted", 
            "message": "Archivo GeoJSON recibido y en proceso de carga masiva",
            "total_features": len(features)
        }

    except Exception as e:
        logger.error(f"[ERROR_UPLOAD] Fallo al leer GeoJSON: {e}")
        raise HTTPException(status_code=400, detail="Archivo GeoJSON inválido")

# === Endpoint DEBUG: inserción síncrona para testing ===
@app.post("/ingesta/debug")
async def recibir_informe_debug(
    informe: InformeAgricola,
    authorization: Optional[str] = Header(None)
):
    if authorization != f"Bearer {settings.ingesta_secret_key}":
        raise HTTPException(status_code=401, detail="No autorizado")
    
    try:
        payload = informe.to_payload()
        logger.info(f"[INFO] Inserción síncrona: {payload['lote_id']}")
        new_id = insertar_informe(payload)
        logger.info(f"[OK] ID: {new_id}")
        return {"status": "ok", "id": new_id, "lote_id": informe.lote_id}
    except Exception as e:
        logger.error(f"[ERROR] {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")

# === Endpoint para consultar lotes ===
@app.get("/lotes/{lote_id}")
async def consultar_lote(lote_id: str, limit: int = 5):
    """Devuelve el informe consolidado + historial de un lote."""
    import psycopg2
    conn = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,   # ← psycopg2 usa dbname=, no database=
        user=settings.db_user,
        password=settings.db_password,
    )
    try:
        with conn.cursor() as cur:
            # Informe consolidado
            cur.execute("""
                SELECT id, fecha, ndvi_promedio, gdd_acumulados,
                       score_total, cv_espacial, cultivo, superficie_ha, metadata
                FROM informes_lotes
                WHERE lote_id = %s
            """, (lote_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' no encontrado.")
            informe = {
                "id": row[0], "fecha": str(row[1]),
                "ndvi_promedio": row[2], "gdd_acumulados": row[3],
                "score_total": row[4], "cv_espacial": row[5],
                "cultivo": row[6], "superficie_ha": row[7],
                "metadata": row[8],
            }
            # Historial por año
            cur.execute("""
                SELECT anio, cultivo, ndvi_critico, horas_calor,
                       score_total, score_vigor, score_estabilidad,
                       score_limpieza, score_clima, valido_para_score
                FROM lote_historial
                WHERE lote_id = %s
                ORDER BY anio
                LIMIT %s
            """, (lote_id, limit))
            historial = [
                {
                    "anio": r[0], "cultivo": r[1], "ndvi_critico": r[2],
                    "horas_calor": r[3], "score_total": r[4],
                    "score_vigor": r[5], "score_estabilidad": r[6],
                    "score_limpieza": r[7], "score_clima": r[8],
                    "valido_para_score": r[9],
                }
                for r in cur.fetchall()
            ]
        return {"lote_id": lote_id, "informe": informe, "historial": historial}
    finally:
        conn.close()


# === Endpoint para borrar un lote específico ===
@app.delete("/lotes/{lote_id}")
async def borrar_lote(lote_id: str, authorization: Optional[str] = Header(None)):
    """Elimina un lote de informes_lotes y su historial completo en lote_historial."""
    if authorization != f"Bearer {settings.ingesta_secret_key}":
        raise HTTPException(status_code=401, detail="No autorizado")
    import psycopg2
    conn = psycopg2.connect(
        host=settings.db_host, port=settings.db_port, dbname=settings.db_name,
        user=settings.db_user, password=settings.db_password,
    )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM lote_historial WHERE lote_id = %s", (lote_id,))
                filas_hist = cur.rowcount
                cur.execute("DELETE FROM informes_lotes WHERE lote_id = %s", (lote_id,))
                filas_inf = cur.rowcount
        if filas_inf == 0:
            raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' no encontrado.")
        logger.info(f"[OK] Lote borrado: {lote_id} ({filas_hist} registros históricos)")
        return {"status": "deleted", "lote_id": lote_id, "historial_eliminado": filas_hist}
    finally:
        conn.close()


# === Endpoint para borrar TODOS los lotes ===
@app.delete("/lotes")
async def borrar_todos_los_lotes(authorization: Optional[str] = Header(None)):
    """Vacía completamente informes_lotes y lote_historial. Irreversible."""
    if authorization != f"Bearer {settings.ingesta_secret_key}":
        raise HTTPException(status_code=401, detail="No autorizado")
    import psycopg2
    conn = psycopg2.connect(
        host=settings.db_host, port=settings.db_port, dbname=settings.db_name,
        user=settings.db_user, password=settings.db_password,
    )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM informes_lotes")
                total = cur.fetchone()[0]
                cur.execute("DELETE FROM lote_historial")
                cur.execute("DELETE FROM informes_lotes")
        logger.info(f"[OK] Base de datos limpiada: {total} lote(s) eliminados")
        return {"status": "cleared", "lotes_eliminados": total}
    finally:
        conn.close()


# === Endpoint para listar todos los lotes ===
@app.get("/lotes")
async def listar_lotes():
    """Devuelve todos los lote_id registrados en informes_lotes."""
    import psycopg2
    conn = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT lote_id, cultivo, score_total, ndvi_promedio, fecha, superficie_ha
                FROM informes_lotes
                ORDER BY lote_id
            """)
            rows = cur.fetchall()
        return {
            "total": len(rows),
            "lotes": [
                {
                    "lote_id":      r[0],
                    "cultivo":      r[1],
                    "score_total":  r[2],
                    "ndvi_promedio": r[3],
                    "fecha":        str(r[4]) if r[4] else None,
                    "superficie_ha": r[5],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()