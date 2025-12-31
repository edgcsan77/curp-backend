# -*- coding: utf-8 -*-
import random
import calendar
import re
import unicodedata
import csv
import os
from datetime import datetime, date

import requests

#from selenium import webdriver
#from selenium.webdriver.common.by import By
#from selenium.webdriver.chrome.service import Service
#from selenium.webdriver.support.ui import WebDriverWait
#from selenium.webdriver.support import expected_conditions as EC
#from webdriver_manager.chrome import ChromeDriverManager
#from selenium.webdriver.common.keys import Keys

import osmnx as ox
import json

# ============================================================
#  OSM: direcciones reales (calle + número + CP)
#  100% reales, sin inventar número exterior
# ============================================================
def to_osm_estado(entidad_curp):
    """
    Convierte el texto de entidad que viene del CURP
    al nombre que usa OSM para el estado.
    """
    base = normalizar_clave(entidad_curp)

    mapping = {
        # CDMX
        "CIUDAD DE MEXICO": "Ciudad de México",
        "CDMX": "Ciudad de México",
        "DISTRITO FEDERAL": "Ciudad de México",

        # Estado de México
        "ESTADO DE MEXICO": "Estado de México",
        "MEXICO": "Estado de México",

        # 🔹 Veracruz
        "VERACRUZ": "Veracruz",
        "VERACRUZ DE IGNACIO DE LA LLAVE": "Veracruz",
        "VERACRUZ LLAVE": "Veracruz",
        "VERACRUZ-LLAVE": "Veracruz",

        # Aquí puedes ir agregando más equivalencias si ves raro en otros estados
        # "MICHOACAN DE OCAMPO": "Michoacán",
        # "COAHUILA DE ZARAGOZA": "Coahuila",
    }

    if base in mapping:
        return mapping[base]

    # En la mayoría de casos, .title() funciona bien: TAMAULIPAS -> Tamaulipas
    return entidad_curp.title()


def to_osm_municipio(mnpio_curp):
    """
    Convierte el municipio del CURP (REYNOSA, SAN NICOLAS DE LOS GARZA, etc.)
    a algo razonable para buscar en OSM.
    """
    return mnpio_curp.title()


def descargar_direcciones_osm(entidad_registro, municipio_registro, max_resultados=3000):
    """
    Descarga direcciones reales desde OSM para el municipio dado:
      - addr:street
      - addr:housenumber
      - addr:postcode
      - colonia opcional (suburb / neighbourhood / district / quarter / locality)

    PERO ahora:

      ✅ Limitando la búsqueda al bounding box del municipio en México.
      ✅ Filtrando por addr:country="MX" para evitar que se cuele Texas, etc.
    """
    estado_osm = to_osm_estado(entidad_registro)
    municipio_osm = to_osm_municipio(municipio_registro)

    print(f"[OSM] Descargando direcciones para {municipio_osm}, {estado_osm}...")

    # 1) Conseguir bounding box del municipio con OSMnx (Nominatim)
    lugar_mpio = f"{municipio_osm}, {estado_osm}, Mexico"
    try:
        gdf = ox.geocode_to_gdf(lugar_mpio)
        if gdf.empty:
            raise ValueError("GeoDataFrame vacío")

        polygon = gdf.geometry.iloc[0]
        # polygon.bounds = (minx, miny, maxx, maxy) = (west, south, east, north)
        west, south, east, north = polygon.bounds
        print(f"[OSM] Bounding box municipio: S={south}, W={west}, N={north}, E={east}")
    except Exception as e:
        print(f"[OSM] Error geocodificando municipio para bounding box: {e}")
        # Si no logramos sacar bbox, devolvemos vacío para que entre el fallback
        return []

    # 2) Query Overpass limitada al bbox y a México (addr:country="MX")
    query = f"""
    [out:json][timeout:120];
    (
      node
        ["addr:housenumber"]
        ["addr:street"]
        ["addr:country"="MX"]
        ({south},{west},{north},{east});
    );
    out tags;
    """

    data = llamar_overpass(query, timeout=180, max_reintentos=2)
    if data is None:
        print("[OSM] Ningún servidor Overpass respondió, devolviendo lista vacía para activar el fallback.")
        return []

    domicilios = []

    for element in data.get("elements", []):
        tags = element.get("tags", {})
        calle = tags.get("addr:street")
        numero = tags.get("addr:housenumber")
        cp = tags.get("addr:postcode")

        if not (calle and numero and cp):
            continue  # sin estos 3 no nos sirve

        colonia = (
            tags.get("addr:suburb")
            or tags.get("addr:neighbourhood")
            or tags.get("addr:district")
            or tags.get("addr:quarter")
            or tags.get("addr:locality")
        )

        numero_int = tags.get("addr:unit") or None

        domicilios.append(
            {
                "nombre_vialidad": str(calle).strip().upper(),
                "numero_exterior": str(numero).strip(),
                "numero_interior": str(numero_int).strip().upper() if numero_int else None,
                "cp": re.sub(r"\D", "", str(cp)).zfill(5),
                "colonia": colonia.strip().upper() if colonia else None,
            }
        )

        if len(domicilios) >= max_resultados:
            break

    print(f"[OSM] Domicilios crudos obtenidos: {len(domicilios)}")
    for i, d in enumerate(domicilios[:5], start=1):
        print(
            f"[OSM] Ejemplo {i}: "
            f"calle={d['nombre_vialidad']}, "
            f"num={d['numero_exterior']}, "
            f"cp={d['cp']}, "
            f"colonia={d['colonia']}"
        )

    return domicilios

