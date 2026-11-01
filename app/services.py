from decimal import Decimal
from app.intents import (
    STORE_CURRENCY,
    ChatIntent,
    DeterministicIntentParser,
    IntentParser,
    IntentType,
    LLMIntentParser,
    ParsedIntent,
    get_intent_parser,
    extract_comparison_names,
    is_gratitude,
    is_arabic,
)
from app.schemas import Product


def parse_chat_intent(message: str, forced_lang: str = "auto") -> ParsedIntent:
    if is_gratitude(message) or extract_comparison_names(message):
        return DeterministicIntentParser().parse(message, forced_lang)
    parser = get_intent_parser()
    return parser.parse(message, forced_lang)


def rank_products(products: list[Product], query: str, sort: str) -> list[Product]:
    tokens = [token for token in query.lower().split() if token]

    def score(product: Product) -> tuple[int, int]:
        searchable = " ".join([product.name, product.brand or "", product.category or "", *product.attributes.values()]).lower()
        exact_hits = sum(searchable.count(token) for token in tokens)
        name_hits = sum(product.name.lower().count(token) for token in tokens)
        return exact_hits + name_hits, name_hits

    ranked = sorted(products, key=lambda p: (-score(p)[0], -score(p)[1], p.product_id))
    currencies = {p.price.currency for p in ranked}
    if sort == "price_asc" and len(currencies) <= 1:
        ranked.sort(key=lambda p: (Decimal(p.price.amount), p.product_id))
    elif sort == "price_desc" and len(currencies) <= 1:
        ranked.sort(key=lambda p: (Decimal(p.price.amount), p.product_id), reverse=True)
    return ranked


def compare_products(products: list[Product]) -> tuple[dict[str, object], list[dict[str, object]]]:
    currencies = {p.price.currency for p in products}
    derived: dict[str, object] = {"cheapest_product_id": None, "price_spread": None}
    if len(currencies) == 1:
        cheapest = min(products, key=lambda p: (Decimal(p.price.amount), p.product_id))
        prices = [Decimal(p.price.amount) for p in products]
        derived = {"cheapest_product_id": cheapest.product_id, "price_spread": format(max(prices) - min(prices), "f")}
    rows: list[dict[str, object]] = [
        {"attribute": "Price", "values": {p.product_id: f"{p.price.amount} {p.price.currency}" for p in products}, "differs": len({p.price.amount for p in products}) > 1},
        {"attribute": "Availability", "values": {p.product_id: p.availability.status for p in products}, "differs": len({p.availability.status for p in products}) > 1},
    ]
    names = sorted({key for p in products for key in p.attributes})
    for name in names:
        values = {p.product_id: p.attributes.get(name) for p in products}
        rows.append({"attribute": name, "values": {key: value for key, value in values.items() if value is not None}, "differs": len({value for value in values.values() if value is not None}) > 1})
    return derived, rows


def template_comparison(products: list[Product], derived: dict[str, object]) -> str:
    cheapest = derived["cheapest_product_id"]
    if cheapest:
        return f"Among the compared products, {cheapest} has the lowest listed price. Prices shown are structured catalog facts."
    return "The compared products use different currencies, so no cross-currency cheapest price or spread is calculated."


def is_laptop_query(intent: ParsedIntent) -> bool:
    q = (intent.catalog_query + " " + intent.clean_query).lower()
    return any(word in q for word in ("laptop", "لابتوب", "حاسوب", "كمبيوتر"))


def is_accessory_query(intent: ParsedIntent) -> bool:
    q = (intent.catalog_query + " " + intent.clean_query).lower()
    return any(word in q for word in (
        "stand", "حامل", "استاند",
        "case", "جراب", "كفر",
        "bag", "شنطة", "شنطه", "حقيبة",
        "charger", "شاحن",
        "cooler", "cooling", "مروحة", "تبريد",
        "mount", "قاعدة", "قاعده",
        "dock", "docking",
        "cable", "سلك", "وصلة", "وصله",
        "accessory", "accessories", "اكسسوار", "إكسسوار", "ملحق", "ملحقات"
    ))


