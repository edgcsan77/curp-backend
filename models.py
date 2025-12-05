# models.py
from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

from db import Base

class Persona(Base):
    __tablename__ = "personas"

    id = Column(Integer, primary_key=True, index=True)
    # idCIF (número aleatorio)
    cif = Column(String(32), nullable=False)
    # D3 completo: "<cif>_<RFC>"
    d3 = Column(String(64), unique=True, index=True, nullable=False)

    # Identificadores
    rfc = Column(String(20), unique=True, index=True, nullable=False)
    curp = Column(String(20), index=True, nullable=False)

    # Registro completo tal cual lo usas en el front
    datos = Column(JSONB, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
