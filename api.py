# api.py
import os
import json
import random

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

import core_sat as core  # tu script grande va en core_sat.py

from db import Base, engine, SessionLocal
from models import Persona
from sqlalchemy.orm import Session

from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import tempfile

from docx_utils import generar_docx_desde_plantilla

# ======================================================
#  APP FASTAPI
# ======================================================
app = FastAPI(
    title="SAT Clon Backend",
    version="1.0.0",
    description="API mínima para generar constancia y datos del QR",
)

origins = ["*"]  # luego puedes restringir a tu dominio de Vercel

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Crear tablas al arrancar
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


# Dependencia de sesión DB (estilo FastAPI)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ===== ESQUEMAS =====
class PeticionConstancia(BaseModel):
    curp: str
    nombre: str
    apellido_paterno: str
    apellido_materno: str
    fecha_nac_str: str
    entidad_registro: str
    municipio_registro: str
    rfc: str

    # domicilio opcional
    colonia: str | None = None
    tipo_vialidad: str | None = None
    nombre_vialidad: str | None = None
    numero_exterior: str | None = None
    numero_interior: str | None = None
    cp: str | None = None

    # régimen opcional
    regimen: str | None = None

    # fechas opcionales (si no las mandas, se calculan)
    fecha_inicio_operaciones: str | None = None   # "DD-MM-YYYY"
    fecha_ultimo_cambio: str | None = None        # "DD-MM-YYYY"
    fecha_alta: str | None = None                 # "DD-MM-YYYY"

