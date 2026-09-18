"""ThermaSight — FastAPI backend. Serves the REST API and the static
frontend from the same process.

    python run.py                -> http://localhost:8000
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .pipeline.demo import generate_demo_csv
from .pipeline.engine import run_pipeline, series_payload
from .pipeline.parser import DatasetError

app = FastAPI(title="ThermaSight", version="1.0.0")

RESULTS: dict[str, dict] = {}
LOCK = threading.Lock()


def _sanitize(obj):
    """Recursively convert NaN/inf floats to None so the JSON is standards-safe."""
    if isinstance(obj, float):
        import math

        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _json(content) -> JSONResponse:
    return JSONResponse(content=_sanitize(content))


class _RawResponse(Response):
    media_type = "application/json"


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "name": "ThermaSight", "time": int(time.time() * 1000)}


@app.post("/api/analyse")
async def analyse(
    demo: str = Form(""),
    dataset: str = Form(""),
    file: UploadFile | None = File(None),
) -> JSONResponse:
    """Run the full pipeline. Upload a CSV conforming to the data contract,
    pass demo=1 / dataset=demo for the built-in synthetic dataset, or
    dataset=real for the bundled YUKTHI 2026 development dataset."""
    if demo == "1" or dataset == "demo":
        csv_text = generate_demo_csv()
        file_name = "yukthi-demo-chillers.csv"
        source = "demo"
    elif dataset == "real":
        real_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "yukthi_development.csv",
        )
        with open(real_path, "r", encoding="utf-8") as fh:
            csv_text = fh.read()
        file_name = "yukthi-development-dataset.csv"
        source = "real"
    elif file is not None:
        raw = await file.read()
        try:
            csv_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="The file must be UTF-8 encoded text (CSV).")
        file_name = file.filename or "uploaded.csv"
        source = "upload"
    else:
        raise HTTPException(status_code=400, detail="Provide a CSV file, or pass demo=1 / dataset=real.")

    result_id = uuid.uuid4().hex[:12]
    try:
        result = run_pipeline(csv_text, file_name)
    except DatasetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # defensive: pipeline bugs should not 500 with a blank body
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    with LOCK:
        RESULTS[result_id] = result
        if len(RESULTS) > 12:  # keep memory bounded
            oldest = next(iter(RESULTS))
            del RESULTS[oldest]

    return _json({
        "id": result_id,
        "source": source,
        "quality": result["quality"],
        "episodeCount": len(result["episodes"]),
        "model": result["model"],
        "nominalIntervalMinutes": result["nominalIntervalMinutes"],
    })


@app.get("/api/result/{result_id}")
def get_result(result_id: str) -> JSONResponse:
    result = RESULTS.get(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found. Run /api/analyse first.")
    # Summaries only (no per-point arrays) — the chart fetches series separately.
    equipment = {}
    for eq, rep in result["equipment"].items():
        equipment[eq] = {
            "equipmentId": eq,
            "health": rep["health"],
            "threshold": rep["threshold"],
            "degradationTrend": rep["degradationTrend"],
            "episodes": rep["episodes"],
            "energyTotalKwh": rep["energyTotalKwh"],
            "energyMeanKwh": rep["energyMeanKwh"],
            "count": rep["count"],
            "timeStart": rep["times"][0],
            "timeEnd": rep["times"][-1],
        }
    return _json({
        "quality": result["quality"],
        "equipment": equipment,
        "episodes": result["episodes"],
        "model": result["model"],
        "nominalIntervalMinutes": result["nominalIntervalMinutes"],
        "generatedAt": result["generatedAt"],
    })


@app.get("/api/series/{result_id}/{equipment_id}")
def get_series(result_id: str, equipment_id: str, variable: str = "Chiller Energy Consumption") -> JSONResponse:
    result = RESULTS.get(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    if equipment_id not in result["equipment"]:
        raise HTTPException(status_code=404, detail=f"Unknown equipment: {equipment_id}")
    if variable not in result["equipment"][equipment_id].get("values", {}):
        raise HTTPException(status_code=404, detail=f"Unknown variable: {variable}")
    return _json(series_payload(result, equipment_id, variable))


@app.get("/api/demo.csv")
def demo_csv_download() -> Response:
    return Response(
        content=generate_demo_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=yukthi-demo-chillers.csv"},
    )


# Static frontend (HTML/CSS/JS) served from the same process.
import os as _os  # noqa: E402

_FRONTEND_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "frontend")
app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")