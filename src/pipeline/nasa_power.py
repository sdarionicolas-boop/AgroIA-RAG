import requests
import pandas as pd
import numpy as np

def estimate_hours_over_threshold(tmax, tmin, umbral):
    """Estima horas sobre un umbral térmico usando interpolación sinusoidal."""
    if tmax <= umbral:
        return 0.0
    if tmin >= umbral:
        return 24.0
    tmean = (tmax + tmin) / 2.0
    tamp  = (tmax - tmin) / 2.0
    x = np.clip((umbral - tmean) / tamp, -1.0, 1.0)
    horas = (24.0 / np.pi) * np.arccos(x)
    return round(horas, 2)

def get_nasa_climate_safe(lat, lon, year, cultivo_conf):
    """Obtiene datos climáticos de NASA POWER y calcula horas de estrés."""
    mes    = cultivo_conf['mes_critico']
    umbral = cultivo_conf['umbral_calor']
    end_month = "0430" if mes <= 6 else "1130"
    start = f"{year}0101"
    end   = f"{year}{end_month}"
    url = (f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M_MAX,T2M_MIN&community=AG"
           f"&longitude={lon}&latitude={lat}&start={start}&end={end}&format=JSON")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        tmax_d = data['properties']['parameter']['T2M_MAX']
        tmin_d = data['properties']['parameter']['T2M_MIN']
        df = pd.DataFrame({'date': pd.to_datetime(list(tmax_d.keys()), format='%Y%m%d'),
                           'Tmax': list(tmax_d.values()), 'Tmin': list(tmin_d.values())})
        df = df[(df['Tmax'] > -900) & (df['Tmin'] > -900)].copy()
        df_mes = df[df['date'].dt.month == mes]
        if df_mes.empty: return 0.0, 'no_data'
        df_mes['horas'] = df_mes.apply(lambda row: estimate_hours_over_threshold(row['Tmax'], row['Tmin'], umbral), axis=1)
        return round(df_mes['horas'].sum(), 2), 'ok'
    except Exception as e:
        return 0.0, f'error: {e}'