# ======================================================
#  ENDPOINT: GENERAR CONSTANCIA
# ======================================================
@app.post("/api/constancia")
def generar_constancia_endpoint(peticion: PeticionConstancia, db: Session = Depends(get_db)):
    """
    Genera la constancia y datos del QR a partir de un CURP.
    Si ya existe una persona con ese RFC, reutiliza el mismo D3 (QR estable).
    """
    try:
        curp = peticion.curp.strip().upper()
        if len(curp) != 18:
            raise HTTPException(status_code=400, detail="CURP debe tener 18 caracteres")

        # === 1) Consultar datos en gob.mx/curp usando tu función ===
        datos_curp = {
            "nombre": peticion.nombre.strip().upper(),
            "apellido_paterno": peticion.apellido_paterno.strip().upper(),
            "apellido_materno": peticion.apellido_materno.strip().upper(),
            "fecha_nac_str": peticion.fecha_nac_str.strip(),
            "entidad_registro": peticion.entidad_registro.strip().upper(),
            "municipio_registro": peticion.municipio_registro.strip().upper(),
        }

        # === 2) Fechas ===
        fecha_nac, fecha_inicio_operaciones = core.generar_fechas(
            datos_curp["fecha_nac_str"]
        )
        fecha_ultimo_cambio = fecha_inicio_operaciones

        fecha_nac_str_out = core.formatear_dd_mm_aaaa(fecha_nac)
        fecha_inicio_str_out = core.formatear_dd_mm_aaaa(fecha_inicio_operaciones)
        fecha_alta = fecha_inicio_str_out
        fecha_ultimo_cambio_str_out = core.formatear_dd_mm_aaaa(fecha_ultimo_cambio)

        # === 3) RFC ===
        if peticion.rfc:
            rfc_calculado = peticion.rfc.strip().upper()
        else:
            # si quieres, puedes implementar después una función local sin Selenium
            # por ahora lanzo error claro para que te acuerdes de enviarlo
            raise HTTPException(
                status_code=400,
                detail="Falta el RFC en la petición (campo 'rfc')."
            )

        # 🔹 3.1 Revisar si YA existe este RFC en la BD
        persona_existente: Persona | None = (
            db.query(Persona).filter(Persona.rfc == rfc_calculado).first()
        )

        if persona_existente:
            # Reusar datos y D3 → QR estable
            D3 = persona_existente.d3
            cif_str = persona_existente.cif
            registro = persona_existente.datos

            url_base = (
                "https://siat.sat.validacion-sat.com/"
                "app/qr/faces/pages/mobile/validadorqr.jsf"
            )
            url_qr = f"{url_base}?D1={registro['D1']}&D2={registro['D2']}&D3={D3}"

            return {
                "cif": cif_str,
                "idcif_rfc": D3,
                "url_qr": url_qr,
                "datos": registro,
                "reutilizado": True,
            }

        # === 4) DOMICILIO ===
        dom_entidad = datos_curp["entidad_registro"]
        dom_municipio = datos_curp["municipio_registro"]
        
        # Si viene domicilio manual “completo o parcial”, úsalo
        if any([peticion.colonia, peticion.cp, peticion.nombre_vialidad, peticion.numero_exterior]):
            direccion = {
                "colonia": (peticion.colonia or "").strip().upper() or "S/C",
                "tipo_vialidad": (peticion.tipo_vialidad or "").strip().upper() or "CALLE",
                "nombre_vialidad": (peticion.nombre_vialidad or "").strip().upper() or "S/N",
                "numero_exterior": (peticion.numero_exterior or "").strip().upper() or "S/N",
                "numero_interior": (peticion.numero_interior or "").strip().upper() or "",
                "cp": (peticion.cp or "").strip() or "00000",
            }
        else:
            # automático como ya lo tienes
            direccion = core.generar_direccion_real(
                dom_entidad,
                dom_municipio,
                ruta_sepomex="sepomex.csv",
                permitir_fallback=True,
            )

        # === 5) CIF + D1, D2, D3 para el QR (nuevo registro) ===
        cif_num = random.randint(10_000_000_000, 30_000_000_000)
        cif_str = str(cif_num)

        D1 = "10"
        D2 = "1"
        D3 = f"{cif_str}_{rfc_calculado}"  # idCIF_RFC

        # === 6) Registro completo (igual que en tu main) ===
        registro = {
            "D1": D1,
            "D2": D2,
            "D3": D3,

            "rfc": rfc_calculado,
            "curp": curp,
            "nombre": datos_curp["nombre"],
            "apellido_paterno": datos_curp["apellido_paterno"],
            "apellido_materno": datos_curp["apellido_materno"],
            "fecha_nacimiento": fecha_nac_str_out,
            "fecha_inicio_operaciones": fecha_inicio_str_out,
            "situacion_contribuyente": core.SITUACION_CONTRIBUYENTE,
            "fecha_ultimo_cambio": fecha_ultimo_cambio_str_out,
            "regimen": core.REGIMEN,
            "fecha_alta": fecha_alta,

            "entidad": core.formatear_entidad_salida(dom_entidad),
            "municipio": dom_municipio,
            "colonia": direccion["colonia"],
            "tipo_vialidad": direccion["tipo_vialidad"],
            "nombre_vialidad": direccion["nombre_vialidad"],
            "numero_exterior": direccion["numero_exterior"],
            "numero_interior": direccion["numero_interior"],
            "cp": direccion["cp"],

            "correo": "",
            "al": "",
        }

        # === 7) Guardar en BD (YA NO EN personas.json) ===
        persona_nueva = Persona(
            cif=cif_str,
            d3=D3,
            rfc=rfc_calculado,
            curp=curp,
            datos=registro,
        )

        db.add(persona_nueva)
        db.commit()
        db.refresh(persona_nueva)

        # === 8) URL del QR ===
        url_base = (
            "https://siat.sat.validacion-sat.com/"
            "app/qr/faces/pages/mobile/validadorqr.jsf"
        )
        url_qr = f"{url_base}?D1={D1}&D2={D2}&D3={D3}"

        return {
            "cif": cif_str,
            "idcif_rfc": D3,
            "url_qr": url_qr,
            "datos": registro,
            "reutilizado": False,
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {e}",
        )
        
