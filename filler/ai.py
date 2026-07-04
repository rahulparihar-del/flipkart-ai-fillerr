"""OpenRouter AI generation for missing listing fields."""
import json
import os
import re

import requests

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
VISION_MODEL = os.environ.get("OPENROUTER_VISION_MODEL", "nvidia/nemotron-nano-12b-v2-vl:free")

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


def _chat(payload: dict, api_key: str) -> str:
    resp = requests.post(
        API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://seller.flipkart.com",
            "X-Title": "Flipkart AI Filler",
        },
        json=payload,
        timeout=150,
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"API returned non-JSON (HTTP {resp.status_code})")
    # OpenRouter can return an error object even with HTTP 200
    if "error" in body:
        err = body["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise RuntimeError(f"OpenRouter: {msg[:220]}")
    if not body.get("choices"):
        raise RuntimeError(f"OpenRouter returned no answer (HTTP {resp.status_code})")
    return body["choices"][0]["message"]["content"] or ""


def analyze_product(image_data_url: str, title: str, brand: str, api_key: str) -> dict:
    """Stage 1: vision model reads the photo. Stage 2: text model builds SEO pack."""
    # ── Stage 1: what is in the photo? ──────────────────────────────────────
    vision_prompt = (
        f'This is a product photo from Indian kids clothing brand "{brand}". '
        f'Seller title: "{title or "not given"}".\n'
        "Look carefully at the photo and describe the product. Output ONLY this JSON:\n"
        '{"product_type": "e.g. Co-ord Set / Frock / Romper / T-shirt Set",\n'
        ' "items": ["e.g. Vest", "Shorts"],\n'
        ' "primary_color": "...", "secondary_color": "...",\n'
        ' "print_theme": "e.g. Avocado cartoon print / Dinosaur print / Floral",\n'
        ' "sleeve": "Sleeveless / Half Sleeve / Full Sleeve",\n'
        ' "closure": "e.g. Front snap buttons / Pullover / Elastic waist",\n'
        ' "fabric_look": "e.g. Muslin cotton / Cotton jersey",\n'
        ' "gender": "Boys / Girls / Unisex",\n'
        ' "age_group": "e.g. 0-2 years / 1-3 years",\n'
        ' "notable_details": ["short phrases of anything special you can see"]}\n'
        "No markdown, no explanation — JSON only."
    )
    vision_error = None
    try:
        raw = _chat({
            "model": VISION_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }],
            "temperature": 0.15,
            "max_tokens": 900,
        }, api_key)
        product = _clean(raw)
    except Exception as e:
        # Vision failed (rate limit / model busy) — continue with title only
        vision_error = str(e)[:200]
        product = {"note": "photo analysis unavailable, SEO built from title only"}

    # ── Stage 2: SEO pack from the facts ────────────────────────────────────
    seo_prompt = (
        f'Indian kids clothing brand "{brand}" is listing this product on Flipkart:\n'
        f"Seller title: {title or 'not given'}\n"
        f"Product facts from photo analysis: {json.dumps(product, ensure_ascii=False)}\n\n"
        "Create a marketplace SEO pack the way Indian shoppers search on Flipkart, Amazon, Meesho and Myntra. "
        "Output ONLY this JSON:\n"
        '{"seo_title": "Flipkart style listing title, max 100 chars, brand first, packed with the strongest search terms",\n'
        ' "keywords_easy": ["8-10 long-tail keywords with lower competition where a new seller can rank first"],\n'
        ' "keywords_high": ["8-10 high-volume competitive keywords"],\n'
        ' "description": "550-800 word rank-optimised product description that naturally uses the keywords, with sections for fabric, design, comfort, sizing and care. End with: Brand: '
        + brand + '. Made in India.",\n'
        ' "key_features": ["5 keyword-rich key features, each 10-15 words"],\n'
        ' "ranking_tips": ["3 short practical tips for this specific listing to rank better"]}\n'
        "No markdown — JSON only."
    )
    seo_raw = _chat({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": seo_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
    }, api_key)
    seo = _clean(seo_raw)

    return {"product": product, "seo": seo,
            "vision_error": vision_error,
            "models": {"vision": VISION_MODEL, "text": MODEL}}


def generate_group_fields(group_rows: list, fields: list, allowed: dict,
                          brand: str, api_key: str, seo: dict | None = None) -> dict:
    """One AI call per product group. Returns {field: value}."""
    sample = group_rows[0]["existing"]
    ctx_lines = [f"SKU: {group_rows[0]['base']}"]
    for key in ("Ideal For", "Primary Product Type", "Pattern", "Brand Color",
                "Items Included", "Search Keywords", "Key Features",
                "Pattern/Print Type", "Sleeve Length", "Net Quantity", "Occasion"):
        if key in sample:
            ctx_lines.append(f"{key}: {sample[key]}")

    if seo:
        s = seo.get("seo", seo)
        p = seo.get("product", {})
        if p:
            ctx_lines.append("Photo analysis: " + json.dumps(p, ensure_ascii=False)[:500])
        kws = (s.get("keywords_easy", []) + s.get("keywords_high", []))[:20]
        if kws:
            ctx_lines.append("Researched SEO keywords (use these in Search Keywords and weave into Description): "
                             + ", ".join(str(k) for k in kws))
        if s.get("seo_title"):
            ctx_lines.append(f"SEO title direction: {s['seo_title']}")
        if s.get("description"):
            ctx_lines.append("Description guidance (rewrite naturally, keep keywords): "
                             + str(s["description"])[:700])

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
