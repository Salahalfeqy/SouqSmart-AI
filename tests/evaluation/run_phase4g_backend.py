import asyncio
import json
import time
import hmac
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

from httpx import AsyncClient, ASGITransport
from app.main import app, retriever, catalog, query_rewriter, sessions
from app.services import parse_chat_intent
from app.config import get_settings

settings = get_settings()

QUERIES = [
    {
        "id": "Q01",
        "query": "Wireless Headphones",
        "lang": "en",
        "type": "exact_catalog",
        "session_key": "sess_q1",
    },
    {
        "id": "Q02",
        "query": "عايز ماكينة قهوة للبيت",
        "lang": "ar",
        "type": "arabic_catalog",
        "session_key": "sess_q2_flow",
    },
    {
        "id": "Q03",
        "query": "سماعات رأس بلوتوث wireless",
        "lang": "ar",
        "type": "mixed_script",
        "session_key": "sess_q3",
    },
    {
        "id": "Q04",
        "query": "wireless mouse under 600 EGP",
        "lang": "en",
        "type": "budget_filter",
        "session_key": "sess_q4",
    },
    {
        "id": "Q05",
        "query": "Samsung Galaxy Ultra smartphone",
        "lang": "en",
        "type": "out_of_catalog",
        "session_key": "sess_q5",
    },
    {
        "id": "Q06",
        "query": "هدية شيك لصديق",
        "lang": "ar",
        "type": "ambiguous_clarify",
        "session_key": "sess_q6",
    },
    {
        "id": "Q07",
        "query": "هل عندكم منها حاجة أرخص؟",
        "lang": "ar",
        "type": "followup_context",
        "session_key": "sess_q2_flow", # Reuses session from Q02
    },
    {
        "id": "Q08",
        "query": "compare Wireless Headphones and Wireless Mouse",
        "lang": "en",
        "type": "comparison",
        "session_key": "sess_q8",
    },
    {
        "id": "Q09",
        "query": "ما هي سياسة الشحن ومواعيد التوصيل؟",
        "lang": "ar",
        "type": "shipping_policy",
        "session_key": "sess_q9",
    },
    {
        "id": "Q10",
        "query": "كيف يمكنني إرجاع منتج وما هي الشروط؟",
        "lang": "ar",
        "type": "returns_policy",
        "session_key": "sess_q10",
    },
    {
        "id": "Q11",
        "query": "ما هي طرق الدفع المتاحة؟",
        "lang": "ar",
        "type": "payment_policy",
        "session_key": "sess_q11",
    },
    {
        "id": "Q12",
        "query": "ما هي سياسة الضمان للأجهزة؟",
        "lang": "ar",
        "type": "warranty_policy",
        "session_key": "sess_q12",
    },
]

async def run_part_a():
    results = []
    session_map = {}

    service_key = settings.service_key
    owner_hash = hmac.new(service_key.encode("utf-8"), b"customer:42", hashlib.sha256).hexdigest()
    headers = {
        "X-Service-Key": service_key,
        "X-Owner-Hash": owner_hash,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for item in QUERIES:
            qid = item["id"]
            q_text = item["query"]
            lang = item["lang"]
            s_key = item["session_key"]
            sid = session_map.get(s_key)

            print(f"--- Running {qid}: '{q_text}' ---")
            t0 = time.time()

            # Pre-trace diagnostics
            parsed_intent = parse_chat_intent(q_text, lang)

            # Execution via production FastAPI ASGI path
            payload = {
                "message": q_text,
                "session_id": sid,
                "language": lang,
                "context": {"current_product_id": None},
            }

            resp = await client.post("/v1/chat/messages", json=payload, headers=headers)
            elapsed = time.time() - t0

            assert resp.status_code == 200, f"Failed on {qid}: {resp.status_code} {resp.text}"
            data = resp.json()

            # Store session ID for follow-ups
            actual_sid = data.get("session_id")
            session_map[s_key] = actual_sid

            reply_text = data.get("reply", {}).get("text", "")
            products = data.get("products", [])

            record = {
                "id": qid,
                "query": q_text,
                "language": lang,
                "type": item["type"],
                "elapsed_seconds": round(elapsed, 2),
                "intent_detected": parsed_intent.intent_type,
                "clarification_needed": parsed_intent.clarification_needed,
                "catalog_query": parsed_intent.catalog_query,
                "filters": parsed_intent.filters,
                "session_id": actual_sid,
                "product_count": len(products),
                "returned_products": [
                    {
                        "product_id": p.get("product_id"),
                        "name": p.get("name"),
                        "price": p.get("price"),
                        "availability": p.get("availability"),
                    }
                    for p in products
                ],
                "reply_text": reply_text,
            }
            results.append(record)
            print(f"[{qid}] Done in {record['elapsed_seconds']}s | Products: {len(products)} | Reply: {reply_text[:60]}...")

    output_path = Path("tests/evaluation/phase4g_part_a_results.json")
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nAll 12 queries completed. Results saved to {output_path}")

if __name__ == "__main__":
    asyncio.run(run_part_a())

