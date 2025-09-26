from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import xml.etree.ElementTree as ET
import httpx
from datetime import datetime

app = FastAPI(title="Robô DOU API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_KEYWORDS = ["PRONAPA","PCFT","PNM","Comando da Marinha","Fundo Naval"]

class Publicacao(BaseModel):
    date: Optional[str]
    section: Optional[str]
    organ: Optional[str]
    type: Optional[str]
    summary: Optional[str]

class ProcessResponse(BaseModel):
    date: str
    count: int
    publications: List[Publicacao]
    whatsapp_text: str

def parse_xml(content: bytes) -> List[Publicacao]:
    root = ET.fromstring(content)
    pubs = []
    for elem in root.iter():
        if elem.text and any(k.lower() in elem.text.lower() for k in DEFAULT_KEYWORDS):
            pubs.append(Publicacao(type=elem.tag, summary=elem.text.strip()))
    return pubs

def monta_whatsapp(pubs: List[Publicacao], date: str) -> str:
    lines = ["Bom dia!", "", "PTC as seguintes publicações de interesse:", f"DOU {date}:", ""]
    for p in pubs:
        lines.append(f"▶️ {p.organ or 'Órgão'}")
        lines.append(f"📌 {p.type}")
        lines.append(p.summary or '')
        lines.append("⚓ Para conhecimento.")
        lines.append("")
    return "\n".join(lines)

@app.post("/processar-xml", response_model=ProcessResponse)
async def processar_xml(
    data: str = Form(...),
    xml_url: Optional[str] = Form(None),
    arquivo: Optional[UploadFile] = File(None)
):
    if xml_url:
        async with httpx.AsyncClient() as client:
            r = await client.get(xml_url)
            r.raise_for_status()
            content = r.content
    elif arquivo:
        content = await arquivo.read()
    else:
        raise HTTPException(status_code=400, detail="Forneça xml_url ou arquivo")

    pubs = parse_xml(content)
    texto = monta_whatsapp(pubs, data)
    return ProcessResponse(date=data, count=len(pubs), publications=pubs, whatsapp_text=texto)
