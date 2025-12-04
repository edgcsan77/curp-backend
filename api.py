# api.py
# Backend mínimo con FastAPI usando tu código de core_sat.py

import os
import json
import random

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

import core_sat as core  # <-- tu script grande va en core_sat.py

app = FastAPI(
    title="SAT Clon Backend",
    version="1.0.0",
    description="API mínima para generar constancia y datos del QR",
)

# Ajusta estos dominios cuando tengas tu frontend en Vercel
origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "https://siat.sat.validacion-sat.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PeticionConstancia(BaseModel):
    curp: str


@app.post("/api/constancia")
def generar_constancia_endpoint(peticion: PeticionConstancia):
    """
    Genera la constancia y datos del QR a partir de un CURP.
    Usa SIEMPRE modo automático de domicilio (OSM + SEPOMEX).
    """
    try:
        curp = peticion.curp.strip().upper()
        if len(curp) != 18:
            raise HTTPException(status_code=400, detail="CURP debe tener 18 caracteres")

        # === 1) Consultar datos en gob.mx/curp usando tu función ===
        datos = core.consultar_curp(curp)

        # === 2) Fechas ===
        fecha_nac, fecha_inicio_operaciones = core.generar_fechas(
            datos["fecha_nac_str"]
        )
        fecha_ultimo_cambio = fecha_inicio_operaciones

        fecha_nac_str_out = core.formatear_dd_mm_aaaa(fecha_nac)
        fecha_inicio_str_out = core.formatear_dd_mm_aaaa(fecha_inicio_operaciones)
        fecha_alta = fecha_inicio_str_out
        fecha_ultimo_cambio_str_out = core.formatear_dd_mm_aaaa(fecha_ultimo_cambio)

        # === 3) RFC calculado con tu función de TaxDown ===
        rfc_calculado = core.calcular_rfc_taxdown(
            datos["nombre"],
            datos["apellido_paterno"],
            datos["apellido_materno"],
            fecha_nac,
        )

        # === 4) Domicilio automático (la misma lógica que en tu main modo 1) ===
        dom_entidad = datos["entidad_registro"]
        dom_municipio = datos["municipio_registro"]

        direccion = core.generar_direccion_real(
            dom_entidad,
            dom_municipio,
            ruta_sepomex="sepomex.csv",
            permitir_fallback=True,
        )

        # === 5) CIF + parámetros D1, D2, D3 para QR ===
        cif_num = random.randint(10_000_000_000, 30_000_000_000)
        cif_str = str(cif_num)

        D1 = "10"
        D2 = "1"
        D3 = f"{cif_str}_{rfc_calculado}"  # idCIF_RFC

        # === 6) Armar registro igual que en tu main() ===
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

            # Campos opcionales que ya traías
            "correo": "",
            "al": "",
        }

        # === 7) (Opcional) guardar en personas.json en este backend ===
        # Ojo: en Render el disco puede ser efímero (se borra al reiniciar),
        # es más bien para pruebas.
        try:
            json_path = os.path.join("public", "data", "personas.json")
            os.makedirs(os.path.dirname(json_path), exist_ok=True)

            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    db = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                db = {}

            db[D3] = registro

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(db, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # No tronamos la API si falla el guardado; solo lo anotamos en logs
            print(f"[WARN] No se pudo guardar personas.json: {e}")

        # === 8) Armar URL de QR y devolver todo al frontend ===
        url_base = (
            "https://siat.sat.validacion-sat.com/"
            "app/qr/faces/pages/mobile/validadorqr.jsf"
        )
        url_qr = f"{url_base}?D1={D1}&D2={D2}&D3={D3}"

        respuesta = {
            "cif": cif_str,
            "idcif_rfc": D3,
            "url_qr": url_qr,
            "datos": registro,
        }

        return respuesta

    except HTTPException:
        # re-lanzamos errores HTTP explícitos
        raise
    except Exception as e:
        # cualquier otra cosa es 500
        print(f"[ERROR] {e!r}")
        raise HTTPException(status_code=500, detail="Error interno en el servidor")
