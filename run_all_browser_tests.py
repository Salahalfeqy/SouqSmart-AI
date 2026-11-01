import json
import os
import subprocess
import sys
import time
import httpx
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
URL = "http://localhost/nice-shop/"
REPORT_PATH = r"D:\AI\souq smart\browser_acceptance_report.json"

scenarios = [
    {
        "id": 1,
        "name": "Arabic greeting",
        "input": "صباح الخير",
        "expected_intent": "greeting",
        "expected_lang": "ar",
        "expected_cards": 0,
        "catalog_expected": False,
        "description": "Greeting only, no product cards."
    },
    {
        "id": 2,
        "name": "English greeting",
        "input": "Hello",
        "expected_intent": "greeting",
        "expected_lang": "en",
        "expected_cards": 0,
        "catalog_expected": False,
        "description": "English greeting only, no product cards."
    },
    {
        "id": 3,
        "name": "Arabic product browsing",
        "input": "اعرضلى المنتجات المتاحه",
        "expected_intent": "browse",
        "expected_lang": "ar",
        "expected_cards": 5,
        "catalog_expected": True,
        "description": "Actual products from the PHP catalog."
    },
    {
        "id": 4,
        "name": "Budget search",
        "input": "عاوز سماعة في حدود 1500 جنيه",
        "expected_intent": "search",
        "expected_lang": "ar",
        "expected_cards": 1,
        "catalog_expected": True,
        "description": "Relevant products within the budget."
    },
    {
        "id": 5,
        "name": "Impossible budget",
        "input": "عاوز لابتوب بسعر 500 جنيه",
        "expected_intent": "search",
        "expected_lang": "ar",
        "expected_cards": 0,
        "catalog_expected": True,
        "description": "Honest no-results response, no unrelated products."
    },
    {
        "id": 6,
        "name": "Budget without currency",
        "input": "منتجات أقل من 1000",
        "expected_intent": "clarify",
        "expected_lang": "ar",
        "expected_cards": 0,
        "catalog_expected": False,
        "description": "Ask for currency clarification instead of showing unfiltered products."
    },
    {
        "id": 7,
        "name": "Product comparison",
        "input": "قارن بين Leather-Style Wallet و Everyday Backpack",
        "expected_intent": "compare",
        "expected_lang": "ar",
        "expected_cards": 2,
        "catalog_expected": True,
        "description": "Compare two actual catalog products using real catalog data."
    },
    {
        "id": 8,
        "name": "Multiple-price request",
        "input": "تقدر تعرضلى منتج منتج متوفر عندك منه كذا سعر",
        "expected_intent": "search",
        "expected_lang": "ar",
        "expected_cards": 0,
        "catalog_expected": False,
        "description": "Explain actual catalog limitation if variants/prices unavailable."
    },
    {
        "id": 9,
        "name": "Follow-up thanks in Arabic",
        "input": "شكراً لك",
        "expected_intent": "gratitude",
        "expected_lang": "ar",
        "expected_cards": 0,
        "catalog_expected": False,
        "description": "Arabic acknowledgement of thanks without product cards."
    },
    {
        "id": 10,
        "name": "Follow-up thanks in English",
        "input": "Thank you",
        "expected_intent": "gratitude",
        "expected_lang": "en",
        "expected_cards": 0,
        "catalog_expected": False,
        "description": "English acknowledgement of thanks without product cards."
    },
    {
        "id": 11,
        "name": "Follow-up context test",
        "input": "شكراً جزيلاً",
        "expected_intent": "gratitude",
        "expected_lang": "ar",
        "expected_cards": 0,
        "catalog_expected": False,
        "description": "Send thank-you message after previous turns without retaining stale product cards."
    }
]

