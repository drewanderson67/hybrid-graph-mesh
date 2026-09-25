"""HTTP API + a tiny single-page console for asking questions."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import HybridGraph
from .ir import Query
from .mesh import PlanningError
from .nl import TranslationError

_CONSOLE = Path(__file__).with_name("console.html")


class AskRequest(BaseModel):
    question: str


class IRRequest(BaseModel):
    query: dict


class NativeRequest(BaseModel):
    query: str
    backend: str | None = None


def create_app(graph: HybridGraph) -> FastAPI:
    app = FastAPI(title="Hybrid Graph Mesh", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def console() -> str:
        return _CONSOLE.read_text()

    @app.get("/schema")
    def schema() -> dict:
        return graph.schema()

    @app.post("/ask")
    def ask(req: AskRequest) -> dict:
        try:
            return graph.ask(req.question).to_dict()
        except (TranslationError, PlanningError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/translate")
    def translate(req: AskRequest) -> dict:
        try:
            t = graph.translate(req.question)
            return {**t.to_dict(), "compiled": graph.compile(req.question)}
        except (TranslationError, PlanningError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/query")
    def query(req: IRRequest) -> dict:
        try:
            return graph.query(Query.from_dict(req.query)).to_dict()
        except PlanningError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/sparql")
    def sparql(req: NativeRequest) -> dict:
        try:
            return graph.sparql(req.query, req.backend).to_dict()
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/cypher")
    def cypher(req: NativeRequest) -> dict:
        try:
            return graph.cypher(req.query, req.backend).to_dict()
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except NotImplementedError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc

    return app