@app.post("/api/constancia/docx")
def generar_constancia_docx(peticion: PeticionConstancia, db: Session = Depends(get_db)):
    # 1) Validaciones mínimas
    curp = (peticion.curp or "").strip().upper()
    if len(curp) != 18:
        raise HTTPException(status_code=400, detail="CURP debe tener 18 caracteres")

    rfc = (peticion.rfc or "").strip().upper()
    if not rfc:
        raise HTTPException(status_code=400, detail="Falta el RFC (campo 'rfc').")

    # 2) Datos base (manuales)
    datos_curp = {
        "nombre": (peticion.nombre or "").strip().upper(),
        "apellido_paterno": (peticion.apellido_paterno or "").strip().upper(),
        "apellido_materno": (peticion.apellido_materno or "").strip().upper(),
        "fecha_nac_str": (peticion.fecha_nac_str or "").strip(),  # "DD/MM/AAAA"
        "entidad_registro": (peticion.entidad_registro or "").strip().upper(),
        "municipio_registro": (peticion.municipio_registro or "").strip().upper(),
    }

    if not datos_curp["nombre"] or not datos_curp["apellido_paterno"] or not datos_curp["fecha_nac_str"]:
        raise HTTPException(
            status_code=400,
            detail="Faltan campos requeridos: nombre, apellido_paterno, fecha_nac_str"
        )

    if not datos_curp["entidad_registro"] or not datos_curp["municipio_registro"]:
        raise HTTPException(
            status_code=400,
            detail="Faltan campos requeridos: entidad_registro y municipio_registro"
        )

    # 3) Fechas: nacimiento (real) + inicio/alta/ultimo (opcionales o auto)
    try:
        fecha_nac, fecha_inicio_auto = core.generar_fechas(datos_curp["fecha_nac_str"])
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="fecha_nac_str inválida. Usa formato DD/MM/AAAA (ej: 22/07/2002)."
        )

    fecha_nac_str_out = core.formatear_dd_mm_aaaa(fecha_nac)

    # Si mandan fecha_inicio_operaciones (DD-MM-YYYY) úsala, si no usa la auto
    fecha_inicio_str_out = (
        peticion.fecha_inicio_operaciones.strip()
        if getattr(peticion, "fecha_inicio_operaciones", None)
        else core.formatear_dd_mm_aaaa(fecha_inicio_auto)
    )

    # Si mandan fecha_ultimo_cambio úsala, si no igual a inicio
    fecha_ultimo_cambio_str_out = (
        peticion.fecha_ultimo_cambio.strip()
        if getattr(peticion, "fecha_ultimo_cambio", None)
        else fecha_inicio_str_out
    )

    # Si mandan fecha_alta úsala, si no igual a inicio
    fecha_alta = (
        peticion.fecha_alta.strip()
        if getattr(peticion, "fecha_alta", None)
        else fecha_inicio_str_out
    )

    # 4) Régimen: si lo mandan úsalo, si no el default del core
    regimen_final = (getattr(peticion, "regimen", None) or core.REGIMEN).strip()

    # 5) Dirección: manual si viene “algo”, si no automática OSM+SEPOMEX
    dom_entidad = datos_curp["entidad_registro"]
    dom_municipio = datos_curp["municipio_registro"]

    viene_algo_domicilio = any([
        getattr(peticion, "colonia", None),
        getattr(peticion, "cp", None),
        getattr(peticion, "nombre_vialidad", None),
        getattr(peticion, "numero_exterior", None),
    ])

    if viene_algo_domicilio:
        direccion = {
            "colonia": (getattr(peticion, "colonia", "") or "").strip().upper() or "S/C",
            "tipo_vialidad": (getattr(peticion, "tipo_vialidad", "") or "").strip().upper() or "CALLE",
            "nombre_vialidad": (getattr(peticion, "nombre_vialidad", "") or "").strip().upper() or "S/N",
            "numero_exterior": (getattr(peticion, "numero_exterior", "") or "").strip().upper() or "S/N",
            "numero_interior": (getattr(peticion, "numero_interior", "") or "").strip().upper() or "",
            "cp": (getattr(peticion, "cp", "") or "").strip() or "00000",
        }
    else:
        direccion = core.generar_direccion_real(
            dom_entidad,
            dom_municipio,
            ruta_sepomex="sepomex.csv",
            permitir_fallback=True,
        )

    # 6) CIF / D3 (para el QR dentro del DOCX)
    cif_num = random.randint(10_000_000_000, 30_000_000_000)
    cif_str = str(cif_num)
    D1, D2 = "10", "1"
    D3 = f"{cif_str}_{rfc}"

    # 7) Fechas para etiqueta (corta/larga)
    ahora = datetime.now(ZoneInfo("America/Mexico_City"))
    fecha_corta = ahora.strftime("%Y/%m/%d %H:%M:%S")

    # Fecha larga estilo “01 DE ENERO DE 2026”
    meses = [
        "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
        "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"
    ]
    fecha_larga = f"{ahora.day:02d} DE {meses[ahora.month - 1]} DE {ahora.year}"

    nombre_etiqueta = " ".join(
        x for x in [
            datos_curp["nombre"],
            datos_curp["apellido_paterno"],
            datos_curp["apellido_materno"],
        ] if x
    ).strip()

    # 8) Datos para plantilla DOCX (placeholders)
    datos_doc = {
        "RFC_ETIQUETA": rfc,
        "NOMBRE_ETIQUETA": nombre_etiqueta,
        "IDCIF_ETIQUETA": cif_str,

        "RFC": rfc,
        "CURP": curp,
        "NOMBRE": datos_curp["nombre"],
        "PRIMER_APELLIDO": datos_curp["apellido_paterno"],
        "SEGUNDO_APELLIDO": datos_curp["apellido_materno"],

        "FECHA_INICIO": fecha_inicio_str_out,
        "ESTATUS": core.SITUACION_CONTRIBUYENTE,
        "FECHA_ULTIMO": fecha_ultimo_cambio_str_out,
        "FECHA": fecha_larga,
        "FECHA_CORTA": fecha_corta,

        "CP": direccion["cp"],
        "TIPO_VIALIDAD": direccion["tipo_vialidad"],
        "VIALIDAD": direccion["nombre_vialidad"],
        "NO_EXTERIOR": direccion["numero_exterior"],
        "NO_INTERIOR": direccion["numero_interior"],
        "COLONIA": direccion["colonia"],
        "LOCALIDAD": dom_municipio,
        "ENTIDAD": core.formatear_entidad_salida(dom_entidad),

        "REGIMEN": regimen_final,
        "FECHA_ALTA": fecha_alta,
    }

    # 9) Elegir plantilla (asalariado vs normal)
    base_dir = Path(__file__).resolve().parent

    if regimen_final == "Régimen de Sueldos y Salarios e Ingresos Asimilados a Salarios":
        plantilla = base_dir / "plantilla-asalariado.docx"
    else:
        plantilla = base_dir / "plantilla.docx"

    if not plantilla.exists():
        raise HTTPException(status_code=500, detail=f"No existe la plantilla: {plantilla.name}")

    # 10) Generar DOCX con tu util (incluye QR + barcode adentro)
    ruta_docx = generar_docx_desde_plantilla(datos_doc, str(plantilla))

    # 11) Responder descarga
    filename = f"{curp}_RFC.docx"
    return FileResponse(
        ruta_docx,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )

# ======================================================
#  ENDPOINT: OBTENER PERSONA POR D3
# ======================================================
@app.get("/api/persona/{d3}")
def obtener_persona(d3: str, db: Session = Depends(get_db)):
    """
    Devuelve los datos de una persona usando el mismo D3 que va en el QR
    (idCIF_RFC, por ejemplo: 24914557872_CASE020722MP6).
    Lee desde Postgres.
    """
    persona: Persona | None = db.query(Persona).filter(Persona.d3 == d3).first()

    if not persona:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    return persona.datos