def inferir_tipo_vialidad_por_nombre(nombre_vialidad):
    nombre = normalizar(nombre_vialidad)

    if nombre.startswith(("AV ", "AVENIDA ")):
        return "AVENIDA"
    if nombre.startswith(("BLVD ", "BOULEVARD ")):
        return "BOULEVARD"
    if nombre.startswith(("CALZ ", "CALZADA ")):
        return "CALZADA"
    if nombre.startswith(("CARRETERA ", "CTRA ")):
        return "CARRETERA"
    if nombre.startswith(("PROL ", "PROLONGACION ")):
        return "PROLONGACION"
    if nombre.startswith(("ANDADOR ",)):
        return "ANDADOR"
    if nombre.startswith(("CERRADA ",)):
        return "CERRADA"
    if nombre.startswith(("CIRCUITO ",)):
        return "CIRCUITO"
    if nombre.startswith(("RETORNO ",)):
        return "RETORNO"
    if nombre.startswith(("PASEO ",)):
        return "PASEO"
    if nombre.startswith(("VIADUCTO ",)):
        return "VIADUCTO"
    if nombre.startswith(("PERIFERICO ",)):
        return "PERIFERICO"
    if nombre.startswith(("LIBRAMIENTO ",)):
        return "LIBRAMIENTO"
    if nombre.startswith(("AUTOPISTA ",)):
        return "AUTOPISTA"
    if nombre.startswith(("CAMINO ",)):
        return "CAMINO"

    return "CALLE"

def es_nombre_vialidad_urbano(nombre_vialidad):
    """
    Devuelve True si el nombre de la vialidad suena a calle/avenida urbana típica
    y False si parece tramo carretero, ramal, brecha, etc.
    """
    if not nombre_vialidad:
        return False

    n = normalizar(nombre_vialidad)

    # Palabras "sospechosas" de carretera / rural / tramo técnico
    palabras_baneadas = [
        "RAMAL",
        "TRAMO",
        "ENTRONQUE",
        "LIBRAMIENTO",
        "CUOTA",
        "AUTOPISTA",
        "PERIFERICO",
        "CARRETERA",
        "BRECHA",
        "VEREDA",
        "KM",
        "KILOMETRO",
    ]

    if any(p in n for p in palabras_baneadas):
        return False

    # Muchos nombres con guion "-" suelen ser tramos tipo "X - Y"
    # (no todos, pero bajan credibilidad)
    if "-" in n and len(n) > 25:
        return False

    # Si el nombre es exageradamente largo, también huele a tramo
    if len(n) > 40:
        return False

    # Al revés: nombres cortos/clásicos son muy urbanos
    # HIDALGO, BENITO JUAREZ, 20 DE NOVIEMBRE, etc. -> siempre OK
    return True

def generar_direccion_real(entidad_registro, municipio_registro,
                           ruta_sepomex="sepomex.csv",
                           permitir_fallback=True):
    """
    Dirección 100% REAL:
      - Calle y número exterior de OSM (addr:street + addr:housenumber)
      - CP de OSM validado contra SEPOMEX
      - Colonia tomada de OSM si machea, si no, se elige una colonia válida de SEPOMEX para ese CP.

    Si no hay nada usable en OSM:
      - Si permitir_fallback=True → usa generar_direccion() como plan B (números simulados).
      - Si permitir_fallback=False → lanza RuntimeError.
    """

    # 1) Obtener domicilios de OSM (ya limitados a México y al municipio)
    domicilios = descargar_direcciones_osm(entidad_registro, municipio_registro)

    if not domicilios:
        msg = "[OSM] No hay direcciones con calle+número+CP en OSM para ese municipio."
        print(msg)
        if permitir_fallback:
            print("[OSM] Usando generar_direccion() como respaldo (número simulado).")
            return generar_direccion(entidad_registro, municipio_registro, ruta_sepomex)
        else:
            raise RuntimeError(msg)

    # 2) Preparar índice SEPOMEX CP -> colonias para ese municipio/estado
    cargar_sepomex(ruta_sepomex)

    estado_clave = normalizar_estado_sepomex(entidad_registro)
    mnpio_clave = normalizar_clave(municipio_registro)
    clave = (estado_clave, mnpio_clave)

    lista_sep = SEPOMEX_IDX.get(clave, [])
    if not lista_sep:
        print("[SEPOMEX] No hay entradas para ese municipio/estado.")
        if permitir_fallback:
            return generar_direccion(entidad_registro, municipio_registro, ruta_sepomex)
        else:
            raise RuntimeError("SEPOMEX no tiene datos para ese municipio/estado.")

    colonias_por_cp = {}
    for r in lista_sep:
        cp = r["cp"]
        col = r["colonia"]
        colonias_por_cp.setdefault(cp, set()).add(col)

    # 3) Cruzar OSM <-> SEPOMEX
    candidatos = []

    for d in domicilios:
        cp = d["cp"]
        colonias_sep = colonias_por_cp.get(cp)

        if not colonias_sep:
            print(f"[OSM+SEPOMEX] CP {cp} de OSM no existe en SEPOMEX para {estado_clave}/{mnpio_clave}")
            continue

        colonia_final = None

        if d["colonia"]:
            # Intentar macheo directo con la colonia OSM normalizada
            col_osm = d["colonia"].strip().upper()
            if col_osm in colonias_sep:
                colonia_final = col_osm

        if not colonia_final:
            # Si OSM no trae colonia o no machea, elegimos una colonia válida para ese CP
            colonia_final = random.choice(list(colonias_sep))

        tipo_vialidad = inferir_tipo_vialidad_por_nombre(d["nombre_vialidad"])
        numero_int = d["numero_interior"] or "S/N"

        candidatos.append(
            {
                "colonia": colonia_final,
                "tipo_vialidad": tipo_vialidad,
                "nombre_vialidad": d["nombre_vialidad"],
                "numero_exterior": d["numero_exterior"],
                "numero_interior": numero_int,
                "cp": cp,
            }
        )

    print(f"[OSM+SEPOMEX] Candidatos tras cruce: {len(candidatos)}")

    # 👇 Filtrado "urbano" tipo domicilio 2
    candidatos_urbanos = [
        c for c in candidatos
        if es_nombre_vialidad_urbano(c["nombre_vialidad"])
    ]

    candidatos_urbanos_cortos = [
        c for c in candidatos_urbanos
        if len(c["nombre_vialidad"]) <= 25
    ]

    if candidatos_urbanos_cortos:
        print(f"[OSM+SEPOMEX] Candidatos urbanos cortos: {len(candidatos_urbanos_cortos)}")
        candidatos_finales = candidatos_urbanos_cortos
    elif candidatos_urbanos:
        print(f"[OSM+SEPOMEX] Candidatos urbanos filtrados: {len(candidatos_urbanos)}")
        candidatos_finales = candidatos_urbanos
    else:
        print("[OSM+SEPOMEX] Sin candidatos urbanos, usando todos los candidatos.")
        candidatos_finales = candidatos

    if not candidatos_finales:
        msg = "[OSM+SEPOMEX] No quedó ningún domicilio tras cruce CP/colonia."
        print(msg)
        if permitir_fallback:
            return generar_direccion(entidad_registro, municipio_registro, ruta_sepomex)
        else:
            raise RuntimeError(msg)

    # 4) Elegimos UNO al azar (preferentemente urbano)
    elegido = random.choice(candidatos_finales)

    # 🔹 Limpieza final parecida a generar_direccion()
    nombre_vialidad_final = elegido["nombre_vialidad"].strip()
    tipo_vialidad_final = elegido["tipo_vialidad"] or "CALLE"

    # Evitar "CALLE CALLE ..."
    if tipo_vialidad_final == "CALLE" and nombre_vialidad_final.startswith("CALLE "):
        nombre_vialidad_final = nombre_vialidad_final[6:].strip()

    # Números: respetamos el exterior de OSM, pero formateamos interior un poco
    numero_exterior_final = str(elegido["numero_exterior"]).strip()

    num_int = (elegido["numero_interior"] or "").strip()
    if not num_int or num_int.upper() == "S/N":
        r = random.random()
        if r < 0.6:
            numero_interior_final = ""        # mayoría sin interior
        elif r < 0.85:
            numero_interior_final = "S/N"
        else:
            numero_interior_final = str(random.randint(1, 10))
    else:
        numero_interior_final = num_int

    return {
        "colonia": elegido["colonia"],
        "tipo_vialidad": tipo_vialidad_final,
        "nombre_vialidad": nombre_vialidad_final,
        "numero_exterior": numero_exterior_final,
        "numero_interior": numero_interior_final,
        "cp": elegido["cp"],
    }

