"""Flipkart AI Filler — upload a Flipkart bulk .xls, AI fills it, download."""
import os
import shutil
import uuid
from pathlib import Path

# pyrefly: ignore [missing-import]
from fastapi import FastAPI, File, HTTPException, UploadFile
# pyrefly: ignore [missing-import]
from fastapi.responses import FileResponse, HTMLResponse
# pyrefly: ignore [missing-import]
from pydantic import BaseModel

from filler import ai, analyze, apply

app = FastAPI(title="Flipkart AI Filler")

JOBS_DIR = Path(os.environ.get("JOBS_DIR", "/tmp/fk_jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
JOBS: dict = {}

DEFAULT_KEY = os.environ.get("OPENROUTER_API_KEY", "")


@app.get("/", response_class=HTMLResponse)
def home():
    return Path(__file__).with_name("static").joinpath("index.html").read_text()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xls", ".xlsx", ".xlsm")):
        raise HTTPException(400, "Please upload the Flipkart .xls template")
    job_id = uuid.uuid4().hex[:12]
    jdir = JOBS_DIR / job_id
    jdir.mkdir(parents=True)
    orig = jdir / file.filename
    orig.write_bytes(await file.read())

    try:
        info = analyze.analyze(str(orig), str(jdir))
    except Exception as e:
        shutil.rmtree(jdir, ignore_errors=True)
        raise HTTPException(422, f"Could not analyze sheet: {e}")

    JOBS[job_id] = {"dir": str(jdir), "file": str(orig), "name": file.filename, "info": info}

    products = []
    for gid, rows in info["groups"].items():
        sample = rows[0]["existing"]
        products.append({
            "group": gid,
            "skus": [r["sku"] for r in rows],
            "sizes": [r["size"] or "?" for r in rows],
            "type": sample.get("Primary Product Type", ""),
            "ideal_for": sample.get("Ideal For", ""),
            "color": sample.get("Brand Color", ""),
            "missing_count": len(rows[0]["empty"]),
        })
    return {
        "job_id": job_id,
        "sheet": info["sheet"],
        "total_rows": len(info["rows"]),
        "products": products,
        "has_parent_sheet": info["parent"] is not None,
    }


class FillReq(BaseModel):
    job_id: str
    mrp: float = 599
    price: float = 299
    stock: int = 100
    brand: str = "KiddieKa"
    manufacturer: str = "KiddieKa, India"
    hsn: str = "6111"
    fill_parent: bool = True
    api_key: str = ""


@app.post("/api/fill")
def fill(req: FillReq):
    job = JOBS.get(req.job_id)
    if not job:
        raise HTTPException(404, "Job not found — upload again")
    info = job["info"]
    key = req.api_key or DEFAULT_KEY
    if not key:
        raise HTTPException(400, "No OpenRouter API key configured")

    headers = info["headers"]
    hidx = {h: i for i, h in enumerate(headers) if h}
    sheet = info["sheet"]
    instructions, effective = [], {}
    ai_errors = []
    ai_cells, fixed_cells = 0, 0
    ai_report: dict = {}

    ui_numbers = {
        "MRP (INR)": req.mrp,
        "Your selling price (INR)": req.price,
        "Stock": req.stock,
    }

    for gid, rows in info["groups"].items():
        # ---- AI fields for this group (union of empty, AI-eligible) ----
        ai_fields = sorted({
            f for r in rows for f in r["empty"]
            if f not in ai.NO_AI and f in hidx
        })
        ai_vals = {}
        if ai_fields:
            try:
                ai_vals = ai.generate_group_fields(rows, ai_fields, info["allowed"], req.brand, key)
            except Exception as e:
                ai_errors.append(f"{gid}: {e}")

        for r in rows:
            eff = dict(r["existing"])
            for field in r["empty"]:
                col = hidx.get(field)
                if col is None:
                    continue
                val, kind = None, "s"
                if field in ui_numbers:
                    val, kind = ui_numbers[field], "n"
                elif field in ("Label Size", "Brand Size") and r["size"]:
                    val = r["size"]
                elif field == "Group ID":
                    val = gid.replace(" ", "")
                elif field == "Style Code":
                    val = r["base"]
                elif field == "Brand":
                    val = req.brand
                elif field in ("Manufacturer Details", "Packer Details"):
                    val = req.manufacturer
                elif field == "HSN":
                    val = req.hsn
                elif field in ai.FIXED_FIELDS and ai.FIXED_FIELDS[field] is not None:
                    fv = ai.FIXED_FIELDS[field]
                    if isinstance(fv, tuple):
                        kind, val = "n", fv[1]
                    else:
                        val = fv
                elif field in ai_vals and ai_vals[field]:
                    v = ai_vals[field]
                    val = "::".join(str(x) for x in v) if isinstance(v, list) else str(v)
                    ai_cells += 1
                    ai_report.setdefault(gid, set()).add(field)
                if val is not None and str(val).strip():
                    if field not in ai_vals or field in ui_numbers or field in ai.FIXED_FIELDS:
                        fixed_cells += 1
                    instructions.append((sheet, r["row"], col, kind, val))
                    eff[field] = str(val)
            effective[r["sku"]] = eff

    # ---- Parent Variant Products: first (smallest) size row per group ----
    parent_rows_added = 0
    if req.fill_parent and info["parent"]:
        pheaders = info["parent"]["headers"]
        prow = info["parent"]["next_row"]
        for gid, rows in info["groups"].items():
            main = sorted(rows, key=lambda r: r["size"] or "zzz")[0]
            eff = effective.get(main["sku"], main["existing"])
            eff = {**eff, "Seller SKU ID": main["sku"]}
            for pc, ph in enumerate(pheaders):
                if pc < 6 or not ph or ph not in eff:
                    continue
                instructions.append(("Parent Variant Products", prow, pc, "s", eff[ph]))
            prow += 1
            parent_rows_added += 1

    out = Path(job["dir"]) / ("FILLED_" + job["name"])
    try:
        apply.apply_fills(job["file"], str(out), instructions)
    except Exception as e:
        raise HTTPException(500, f"Fill failed: {e}")

    job["out"] = str(out)
    return {
        "ok": True,
        "cells_filled": len(instructions),
        "ai_cells": ai_cells,
        "fixed_cells": fixed_cells,
        "ai_report": {g: sorted(f) for g, f in ai_report.items()},
        "model": ai.MODEL,
        "parent_rows": parent_rows_added,
        "ai_errors": ai_errors,
        "download": f"/api/download/{req.job_id}",
    }


@app.get("/api/download/{job_id}")
def download(job_id: str):
    job = JOBS.get(job_id)
    if not job or "out" not in job:
        raise HTTPException(404, "Nothing to download")
    return FileResponse(job["out"], filename=Path(job["out"]).name,
                        media_type="application/vnd.ms-excel")
