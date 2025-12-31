# api.py
import os
import json
import random

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

import core_sat as core  # tu script grande va en core_sat.py

from db import Base, engine, SessionLocal
from models import Persona
from sqlalchemy.orm import Session

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

    # datos que ANTES venían de gob.mx/curp
    nombre: str
    apellido_paterno: str
    apellido_materno: str
    fecha_nac_str: str            # "DD/MM/AAAA"
    entidad_registro: str
    municipio_registro: str

    # opcional: si ya copias el RFC (p.ej. de TaxDown)
    rfc: str | None = None

# ======================================================
#  ENDPOINT: GENERAR CONSTANCIA
# ======================================================
@app.post("/api/constancia")
def generar_constancia_endpoint(peticion: PeticionConstancia, db: Session = next(get_db())):
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

        # === 4) Domicilio automático (igual que en tu main modo automático) ===
        dom_entidad = datos_curp["entidad_registro"]
        dom_municipio = datos_curp["municipio_registro"]

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


# ======================================================
#  ENDPOINT: OBTENER PERSONA POR D3
# ======================================================
@app.get("/api/persona/{d3}")
def obtener_persona(d3: str, db: Session = next(get_db())):
    """
    Devuelve los datos de una persona usando el mismo D3 que va en el QR
    (idCIF_RFC, por ejemplo: 24914557872_CASE020722MP6).
    Lee desde Postgres.
    """
    persona: Persona | None = db.query(Persona).filter(Persona.d3 == d3).first()

    if not persona:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    return persona.datos
