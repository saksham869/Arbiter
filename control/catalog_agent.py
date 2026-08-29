"""
control/catalog_agent.py

Multimodal COGS Extraction Agent.

Merchant uploads a product image or supplier invoice.
Claude Sonnet (multimodal) extracts structured cost data.
Human approves before it enters the trusted catalog.

IMPORTANT: Extracted COGS never automatically becomes trusted.
The pipeline is:
  image/PDF → AI extraction → confidence check → human approval → trusted catalog

A confidence < 0.80 always requires human review.
A confidence > 0.80 ALSO requires human review.
Financial truth is never delegated to the model.
"""
from __future__ import annotations
import base64
import csv
import io
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import boto3
from dotenv import load_dotenv

load_dotenv()

REGION     = "us-east-1"
MODEL_ID   = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
CATALOG_PATH = "./data/catalog.csv"

# Pending extractions waiting for human approval
_pending: dict[str, dict] = {}


@dataclass
class ExtractionResult:
    extraction_id: str
    status:        str        # pending_review | approved | rejected
    extracted:     list[dict] # list of {sku, product, cogs_paise, confidence}
    image_note:    str
    extracted_at:  str


def _load_catalog() -> dict:
    catalog = {}
    if not os.path.exists(CATALOG_PATH):
        return catalog
    with open(CATALOG_PATH) as f:
        for row in csv.DictReader(f):
            catalog[row["sku"]] = row
    return catalog


def _save_catalog(catalog: dict):
    if not catalog:
        return
    fieldnames = ["sku", "name", "category", "price_paise",
                  "cogs_paise", "return_rate", "stock_units"]
    with open(CATALOG_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in catalog.values():
            # ensure all fields present
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def extract_from_image(image_bytes: bytes, media_type: str = "image/png") -> ExtractionResult:
    """
    Send image to Claude Sonnet (multimodal).
    Extract structured product cost data.
    Returns an ExtractionResult with status=pending_review.
    """
    client  = boto3.client("bedrock-runtime", region_name=REGION)
    b64_img = base64.standard_b64encode(image_bytes).decode("utf-8")

    prompt = """You are a financial data extraction assistant for a retail merchant.

Analyze this supplier invoice or product image and extract cost data.

For each product you can identify, return a JSON array entry:
{
  "sku": "the SKU code if visible, otherwise suggest one like PROD-001",
  "product": "product name",
  "category": "footwear|accessories|apparel|fitness|electronics|other",
  "cogs_paise": <unit cost in Indian paise, integer, 1 rupee = 100 paise>,
  "confidence": <0.0 to 1.0, how confident you are in the cost figure>,
  "note": "any relevant note about this extraction"
}

Return ONLY a JSON array. No explanation. No markdown fences.
If you cannot extract cost data, return an empty array: []

Important:
- The invoice shows costs in rupees (Rs.)
- Convert to paise by multiplying by 100
- Rs. 620.00 → cogs_paise = 62000
- Rs. 210.00 → cogs_paise = 21000
- Rs. 149.00 → cogs_paise = 14900
- DO NOT multiply twice. Rs. 620 × 100 = 62000 paise. That is the final answer.
- If cost is unclear or missing, set confidence = 0.0"""

    try:
        resp = client.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_img,
                            }
                        },
                        {"type": "text", "text": prompt}
                    ]
                }]
            })
        )

        text = json.loads(resp["body"].read())["content"][0]["text"].strip()

        # Clean JSON
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
            if "```" in text:
                text = text.split("```")[0].strip()

        extracted = json.loads(text) if text != "[]" else []

        # Ensure all required fields
        for item in extracted:
            item.setdefault("confidence", 0.5)
            item.setdefault("note", "")
            item.setdefault("category", "other")

        image_note = f"Extracted {len(extracted)} items. " + (
            "All require human approval before entering trusted catalog."
            if extracted else "No cost data found."
        )

    except Exception as e:
        extracted  = []
        image_note = f"Extraction failed: {str(e)}"

    extraction_id = str(uuid.uuid4())
    result = ExtractionResult(
        extraction_id=extraction_id,
        status="pending_review",
        extracted=extracted,
        image_note=image_note,
        extracted_at=datetime.utcnow().isoformat() + "Z",
    )

    # Store for human approval
    _pending[extraction_id] = {
        "extraction_id": extraction_id,
        "status":        "pending_review",
        "extracted":     extracted,
        "image_note":    image_note,
        "extracted_at":  result.extracted_at,
    }

    return result


def approve_extraction(extraction_id: str, approved_skus: list[str]) -> dict:
    """
    Human approves specific SKUs from an extraction.
    Only approved items enter the trusted catalog.
    """
    if extraction_id not in _pending:
        return {"error": "extraction_not_found"}

    pending = _pending[extraction_id]
    catalog = _load_catalog()
    updated = []

    for item in pending["extracted"]:
        if item["sku"] not in approved_skus:
            continue

        sku = item["sku"]
        if sku in catalog:
            # Update existing SKU's COGS
            catalog[sku]["cogs_paise"] = str(item["cogs_paise"])
        else:
            # New SKU — add with defaults
            catalog[sku] = {
                "sku":         sku,
                "name":        item.get("product", sku),
                "category":    item.get("category", "other"),
                "price_paise": str(item.get("cogs_paise", 0)),
                "cogs_paise":  str(item["cogs_paise"]),
                "return_rate": "0.10",
                "stock_units": "100",
            }
        updated.append(sku)

    _save_catalog(catalog)
    _pending[extraction_id]["status"] = "approved"

    return {
        "approved":     updated,
        "catalog_path": CATALOG_PATH,
        "message":      f"{len(updated)} SKUs added to trusted catalog.",
    }


def get_pending() -> list[dict]:
    return [v for v in _pending.values() if v["status"] == "pending_review"]