# ===========================
#  CONSTANTES
# ===========================
URL_CURP = "https://www.gob.mx/curp/"
URL_RFC = "https://taxdown.com.mx/rfc/como-sacar-rfc-homoclave"
SITUACION_CONTRIBUYENTE = "ACTIVO"
REGIMEN = "Régimen de Sueldos y Salarios e Ingresos Asimilados a Salarios"

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

def llamar_overpass(query, timeout=180, max_reintentos=2):
    for url in OVERPASS_URLS:
        for intento in range(max_reintentos):
            try:
                print(f"[OSM] Consultando Overpass: {url} (intento {intento+1})")
                resp = requests.post(url, data={"data": query}, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                print(f"[OSM] Error con {url}: {e}")
    return None

# ===========================
#  NORMALIZADORES GENERALES
# ===========================
def normalizar(texto):
    """
    Mayúsculas + quitar acentos. Se usa para OSM/calles.
    """
    if not texto:
        return ""
    txt = texto.strip().upper()
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return txt

def normalizar_clave(texto):
    """
    Normaliza cadenas para empatar entre gob.mx, SEPOMEX y calles:
    - Mayúsculas
    - Quita acentos
    - Colapsa espacios
    """
    if not texto:
        return ""
    txt = texto.strip().upper()
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    txt = re.sub(r"\s+", " ", txt)
    return txt

def solo_letras(texto):
    """
    Elimina números, signos y deja solo letras y espacios.
    Mantiene acentos.
    """
    if not texto:
        return ""
    texto = texto.strip()
    texto = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip().upper()

def formatear_entidad_salida(entidad):
    """
    Formatea la entidad para mostrarla bonita en la salida.
    Para Veracruz, siempre devuelve 'Veracruz de Ignacio de la Llave'.
    El resto las deja tal cual o en mayúsculas según vengan.
    """
    base = normalizar_clave(entidad)

    if base in (
        "VERACRUZ",
        "VERACRUZ DE IGNACIO DE LA LLAVE",
        "VERACRUZ LLAVE",
        "VERACRUZ-LLAVE",
    ):
        return "VERACRUZ DE IGNACIO DE LA LLAVE"

    # Si quieres, aquí podrías hacer casos especiales para CDMX, etc.
    # if base == "CIUDAD DE MEXICO":
    #     return "Ciudad de México"

    # Por defecto, regresa tal cual viene
    return entidad

# ============================================================
#  FECHAS Y CURP
# ============================================================
def generar_fechas(fecha_nac_str):
    """
    Recibe fecha de nacimiento en formato DD/MM/AAAA (como viene en gob.mx)
    y regresa:
        - fecha_nac (date real)
        - fecha_inicio (date): año = año_nac + 18, día y mes random válidos
    """
    fecha_nac = datetime.strptime(fecha_nac_str.strip(), "%d/%m/%Y").date()

    anio_inicio = fecha_nac.year + 18
    mes = random.randint(1, 12)
    ultimo_dia = calendar.monthrange(anio_inicio, mes)[1]
    dia = random.randint(1, ultimo_dia)

    fecha_inicio = date(anio_inicio, mes, dia)
    return fecha_nac, fecha_inicio

def formatear_dd_mm_aaaa(fecha_obj):
    return fecha_obj.strftime("%d-%m-%Y")

def consultar_curp(curp):
    raise RuntimeError(
        "consultar_curp() ya no usa Selenium. "
        "Ahora los datos del CURP deben venir en el request."
    )

def calcular_rfc_taxdown(nombre, apellido_paterno, apellido_materno, fecha_nac):
    raise RuntimeError(
        "calcular_rfc_taxdown() ya no usa Selenium. "
        "Envía el RFC calculado en el campo 'rfc' del request."
    )

# ============================================================
#  SEPOMEX: índices para colonia/CP por estado y municipio
# ============================================================
SEPOMEX_IDX = {}
SEPOMEX_CARGADO = False

def normalizar_estado_sepomex(nombre_estado):
    """
    Normaliza el nombre del estado y aplica equivalencias
    especiales para empatar:
      - gob.mx (Entidad de registro)
      - SEPOMEX (d_estado)
    Siempre regresa una clave canónica en MAYÚSCULAS sin acentos.
    """
    base = normalizar_clave(nombre_estado)  # ya viene en MAYUS y sin acentos

    equivalencias = {
        # ============ NORTE ============
        "AGUASCALIENTES": "AGUASCALIENTES",

        "BAJA CALIFORNIA": "BAJA CALIFORNIA",
        "BC": "BAJA CALIFORNIA",

        "BAJA CALIFORNIA SUR": "BAJA CALIFORNIA SUR",
        "BCS": "BAJA CALIFORNIA SUR",

        "CHIHUAHUA": "CHIHUAHUA",

        "COAHUILA": "COAHUILA DE ZARAGOZA",
        "COAHUILA DE ZARAGOZA": "COAHUILA DE ZARAGOZA",

        "DURANGO": "DURANGO",

        "NUEVO LEON": "NUEVO LEON",
        "NL": "NUEVO LEON",

        "TAMAULIPAS": "TAMAULIPAS",

        "SONORA": "SONORA",

        "SINALOA": "SINALOA",

        "BAJA CALIFORNIA NORTE": "BAJA CALIFORNIA",  # por si acaso

        # ============ OCCIDENTE / BAJIO ============
        "JALISCO": "JALISCO",

        "GUANAJUATO": "GUANAJUATO",

        "COLIMA": "COLIMA",

        "MICHOACAN": "MICHOACAN DE OCAMPO",
        "MICHOACAN DE OCAMPO": "MICHOACAN DE OCAMPO",

        "NAYARIT": "NAYARIT",

        "ZACATECAS": "ZACATECAS",

        "AGUASCALIENTE": "AGUASCALIENTES",  # errores típicos

        # ============ CENTRO ============
        # CDMX / DF
        "CIUDAD DE MEXICO": "CIUDAD DE MEXICO",
        "CDMX": "CIUDAD DE MEXICO",
        "DISTRITO FEDERAL": "CIUDAD DE MEXICO",
        "DF": "CIUDAD DE MEXICO",

        # Estado de México
        "MEXICO": "MEXICO",
        "ESTADO DE MEXICO": "MEXICO",
        "EDO DE MEXICO": "MEXICO",
        "EDOMEX": "MEXICO",

        "HIDALGO": "HIDALGO",
        "HIDALGO DE OCAMPO": "HIDALGO",

        "MORELOS": "MORELOS",

        "TLAXCALA": "TLAXCALA",
        "TLAXCALA DE XICOHTENCATL": "TLAXCALA",

        "PUEBLA": "PUEBLA",
        "PUEBLA DE ZARAGOZA": "PUEBLA",

        "QUERETARO": "QUERETARO",
        "QUERETARO DE ARTEAGA": "QUERETARO",

        # ============ SUR / SURESTE ============
        "OAXACA": "OAXACA",

        "CHIAPAS": "CHIAPAS",

        "GUERRERO": "GUERRERO",

        "CAMPECHE": "CAMPECHE",

        "TABASCO": "TABASCO",

        "QUINTANA ROO": "QUINTANA ROO",

        "YUCATAN": "YUCATAN",

        # ============ GOLFO / ORIENTE ============
        "VERACRUZ": "VERACRUZ DE IGNACIO DE LA LLAVE",
        "VERACRUZ DE IGNACIO DE LA LLAVE": "VERACRUZ DE IGNACIO DE LA LLAVE",
        "VERACRUZ LLAVE": "VERACRUZ DE IGNACIO DE LA LLAVE",
        "VERACRUZ-LLAVE": "VERACRUZ DE IGNACIO DE LA LLAVE",

        "SAN LUIS POTOSI": "SAN LUIS POTOSI",

        # ============ OTROS ============
        "CAMPECHE": "CAMPECHE",
        "YUCATAN": "YUCATAN",
    }

    if base in equivalencias:
        return equivalencias[base]

    # Si no está en la tabla, regresamos base tal cual;
    # como ya está normalizado, seguirá funcionando si coincide exacto.
    return base

def cargar_sepomex(ruta_csv="sepomex.csv"):
    """
    Carga el catálogo SEPOMEX desde un CSV y arma un índice:
        SEPOMEX_IDX[(ESTADO, MUNICIPIO)] = [ {cp, colonia}, ... ]
    Se carga solo una vez por ejecución.
    """
    global SEPOMEX_IDX, SEPOMEX_CARGADO
    if SEPOMEX_CARGADO:
        return

    SEPOMEX_IDX = {}

    with open(ruta_csv, "r", encoding="latin-1", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            estado_raw = row.get("d_estado", "")
            mnpio_raw = row.get("D_mnpio", "")
            colonia_raw = row.get("d_asenta", "")
            cp_raw = row.get("d_codigo", "")

            if not (estado_raw and mnpio_raw and colonia_raw and cp_raw):
                continue

            estado = normalizar_estado_sepomex(estado_raw)
            mnpio = normalizar_clave(mnpio_raw)
            colonia = colonia_raw.strip().upper()

            cp = cp_raw.strip()
            if "." in cp:
                cp = cp.split(".")[0]
            cp = re.sub(r"\D", "", cp)
            if cp:
                cp = cp.zfill(5)

            clave = (estado, mnpio)
            SEPOMEX_IDX.setdefault(clave, []).append(
                {
                    "cp": cp,
                    "colonia": colonia,
                }
            )

    SEPOMEX_CARGADO = True

# ============================================================
#  OSM: COLONIA → CALLES REALES (OSMNX + NOMINATIM)
# ============================================================
CALLES_CACHE = {}  # (estado_clave, mnpio_clave, colonia_norm) -> lista de calles

def mapear_highway_a_tipo_vialidad(highway):
    highway = str(highway)
    tipo_vialidad = "CALLE"
    if highway in ("primary", "secondary", "tertiary", "trunk"):
        tipo_vialidad = "AVENIDA"
    elif highway in ("motorway",):
        tipo_vialidad = "CARRETERA"
    elif highway in ("service",):
        tipo_vialidad = "PRIVADA"
    elif highway in ("footway", "path"):
        tipo_vialidad = "ANDADOR"
    return tipo_vialidad

def obtener_calles_osm_colonia(entidad, municipio, colonia):
    lugar_colonia = f"{colonia}, {municipio}, {entidad}, Mexico"
    print(f"[OSM] Buscando colonia: {lugar_colonia}")

    try:
        gdf = ox.geocode_to_gdf(lugar_colonia)
        print(f"[OSM] Geocodificación colonia OK, {len(gdf)} resultado(s)")
    except Exception as e:
        print(f"[OSM] Error geocodificando colonia: {e}")
        # Fallback: usar polígono del municipio completo
        try:
            lugar_mpio = f"{municipio}, {entidad}, Mexico"
            print(f"[OSM] Intentando municipio: {lugar_mpio}")
            gdf = ox.geocode_to_gdf(lugar_mpio)
            print(f"[OSM] Geocodificación municipio OK, {len(gdf)} resultado(s)")
        except Exception as e2:
            print(f"[OSM] Error geocodificando municipio: {e2}")
            return []

    if gdf.empty:
        print("[OSM] GeoDataFrame vacío")
        return []

    polygon = gdf.geometry.iloc[0]

    try:
        print("[OSM] Descargando red vial (graph_from_polygon)...")
        G = ox.graph_from_polygon(polygon, network_type="drive")
    except Exception as e:
        print(f"[OSM] Error en graph_from_polygon: {e}")
        return []

    edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
    print(f"[OSM] Edges descargados: {len(edges)}")

    if "name" not in edges.columns:
        print("[OSM] La columna 'name' no existe en edges")
        return []

    resultados = []
    colonia_norm = normalizar_clave(colonia)
    vistos = set()

    for _, row in edges.iterrows():
        name = row.get("name")
        if not name:
            continue

        # 👇 Si viene como lista (varios nombres), nos quedamos con el primero no vacío
        if isinstance(name, (list, tuple, set)):
            candidates = [str(n).strip() for n in name if str(n).strip()]
            if not candidates:
                continue
            name_str = candidates[0]
        else:
            name_str = str(name).strip()

        if not name_str:
            continue

        highway = row.get("highway")
        if isinstance(highway, (list, tuple, set)):
            hw_list = list(highway)
        else:
            hw_list = [highway]

        for hw in hw_list:
            if hw is None:
                continue

            tipo_vialidad = mapear_highway_a_tipo_vialidad(hw)
            nombre_norm = normalizar(name_str)

            clave_vista = (nombre_norm, tipo_vialidad)
            if clave_vista in vistos:
                continue
            vistos.add(clave_vista)

            resultados.append(
                {
                    "colonia": colonia_norm,
                    "tipo_vialidad": tipo_vialidad,
                    "nombre_vialidad": nombre_norm,
                }
            )

    print(f"[OSM] Calles encontradas: {len(resultados)}")
    return resultados

def obtener_o_elegir_calle(entidad_registro, municipio_registro, colonia):
    """
    Intenta obtener una calle real de OSM para la colonia dada.
    Usa cache en memoria para no repetir descargas.
    """
    estado_clave = normalizar_estado_sepomex(entidad_registro)
    mnpio_clave = normalizar_clave(municipio_registro)
    colonia_norm = normalizar_clave(colonia)
    clave = (estado_clave, mnpio_clave, colonia_norm)

    if clave not in CALLES_CACHE:
        CALLES_CACHE[clave] = obtener_calles_osm_colonia(
            entidad_registro, municipio_registro, colonia
        )

    lista = CALLES_CACHE[clave]
    if not lista:
        return None

    return random.choice(lista)

# Tipos de vialidad de respaldo (si no hay OSM)
TIPOS_VIALIDAD = [
    "CALLE",
    "AVENIDA",
    "BOULEVARD",
    "BLVD",
    "CALZADA",
    "CARRETERA",
    "CAMINO",
    "ANDADOR",
    "CERRADA",
    "CIRCUITO",
    "RETORNO",
    "VIADUCTO",
    "EJE",
    "EJE VIAL",
    "PERIFERICO",
    "LIBRAMIENTO",
    "PROLONGACION",
    "PASO A DESNIVEL",
    "PASO A NIVEL",
    "BRECHA",
    "VEREDA",
    "CUOTA",
    "AUTOPISTA",
    "DIAGONAL",
    "GLORIETA",
    "PASAJE",
    "PEATONAL",
    "SENDERO",
    "TRAVESIA",
    "VIALIDAD",
    "CORREDOR",
    "MALECON",
    "PAR VIAL",
    "PASEO",
    "ACCESO",
    "ENSEÑADA",
    "TRAMO",
    "ZONA",
    "SECCION",
    "MANZANA",

    # Zonas habitacionales
    "PRIVADA",
    "UNIDAD HABITACIONAL",
    "FRACCIONAMIENTO",

    # Rurales
    "RANCHO",
    "EJIDO",
    "PARCELA",

    # Infraestructura especial
    "NODO VIAL",
    "ENTRONQUE",
    "DISTRIBUIDOR VIAL",
]

NOMBRES_VIALIDAD = [
    # Nombres geográficos
    "HIDALGO",
    "JUAREZ",
    "MORELOS",
    "MADERO",
    "OBREGON",
    "ZARAGOZA",
    "ITURBIDE",
    "REFORMA",
    "INSURGENTES",
    "CONSTITUCION",
    "INDEPENDENCIA",
    "REVOLUCION",
    "BENITO JUAREZ",
    "EMILIANO ZAPATA",
    "VENUSTIANO CARRANZA",
    "FRANCISCO I MADERO",
    "ADOLFO LOPEZ MATEOS",
    "LAZARO CARDENAS",
    "MANUEL AVILA CAMACHO",

    # Coloniales / tradicionales
    "GUERRERO",
    "ALLENDE",
    "ALDAMA",
    "MINA",
    "VICTORIA",
    "MATAMOROS",
    "BRAVO",
    "GALEANA",
    "ALVARADO",
    "HERRERA",
    "ESCOBEDO",
    "TREVIÑO",
    "ZAMORA",
    "SALINAS",
    "RAMIREZ",
    "RODRIGUEZ",
    "ROCHA",

    # Fechas típicas
    "5 DE MAYO",
    "16 DE SEPTIEMBRE",
    "20 DE NOVIEMBRE",
    "1 DE MAYO",
    "18 DE MARZO",
    "24 DE FEBRERO",
    "21 DE MARZO",
    "12 DE OCTUBRE",

    # Lugares naturales
    "LAS PALMAS",
    "LOS PINOS",
    "LAS FLORES",
    "EL ROCIO",
    "EL MIRADOR",
    "LA LOMA",
    "LA SIERRA",
    "EL BOSQUE",
    "EL PARAISO",
    "EL NARANJO",
    "LOS ENCINOS",
    "EL ROBLE",
    "LA CEIBA",

    # Modernos
    "DEL SOL",
    "DEL VALLE",
    "LAS AMERICAS",
    "LOS ARCOS",
    "MONTE CARLO",
    "MONTEBELLO",
    "LOS OLIVOS",
    "RESIDENCIAL DEL NORTE",
    "RESIDENCIAL DEL SUR",
    "TORRES DEL VALLE",
    "PASEOS DEL SOL",

    # Cultura / arte / ciencia
    "SOR JUANA",
    "OCTAVIO PAZ",
    "PANCHO VILLA",
    "NEZAHUALCOYOTL",
    "NETZAHUALCOYOTL",
    "FRIDA KAHLO",
    "DIEGO RIVERA",
    "DAVID ALFARO SIQUEIROS",
    "CARLOS FUENTES",
    "MARIO MOLINA",

    # Nombres industriales / técnicos
    "INDUSTRIAL",
    "COMERCIAL",
    "LOGISTICA",
    "FERROCARRIL",
    "AEROPUERTO",
    "PARQUE INDUSTRIAL",
]

# ============================================================
#  GENERACIÓN DE DIRECCIÓN (SEPOMEX + OSM)
# ============================================================
def generar_direccion(entidad_registro, municipio_registro,
                      ruta_sepomex="sepomex.csv"):
    """
    Genera una dirección CONSISTENTE:
      - COLONIA y CP reales desde SEPOMEX (por estado y municipio).
      - Calle real dentro de esa colonia (OSMnx/Nominatim).
      - Si no hay datos de OSM: usa nombres de calle genéricos.
    """
    # SEPOMEX: colonia + CP
    cargar_sepomex(ruta_sepomex)

    estado_clave = normalizar_estado_sepomex(entidad_registro)
    mnpio_clave = normalizar_clave(municipio_registro)
    clave = (estado_clave, mnpio_clave)

    lista = SEPOMEX_IDX.get(clave)

    if lista:
        eleccion = random.choice(lista)
        cp = eleccion["cp"]
        colonia = eleccion["colonia"]
    else:
        # Fallback si SEPOMEX no tiene nada para ese municipio/estado
        cp = f"{random.randint(10, 99)}{random.randint(0, 9)}{random.randint(0, 9)}{random.randint(0, 9)}"
        colonia = "COLONIA " + str(random.randint(1, 200))

    # Calle real según OSM en esa colonia
    calle = obtener_o_elegir_calle(entidad_registro, municipio_registro, colonia)

    if calle:
        tipo_vialidad = calle["tipo_vialidad"] or random.choice(TIPOS_VIALIDAD)
        nombre_vialidad = calle["nombre_vialidad"]
    else:
        # Fallback si no hay datos de OSM para esa colonia/municipio
        tipo_vialidad = random.choice(TIPOS_VIALIDAD)
        nombre_vialidad = random.choice(NOMBRES_VIALIDAD)

    # 🔹 LIMPIAR "CALLE CALLE ..." (evitar repetir la palabra CALLE)
    nombre_vialidad = nombre_vialidad.strip()
    if tipo_vialidad == "CALLE" and nombre_vialidad.startswith("CALLE "):
        nombre_vialidad = nombre_vialidad[6:].strip()

    # Números de la dirección
    numero_exterior = str(random.randint(100, 999))

    r = random.random()
    if r < 0.6:
        numero_interior_final = ""        
    elif r < 0.85:
        numero_interior_final = "S/N"
    else:
        numero_interior_final = f"{random.randint(1, 10)}"

    return {
        "colonia": colonia,
        "tipo_vialidad": tipo_vialidad,
        "nombre_vialidad": nombre_vialidad,
        "numero_exterior": numero_exterior,
        "numero_interior": numero_interior_final,
        "cp": cp,
    }

def generar_direccion_manual(datos_curp, ruta_sepomex="sepomex.csv"):
    """
    Modo MANUAL / semi-manual:
      - Te pide algunos datos de domicilio.
      - Lo que dejes en blanco se calcula automáticamente,
        pero SIN llamar generar_direccion() (para que no use colonias random).
    Usa:
      datos_curp["entidad_registro"]
      datos_curp["municipio_registro"]
    como valores por defecto.
    """
    print("\n--- Captura MANUAL de domicilio ---")
    print("Deja en blanco lo que quieras que se calcule automáticamente.\n")

    # Entidad y municipio (por defecto, los del CURP)
    entidad_dom = input(
        f"Entidad Federativa [{datos_curp['entidad_registro']}]: "
    ).strip().upper()
    if not entidad_dom:
        entidad_dom = datos_curp["entidad_registro"]

    municipio_dom = input(
        f"Municipio o delegación [{datos_curp['municipio_registro']}]: "
    ).strip().upper()
    if not municipio_dom:
        municipio_dom = datos_curp["municipio_registro"]

    # Campos de domicilio: todos opcionales
    colonia_in = input("Colonia (en blanco = automática según SEPOMEX/OSM): ").strip().upper()
    tipo_vialidad_in = input("Tipo de vialidad (CALLE, AVENIDA, etc., en blanco = automático): ").strip().upper()
    nombre_vialidad_in = input("Nombre de la vialidad (en blanco = automática): ").strip().upper()
    numero_exterior_in = input("Número exterior (en blanco = automático): ").strip().upper()
    numero_interior_in = input("Número interior (en blanco = automático): ").strip().upper()
    cp_in = input("CP (en blanco = automático): ").strip()

    # ==========================
    #  Cargar SEPOMEX
    # ==========================
    cargar_sepomex(ruta_sepomex)

    estado_clave = normalizar_estado_sepomex(entidad_dom)
    mnpio_clave = normalizar_clave(municipio_dom)
    clave = (estado_clave, mnpio_clave)

    lista = SEPOMEX_IDX.get(clave, [])

    colonia_final = colonia_in if colonia_in else None
    cp_final = cp_in if cp_in else None

    # ==========================
    #  Resolver colonia <-> CP
    # ==========================
    if cp_final and not colonia_final:
        # Tengo CP, pero no colonia: buscar colonias reales con ese CP en ese municipio/estado
        candidatos = [r for r in lista if r["cp"] == cp_final]
        if candidatos:
            eleccion = random.choice(candidatos)
            colonia_final = eleccion["colonia"]
        else:
            # CP no encontrado en SEPOMEX para ese municipio: colonia genérica
            colonia_final = "COLONIA " + cp_final
    elif colonia_final and not cp_final:
        # Tengo colonia, pero no CP: buscar CP real para esa colonia
        col_norm = normalizar_clave(colonia_final)
        candidatos = [r for r in lista if normalizar_clave(r["colonia"]) == col_norm]
        if candidatos:
            eleccion = random.choice(candidatos)
            cp_final = eleccion["cp"]
        else:
            # Colonia no encontrada en SEPOMEX: CP aleatorio
            cp_final = f"{random.randint(10, 99)}{random.randint(0, 9)}{random.randint(0, 9)}{random.randint(0, 9)}"
    elif not colonia_final and not cp_final:
        # Ni colonia ni CP: elegir una entrada SEPOMEX real si existe
        if lista:
            eleccion = random.choice(lista)
            colonia_final = eleccion["colonia"]
            cp_final = eleccion["cp"]
        else:
            # No hay datos SEPOMEX para ese municipio/estado
            colonia_final = "COLONIA " + str(random.randint(1, 200))
            cp_final = f"{random.randint(10, 99)}{random.randint(0, 9)}{random.randint(0, 9)}{random.randint(0, 9)}"

    # ==========================
    #  Tipo de vialidad
    # ==========================
    if tipo_vialidad_in:
        tipo_vialidad_final = tipo_vialidad_in
    else:
        if nombre_vialidad_in:
            # Inferir del nombre
            tipo_vialidad_final = inferir_tipo_vialidad_por_nombre(nombre_vialidad_in)
        else:
            # Lo vamos a intentar sacar de OSM si conseguimos calle, si no random
            tipo_vialidad_final = None  # se completará más abajo

    # ==========================
    #  Nombre de la vialidad
    # ==========================
    if nombre_vialidad_in:
        nombre_vialidad_final = nombre_vialidad_in
        # Si no hay tipo_vialidad y hay nombre, inferimos
        if not tipo_vialidad_final:
            tipo_vialidad_final = inferir_tipo_vialidad_por_nombre(nombre_vialidad_in)
    else:
        # No se dio nombre: intentar obtener una calle REAL de OSM para esa colonia
        calle_osm = obtener_o_elegir_calle(entidad_dom, municipio_dom, colonia_final)
        if calle_osm:
            nombre_vialidad_final = calle_osm["nombre_vialidad"]
            if not tipo_vialidad_final:
                tipo_vialidad_final = calle_osm["tipo_vialidad"] or "CALLE"
        else:
            # Sin OSM: usar un nombre genérico
            nombre_vialidad_final = random.choice(NOMBRES_VIALIDAD)
            if not tipo_vialidad_final:
                tipo_vialidad_final = random.choice(TIPOS_VIALIDAD)

    # Asegurar tipo_vialidad_final siempre tenga algo
    if not tipo_vialidad_final:
        tipo_vialidad_final = "CALLE"

    # 🔹 LIMPIAR "CALLE CALLE ..." EN EL NOMBRE
    nombre_vialidad_final = nombre_vialidad_final.strip()
    if tipo_vialidad_final == "CALLE" and nombre_vialidad_final.startswith("CALLE "):
        nombre_vialidad_final = nombre_vialidad_final[6:].strip()

    # ==========================
    #  Números
    # ==========================
    if numero_exterior_in:
        numero_exterior_final = numero_exterior_in
    else:
        numero_exterior_final = str(random.randint(10, 3999))

    if numero_interior_in:
        numero_interior_final = numero_interior_in
    else:
        if random.random() < 0.4:
            numero_interior_final = "S/N"
        else:
            numero_interior_final = f"{random.randint(1, 50)}"

    # Resultado final
    direccion = {
        "colonia": colonia_final,
        "tipo_vialidad": tipo_vialidad_final,
        "nombre_vialidad": nombre_vialidad_final,
        "numero_exterior": numero_exterior_final,
        "numero_interior": numero_interior_final,
        "cp": cp_final,
    }

    return entidad_dom, municipio_dom, direccion

# ============================================================
#  MAIN
# ============================================================
def main():
    # 1) Preguntar desde el inicio si dirección será automática o manual
    print("=== MODO DE DOMICILIO ===")
    print("1) Automático (OSM + SEPOMEX, sin capturar nada de domicilio)")
    print("2) Manual / semi-manual (tú escribes lo mínimo y se calcula el resto)")
    modo_dom = input("Elige 1 o 2 [1]: ").strip()

    # 2) CURP (esto se ocupa SIEMPRE)
    curp = input("Ingresa el CURP: ").strip().upper()

    datos = consultar_curp(curp)

    # Fechas
    fecha_nac, fecha_inicio_operaciones = generar_fechas(datos["fecha_nac_str"])
    fecha_ultimo_cambio = fecha_inicio_operaciones

    fecha_nac_str_out = formatear_dd_mm_aaaa(fecha_nac)
    fecha_inicio_str_out = formatear_dd_mm_aaaa(fecha_inicio_operaciones)
    fecha_alta = fecha_inicio_str_out
    fecha_ultimo_cambio_str_out = formatear_dd_mm_aaaa(fecha_ultimo_cambio)

    # RFC calculado
    rfc_calculado = calcular_rfc_taxdown(
        datos["nombre"],
        datos["apellido_paterno"],
        datos["apellido_materno"],
        fecha_nac
    )

    # 3) Generar domicilio según modo elegido
    if modo_dom == "2":
        # MODO MANUAL / SEMI-MANUAL
        dom_entidad, dom_municipio, direccion = generar_direccion_manual(
            datos,
            ruta_sepomex="sepomex.csv"
        )
    else:
        # MODO AUTOMÁTICO
        dom_entidad = datos["entidad_registro"]
        dom_municipio = datos["municipio_registro"]
        direccion = generar_direccion_real(
            dom_entidad,
            dom_municipio,
            ruta_sepomex="sepomex.csv",
            permitir_fallback=True
        )

    # 4) Imprimir resultado final en consola (solo para revisión)
    print("\n========== RESULTADO ==========")
    print(f"RFC: {rfc_calculado}")
    print(f"CURP: {curp}")
    print(f"NOMBRE: {datos['nombre']}")
    print(f"APELLIDO PATERNO: {datos['apellido_paterno']}")
    print(f"APELLIDO MATERNO: {datos['apellido_materno']}")
    print(f"FECHA DE NACIMIENTO: {fecha_nac_str_out}")
    print(f"FECHA DE INICIO DE OPERACIONES: {fecha_inicio_str_out}")
    print(f"SITUACION DEL CONTRIBUYENTE: {SITUACION_CONTRIBUYENTE}")
    print(f"FECHA DEL ULTIMO CAMBIO DE SITUACION: {fecha_ultimo_cambio_str_out}")
    print(f"REGIMEN: {REGIMEN}")
    print(f"FECHA DE ALTA: {fecha_alta}")
    print(f"Entidad Federativa: {formatear_entidad_salida(dom_entidad)}")
    print(f"Municipio o delegación: {dom_municipio}")
    print(f"Colonia: {direccion['colonia']}")
    print(f"Tipo de vialidad: {direccion['tipo_vialidad']}")
    print(f"Nombre de la vialidad: {direccion['nombre_vialidad']}")
    print(f"Número exterior: {direccion['numero_exterior']}")
    print(f"Número interior: {direccion['numero_interior']}")
    print(f"CP: {direccion['cp']}")
    print("================================")

    # 5) Generar CIF aleatorio entre 10000000000 y 30000000000
    cif_num = random.randint(10_000_000_000, 30_000_000_000)
    cif_str = str(cif_num)

    # 6) Construir idCIF_RFC (D3) y parámetros del QR
    D1 = "10"
    D2 = "1"
    D3 = f"{cif_str}_{rfc_calculado}"   # idCIF_RFC

    # 7) Armar el registro COMPLETO que usará el HTML
    registro = {
        "D1": D1,
        "D2": D2,
        "D3": D3,  # idCIF_RFC

        "rfc": rfc_calculado,
        "curp": curp,
        "nombre": datos["nombre"],
        "apellido_paterno": datos["apellido_paterno"],
        "apellido_materno": datos["apellido_materno"],
        "fecha_nacimiento": fecha_nac_str_out,
        "fecha_inicio_operaciones": fecha_inicio_str_out,
        "situacion_contribuyente": SITUACION_CONTRIBUYENTE,
        "fecha_ultimo_cambio": fecha_ultimo_cambio_str_out,
        "regimen": REGIMEN,
        "fecha_alta": fecha_alta,

        "entidad": formatear_entidad_salida(dom_entidad),
        "municipio": dom_municipio,
        "colonia": direccion["colonia"],
        "tipo_vialidad": direccion["tipo_vialidad"],
        "nombre_vialidad": direccion["nombre_vialidad"],
        "numero_exterior": direccion["numero_exterior"],
        "numero_interior": direccion["numero_interior"],
        "cp": direccion["cp"],

        # Si luego tienes estos datos, los llenas:
        "correo": "",
        "al": ""
    }

    # 8) Guardar/actualizar personas.json
    json_path = os.path.join("public", "data", "personas.json")
    os.makedirs(os.path.dirname(json_path), exist_ok=True)

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            db = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        db = {}

    # db[D3] = registro, para que el front lo busque por el mismo D3 del QR
    db[D3] = registro

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    # 9) Imprimir datos finales para que generes el QR
    url_base = "https://siat.sat.validacion-sat.com/app/qr/faces/pages/mobile/validadorqr.jsf"
    url_qr = f"{url_base}?D1={D1}&D2={D2}&D3={D3}"

    print("\n=== DATOS PARA QR ===")
    print(f"CIF aleatorio: {cif_str}")
    print(f"idCIF_RFC (D3): {D3}")
    print(f"URL para el código QR:")
    print(url_qr)
    print("=======================")

if __name__ == "__main__":
    main()
