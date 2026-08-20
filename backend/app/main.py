from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.rag.engine import run_pu_matcher_agent
from app.templates import TEMPLATES_DISPONIVEIS

app = FastAPI(title="PU Matcher API", version="2.0.0")

class MatchRequest(BaseModel):
    query: str
    template_id: str = "proposta_tecnica_completa"
    model_name: str = "gemini/gemini-1.5-flash"
    history: Optional[List[dict]] = []

@app.get("/")
def health():
    return {"status": "online", "service": "PU Matcher - Product Match & Consultative Sales"}

@app.get("/api/templates")
def list_templates():
    """Lista todos os templates cadastrados no sistema."""
    return TEMPLATES_DISPONIVEIS

@app.post("/api/match")
def match_product(req: MatchRequest):
    try:
        res = run_pu_matcher_agent(
            query=req.query,
            template_id=req.template_id,
            model_name=req.model_name,
            history=req.history
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
