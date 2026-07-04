"""OpenRouter AI generation for missing listing fields."""
import json
import os
import re

import requests

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

SYSTEM = (
    "You are a JSON generator for Flipkart product listings for Indian baby and kids "
    "clothing. You ONLY output raw JSON. Never write any explanation or text outside "
    "the JSON object. Start your response with { and end with }."
)

# Fields the server fills with fixed defaults (no AI needed)
FIXED_FIELDS = {
    "Listing Status": "ACTIVE",
    "Fullfilment by": "SELLER",
    "Fulfillment by": "SELLER",
    "Procurement type": "REGULAR",
    "Procurement SLA (DAY)": ("n", 3),
    "Stock": None,            # from UI
    "MRP (INR)": None,        # from UI
    "Your selling price (INR)": None,  # from UI
    "Shipping provider": "FLIPKART",
    "Local handling fee (INR)": ("n", 0),
    "Zonal handling fee (INR)": ("n", 0),
    "National handling fee (INR)": ("n", 0),
    "Length (CM)": ("n", 25),
    "Breadth (CM)": ("n", 20),
    "Height (CM)": ("n", 5),
    "Weight (KG)": ("n", 0.25),
    "HSN": "6111",
    "Country Of Origin": "India",
    "Tax Code": "GST_APPAREL",
    "Minimum Order Quantity (MinOQ)": ("n", 1),
    "Warranty Summary": "No warranty applicable on clothing",
}

# Never send these to AI (Flipkart-internal / IDs / URLs / UI-provided)
NO_AI = set(FIXED_FIELDS) | {
    "Group ID", "Parent Variant FSN", "Brand", "Manufacturer Details",
    "Packer Details", "Importer Details", "Luxury Cess", "EAN/UPC",
    "Supplier Image", "Video URL", "Domestic Warranty",
    "Domestic Warranty - Measuring Unit", "Main Image URL", "Other Image URL 1",
    "Other Image URL 2", "Other Image URL 3", "Label Size", "Brand Size",
    "Style Code", "Seller SKU ID", "Other Dimensions",
}


def _clean(raw: str) -> dict:
    raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.I).strip()
    raw = re.sub(r"```json\s*", "", raw, flags=re.I).replace("```", "").strip()
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("AI did not return JSON")
    return json.loads(raw[s : e + 1])


def generate_group_fields(group_rows: list, fields: list, allowed: dict,
                          brand: str, api_key: str) -> dict:
    """One AI call per product group. Returns {field: value}."""
    sample = group_rows[0]["existing"]
    ctx_lines = [f"SKU: {group_rows[0]['base']}"]
    for key in ("Ideal For", "Primary Product Type", "Pattern", "Brand Color",
                "Items Included", "Search Keywords", "Key Features",
                "Pattern/Print Type", "Sleeve Length", "Net Quantity", "Occasion"):
        if key in sample:
            ctx_lines.append(f"{key}: {sample[key]}")

    spec_lines = []
    for f in fields:
        opts = allowed.get(f)
        if opts:
            shown = opts[:60]
            spec_lines.append(f'"{f}": pick EXACTLY one from [{", ".join(json.dumps(o) for o in shown)}]')
        elif f == "Description":
            spec_lines.append('"Description": 5-7 sentence product description, mention set contents, fabric, comfort, care. End with: Brand: '
                              + brand + '. Made in India.')
        elif f in ("Fabric", "Fabric Care", "Secondary Product Type", "Secondary Color", "Trend", "Other Features"):
            spec_lines.append(f'"{f}": suitable value(s), join multiple with :: separator')
        else:
            spec_lines.append(f'"{f}": suitable short value')

    prompt = (
        f"A seller of Indian kids clothing brand {brand} is listing this product on Flipkart.\n\n"
        "Known product data:\n" + "\n".join(ctx_lines) + "\n\n"
        "Generate values for these fields. For fields with an options list you MUST copy "
        "one option EXACTLY as written. Multi-value fields use :: as separator.\n\n"
        + "\n".join(spec_lines)
        + '\n\nOutput ONLY a JSON object mapping every field name to its value.'
    )

    resp = requests.post(
        API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://seller.flipkart.com",
            "X-Title": "Flipkart AI Filler",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.25,
            "max_tokens": 2500,
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _clean(content)