def is_accessory_product(p: Product) -> bool:
    name = p.name.lower()
    category = (p.category or "").lower()
    accessory_keywords = (
        "stand", "mount", "case", "bag", "sleeve", "holder",
        "charger", "cooling", "cooler", "cable", "dock", "hub",
        "adapter", "strap", "cover"
    )
    return any(kw in name for kw in accessory_keywords) or category in ("accessories", "ملحقات")


def chat_reply(
    message: str,
    products: list[Product],
    *,
    language: str = "en",
    intent: ParsedIntent | None = None,
    accessories: list[Product] | None = None,
) -> str:
    """Deterministic reply used as a fallback when Gemini generation fails.
    This function remains unchanged and is called directly by `generate_natural_reply`
    when the LLM path cannot be used.
    """

    lower = message.lower()

    if intent and intent.intent_type == "gratitude":
        if language == "ar":
            return "العفو! سعيد بمساعدتك. هل تحتاج مساعدة في اختيار منتج آخر؟"
        return "You're welcome! I'm happy to help. Can I help you choose another product?"

    # 1. Comparison handling with honest result count
    is_compare = (intent and intent.is_compare) or (
        len(products) >= 2 and any(word in lower for word in ("cheaper", "compare", "comparison", "which"))
    )
    if is_compare:
        if len(products) >= 2:
            derived, _ = compare_products(products)
            items = ("، " if language == "ar" else ", ").join(
                f"{p.name} ({p.price.amount} {p.price.currency})" for p in products
            )
            if language == "ar":
                cheapest = derived.get("cheapest_product_id")
                if cheapest:
                    return f"مقارنة المنتجات: {items}. المنتج (كود {cheapest}) هو الأقل سعراً."
                return f"مقارنة المنتجات: {items}. تستخدم المنتجات عملات مختلفة، لذلك لم يتم حساب فارق السعر."
            return f"Compared products: {items}. {template_comparison(products, derived)}"
        if len(products) == 1:
            p = products[0]
            if language == "ar":
                return f"تم العثور على منتج واحد مطابق: {p.name} (كود {p.product_id}، بسعر {p.price.amount} {p.price.currency}). يلزم توفر منتجين على الأقل لإجراء مقارنة مباشرة."
            return f"Found 1 matching product: {p.name} (ID {p.product_id}, listed at {p.price.amount} {p.price.currency}). At least two comparable products are required to generate a side-by-side comparison."
        if language == "ar":
            return "لم يتم العثور على منتجات مطابقة في الكتالوج لإجراء المقارنة."
        return "No matching products were found in the catalog to compare."

    # 2. Standalone greeting
    if intent and intent.is_greeting and not products:
        if language == "ar":
            return "أهلاً بك! أنا سوق سمارت، مساعدك للتسوق الذكي. يمكنك سؤالي عن المنتجات، توفر المخزون، أو المقارنة بين الأسعار."
        return "Hello! I am SouqSmart, your AI shopping assistant. Ask me to recommend products, compare options, or verify what is currently in stock."

    # 3. Currency limitation note / clarification request (clearly distinguish unfiltered suggestions)
    if intent and intent.currency_note:
        if products:
            if language == "ar":
                items = "، ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
                return f"{intent.currency_note}\n\n[مقترحات عامة غير مصفاة بالميزانية]: {items}."
            items = ", ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
            return f"{intent.currency_note}\n\n[Unfiltered catalog suggestions]: {items}."
        if language == "ar":
            return f"{intent.currency_note}\n\nلم يتم العثور على منتجات في الكتالوج حالياً."
        return f"{intent.currency_note}\n\nNo matching products were found in the catalog."

    # 4. Laptop device vs accessory explanation
    if intent and is_laptop_query(intent) and not is_accessory_query(intent) and not products:
        if accessories:
            if language == "ar":
                items = "، ".join(f"{p.name} (كود {p.product_id}، بسعر {p.price.amount} {p.price.currency})" for p in accessories[:3])
                return f"لا تتوفر أجهزة لابتوب (حواسيب محمولة) في الكتالوج حالياً، ولكن يتوفر لدينا ملحقات ذات صلة مثل: {items}."
            items = ", ".join(f"{p.name} (ID {p.product_id}, listed at {p.price.amount} {p.price.currency})" for p in accessories[:3])
            return f"There are currently no laptop computers in our catalog, but related accessories are available, such as: {items}."
        if language == "ar":
            return "لا تتوفر أجهزة لابتوب (حواسيب محمولة) في الكتالوج حالياً."
        return "There are currently no laptop computers available in our catalog."

    # 5. Budget intent with store currency (EGP)
    if intent and intent.budget and intent.budget_currency == STORE_CURRENCY:
        if intent.filters.get("price_min"):
            min_val = intent.filters["price_min"]
            if products:
                if language == "ar":
                    items = "، ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
                    return f"إليك المنتجات المتاحة في الكتالوج بسعر {min_val} {intent.budget_currency} أو أكثر: {items}."
                items = ", ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
                return f"Here are products available in our catalog for {min_val} {intent.budget_currency} or more: {items}."
            if language == "ar":
                return f"لا توجد منتجات متاحة في الكتالوج بسعر {min_val} {intent.budget_currency} أو أكثر."
            return f"No products are currently available in the catalog for {min_val} {intent.budget_currency} or more."

        if products:
            if language == "ar":
                items = "، ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
                return f"إليك المنتجات المتاحة في الكتالوج بميزانية حتى {intent.budget} {intent.budget_currency}: {items}."
            items = ", ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
            return f"Here are products available in our catalog under {intent.budget} {intent.budget_currency}: {items}."
        if language == "ar":
            return f"لا توجد منتجات متاحة في الكتالوج بسعر أقل من {intent.budget} {intent.budget_currency}."
        return f"No products are currently available in the catalog under {intent.budget} {intent.budget_currency}."

    # 6. Stock intent
    if intent and intent.filters.get("in_stock_only"):
        if products:
            if language == "ar":
                items = "، ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
                return f"إليك المنتجات المتوفرة حالياً في المخزن: {items}."
            items = ", ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
            return f"Here are products currently in stock in our catalog: {items}."
        if language == "ar":
            return "لا توجد منتجات متوفرة في المخزن حالياً."
        return "No products are currently in stock in the catalog."

    # 7. General browse
    if intent and intent.is_browse:
        if products:
            if language == "ar":
                items = "، ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
                return f"أهلاً بك! إليك أبرز المنتجات المتاحة في متجرنا اليوم: {items}."
            items = ", ".join(f"{p.name} ({p.price.amount} {p.price.currency})" for p in products[:5])
            return f"Welcome! Here are top products available in our store: {items}."
        if language == "ar":
            return "لا توجد منتجات متاحة في الكتالوج حالياً."
        return "No products are currently available in the catalog."

    # 8. Specific search queries
    if any(word in lower for word in ("find", "search", "show", "recommend", "looking")):
        names = ", ".join(p.product_id for p in products[:5])
        return f"Catalog results are available for: {names}." if names else "No matching products were found in the catalog."

    if products:
        p = products[0]
        if language == "ar":
            return f"{p.name} (كود {p.product_id}) متوفر في الكتالوج؛ استخدم بيانات المنتج للتحقق من السعر والمخزون."
        return f"{p.name} (ID {p.product_id}) is listed in the catalog; use the structured product fields for verified price and availability."

    if language == "ar":
        return "لم يتم العثور على منتجات مطابقة في الكتالوج."
    return "No matching catalog products were found."