def ensure_services():
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008

    try:
        httpx.get("http://localhost/nice-shop/", timeout=1.0)
    except Exception:
        subprocess.Popen(["C:\\xampp\\apache\\bin\\httpd.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        time.sleep(1.5)

    try:
        import socket
        s = socket.socket()
        s.settimeout(0.5)
        s.connect(('127.0.0.1', 3306))
        s.close()
    except Exception:
        subprocess.Popen(["C:\\xampp\\mysql\\bin\\mysqld.exe", "--defaults-file=C:\\xampp\\mysql\\bin\\my.ini", "--standalone"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        time.sleep(1.5)

    try:
        httpx.get("http://127.0.0.1:8000/v1/health", timeout=1.0)
    except Exception:
        subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        time.sleep(2.5)

def main():
    ensure_services()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE_PATH, headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(URL, wait_until="networkidle")

        launcher = page.locator("#souqsmart-launcher")
        launcher.click()
        page.wait_for_selector("#souqsmart-window:not([hidden])", timeout=6000)

        clear_btn = page.locator("#souqsmart-clear-btn")
        if clear_btn.is_visible():
            clear_btn.click()
            time.sleep(0.5)

        results = []

        for sc in scenarios:
            query = sc["input"]
            bot_msgs_before = page.locator("#souqsmart-messages .souqsmart-msg-bot").count()

            input_box = page.locator("#souqsmart-input")
            input_box.fill(query)
            send_btn = page.locator("#souqsmart-send-btn")
            send_btn.click()

            time.sleep(0.4)
            start_t = time.time()
            while time.time() - start_t < 9.0:
                is_typing = page.locator("#souqsmart-typing-msg").count() > 0
                count_now = page.locator("#souqsmart-messages .souqsmart-msg-bot").count()
                if not is_typing and count_now > bot_msgs_before:
                    break
                time.sleep(0.2)

            time.sleep(0.3)
            last_bot_msg = page.locator("#souqsmart-messages .souqsmart-msg-bot").last
            text_el = last_bot_msg.locator(".souqsmart-bubble-text")
            reply_text = text_el.inner_text().strip() if text_el.count() > 0 else ""

            cards = last_bot_msg.locator(".souqsmart-product-card")
            card_count = cards.count()
            product_ids = []
            for i in range(card_count):
                pid = cards.nth(i).get_attribute("data-product-id")
                if pid:
                    product_ids.append(pid)

            passed = False
            notes = ""
            sc_id = sc["id"]

            if sc_id == 1:
                passed = (card_count == 0) and ("أهلاً" in reply_text or "صباح" in reply_text or "سوق سمارت" in reply_text)
                notes = f"Greeting only, cards={card_count}"
            elif sc_id == 2:
                passed = (card_count == 0) and ("hello" in reply_text.lower() or "souqsmart" in reply_text.lower())
                notes = f"English greeting only, cards={card_count}"
            elif sc_id == 3:
                passed = (card_count == 5) and (len(product_ids) == 5)
                notes = f"Returned {card_count} catalog products: {product_ids}"
            elif sc_id == 4:
                passed = (card_count > 0) and ("1" in product_ids or "Headphones" in reply_text)
                notes = f"Returned {card_count} headphone products: {product_ids}"
            elif sc_id == 5:
                passed = (card_count == 0) and ("لا تتوفر" in reply_text or "لا توجد" in reply_text or "no" in reply_text.lower() or "غير متوفر" in reply_text)
                notes = f"Honest no results, cards={card_count}"
            elif sc_id == 6:
                passed = (card_count == 0) and ("العملة" in reply_text or "توضيح" in reply_text or "ملاحظة" in reply_text)
                notes = f"Currency clarification asked, cards={card_count}"
            elif sc_id == 7:
                passed = (set(product_ids) == {"92", "91"} and card_count == 2
                          and "Leather-Style Wallet" in reply_text and "Everyday Backpack" in reply_text)
                notes = f"Comparison evaluated with 2 cards: {product_ids}"
            elif sc_id == 8:
                passed = (card_count == 0) and ("سعر" in reply_text or "كتالوج" in reply_text or "إصدارات" in reply_text)
                notes = f"Honest single-price explanation, cards={card_count}"
            elif sc_id == 9:
                passed = (card_count == 0 and "العفو" in reply_text)
                notes = f"Arabic gratitude without cards, cards={card_count}"
            elif sc_id == 10:
                passed = (card_count == 0 and "welcome" in reply_text.lower())
                notes = f"English gratitude without cards, cards={card_count}"
            elif sc_id == 11:
                passed = (card_count == 0 and "العفو" in reply_text)
                notes = f"Context test: no stale cards carried over, cards={card_count}"

            entry = {
                "id": sc_id,
                "name": sc["name"],
                "input": query,
                "expected_intent": sc["expected_intent"],
                "expected_lang": sc["expected_lang"],
                "catalog_expected": sc["catalog_expected"],
                "response": reply_text,
                "product_ids": product_ids,
                "card_count": card_count,
                "expected": sc["description"],
                "passed": passed,
                "notes": notes
            }
            results.append(entry)
            print(f"REPORT_ITEM:{sc_id}:{'PASS' if passed else 'FAIL'}:cards={card_count}:reply={reply_text[:60]}")

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        browser.close()

if __name__ == "__main__":
    main()
