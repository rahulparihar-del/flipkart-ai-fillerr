"""Analyze a Flipkart bulk listing .xls template."""
import re
import subprocess
from pathlib import Path

import pandas as pd

META_ROWS = 4  # rows 0-3 are header/type/example/instructions
SKIP_COLS_PREFIX = 6  # cols 0-5 are Flipkart-internal (QC status etc.)


def convert_to_xlsx(xls_path: str, out_dir: str) -> str:
    """Convert legacy .xls to .xlsx with LibreOffice for reading."""
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "xlsx", xls_path, "--outdir", out_dir],
        check=True, capture_output=True, timeout=120,
    )
    out = Path(out_dir) / (Path(xls_path).stem + ".xlsx")
    if not out.exists():
        raise RuntimeError("LibreOffice conversion failed")
    return str(out)


def find_category_sheet(xl: pd.ExcelFile) -> str:
    for name in xl.sheet_names:
        if name == "Parent Variant Products":
            continue
        df = pd.read_excel(xl, sheet_name=name, header=None, nrows=1)
        headers = [str(v) for v in df.iloc[0].tolist()]
        if "Seller SKU ID" in headers:
            return name
    raise ValueError("No category sheet with 'Seller SKU ID' found")


def read_allowed_values(xl: pd.ExcelFile, header_names: set) -> dict:
    """Parse the Index sheet: attribute name -> list of allowed values."""
    allowed = {}
    if "Index" not in xl.sheet_names:
        return allowed
    ix = pd.read_excel(xl, sheet_name="Index", header=None)
    for c in range(ix.shape[1]):
        vals = [str(v).strip() for v in ix[c].dropna().tolist() if str(v).strip()]
        if not vals:
            continue
        # attribute name is the first (or second) cell that matches a real column header
        if vals[0] in header_names and len(vals) > 1:
            allowed.setdefault(vals[0], vals[1:])
        elif len(vals) > 2 and vals[1] in header_names:
            allowed.setdefault(vals[1], vals[2:])
    return allowed


SIZE_PAT = re.compile(r"[-_ ](\d{1,2})[-_ ](\d{1,2})$")


def sku_base_and_size(sku: str):
    """'Pink-Bear-0-6' -> ('Pink-Bear', '0 - 6 Months')."""
    m = SIZE_PAT.search(sku.strip())
    if not m:
        return sku.strip(), None
    a, b = int(m.group(1)), int(m.group(2))
    return sku[: m.start()].strip("-_ "), f"{a} - {b} Months"


def analyze(xls_path: str, work_dir: str) -> dict:
    xlsx = convert_to_xlsx(xls_path, work_dir)
    xl = pd.ExcelFile(xlsx)
    sheet = find_category_sheet(xl)
    df = pd.read_excel(xl, sheet_name=sheet, header=None)

    headers = ["" if pd.isna(v) else str(v).strip() for v in df.iloc[0].tolist()]
    types = ["" if pd.isna(v) else str(v) for v in df.iloc[1].tolist()] if len(df) > 1 else [""] * len(headers)
    hidx = {h: i for i, h in enumerate(headers) if h}
    sku_col = hidx["Seller SKU ID"]
    allowed = read_allowed_values(xl, set(headers))

    rows = []
    for r in range(META_ROWS, len(df)):
        sku = df.iloc[r][sku_col]
        if pd.isna(sku) or not str(sku).strip():
            continue
        existing, empty = {}, []
        for c in range(SKIP_COLS_PREFIX, len(headers)):
            h = headers[c]
            if not h:
                continue
            v = df.iloc[r][c]
            if pd.isna(v) or not str(v).strip():
                empty.append(h)
            else:
                existing[h] = str(v).strip()
        base, size = sku_base_and_size(str(sku))
        rows.append({
            "row": r, "sku": str(sku).strip(), "base": base, "size": size,
            "existing": existing, "empty": empty,
        })

    # group rows by Group ID if present, else by SKU base
    groups = {}
    for row in rows:
        gid = row["existing"].get("Group ID") or row["base"]
        groups.setdefault(gid, []).append(row)

    parent_info = None
    if "Parent Variant Products" in xl.sheet_names:
        pdf = pd.read_excel(xl, sheet_name="Parent Variant Products", header=None)
        pheaders = ["" if pd.isna(v) else str(v).strip() for v in pdf.iloc[0].tolist()]
        # first empty data row in parent sheet
        prow = META_ROWS
        sku_pc = pheaders.index("Seller SKU ID") if "Seller SKU ID" in pheaders else 6
        while prow < len(pdf) and pd.notna(pdf.iloc[prow][sku_pc]) and str(pdf.iloc[prow][sku_pc]).strip():
            prow += 1
        parent_info = {"headers": pheaders, "next_row": prow}

    return {
        "sheet": sheet, "headers": headers, "types": types,
        "allowed": allowed, "rows": rows, "groups": groups,
        "parent": parent_info, "xlsx": xlsx,
    }
