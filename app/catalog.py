from abc import ABC, abstractmethod
from typing import Any
import httpx
from app.schemas import Product
from pydantic import ValidationError


class CatalogError(Exception):
    pass


class CatalogTimeout(CatalogError):
    pass


class CatalogProvider(ABC):
    @abstractmethod
    async def search(self, payload: dict[str, Any]) -> tuple[list[Product], int]:
        raise NotImplementedError

    @abstractmethod
    async def products(self, ids: list[str]) -> tuple[list[Product], list[str]]:
        raise NotImplementedError


class FixtureProvider(CatalogProvider):
    """Clearly labelled fictional data. Never use for a live NiceShop deployment."""
    def __init__(self) -> None:
        self.products_by_id = {
            "fixture-101": Product(product_id="fixture-101", name="Fixture Nova Laptop", brand="FixtureBrand", category="laptops", price={"amount": "1299.99", "currency": "USD"}, availability={"status": "in_stock"}, attributes={"RAM": "16 GB", "Storage": "512 GB SSD"}, url="https://example.invalid/products/fixture-101", image_url=None, updated_at="2026-10-28T10:00:00Z"),
            "fixture-102": Product(product_id="fixture-102", name="Fixture Orbit Laptop", brand="FixtureBrand", category="laptops", price={"amount": "1499.99", "currency": "USD"}, availability={"status": "in_stock"}, attributes={"RAM": "32 GB", "Storage": "1 TB SSD"}, url="https://example.invalid/products/fixture-102", image_url=None, updated_at="2026-10-28T10:00:00Z"),
            "fixture-201": Product(product_id="fixture-201", name="Fixture Spark Phone", brand="FixtureMobile", category="phones", price={"amount": "799.00", "currency": "USD"}, availability={"status": "out_of_stock"}, attributes={"Storage": "128 GB"}, url="https://example.invalid/products/fixture-201", image_url=None, updated_at="2026-10-28T10:00:00Z"),
        }

    async def search(self, payload: dict[str, Any]) -> tuple[list[Product], int]:
        query = payload["query"].lower()
        results = [p for p in self.products_by_id.values() if query in " ".join([p.name, p.brand or "", p.category or "", *p.attributes.values()]).lower()]
        filters = payload.get("filters", {})
        if filters.get("category"):
            results = [p for p in results if p.category == filters["category"]]
        if filters.get("brand"):
            results = [p for p in results if p.brand == filters["brand"]]
        if filters.get("in_stock_only"):
            results = [p for p in results if p.availability.status == "in_stock"]
        if filters.get("price_min"):
            results = [p for p in results if float(p.price.amount) >= float(filters["price_min"])]
        if filters.get("price_max"):
            results = [p for p in results if float(p.price.amount) <= float(filters["price_max"])]
        total = len(results)
        results = results[payload["offset"]:payload["offset"] + payload["limit"]]
        return results, total

    async def products(self, ids: list[str]) -> tuple[list[Product], list[str]]:
        found = [self.products_by_id[item] for item in ids if item in self.products_by_id]
        missing = [item for item in ids if item not in self.products_by_id]
        return found, missing


class PHPProvider(CatalogProvider):
    def __init__(self, base_url: str, service_key: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Service-Key": service_key}
        self.timeout = timeout

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, headers=self.headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise CatalogTimeout() from exc
        except httpx.HTTPError as exc:
            raise CatalogError(str(exc)) from exc
        if response.status_code >= 400:
            raise CatalogError(f"catalog HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise CatalogError("catalog returned invalid JSON") from exc

    async def search(self, payload: dict[str, Any]) -> tuple[list[Product], int]:
        php_payload = {
            "query": payload.get("query", ""),
            "limit": payload.get("limit", 10),
            "offset": payload.get("offset", 0),
        }
        filters = payload.get("filters", {})
        if filters.get("category"):
            php_payload["category"] = filters["category"]
        if filters.get("brand"):
            php_payload["brand"] = filters["brand"]
        if filters.get("in_stock_only"):
            php_payload["availability"] = "in_stock"
        if filters.get("price_min") is not None:
            php_payload["price_min"] = filters["price_min"]
        if filters.get("price_max") is not None:
            php_payload["price_max"] = filters["price_max"]
        data = await self._request("POST", f"{self.base_url}/internal/catalog/search", json=php_payload)
        try:
            products = [Product.model_validate(item) for item in data.get("products", data.get("results", []))]
        except ValidationError as exc:
            raise CatalogError("catalog product contract is invalid") from exc
        return products, int(data.get("total", len(products)))

    async def products(self, ids: list[str]) -> tuple[list[Product], list[str]]:
        data = await self._request("GET", f"{self.base_url}/internal/catalog/products", params={"ids": ",".join(ids)})
        returned = {item["product_id"]: item for item in data.get("products", [])}
        try:
            products = [Product.model_validate(returned[item]) for item in ids if item in returned]
        except ValidationError as exc:
            raise CatalogError("catalog product contract is invalid") from exc
        missing = list(data.get("missing_ids", [item for item in ids if item not in returned]))
        return products, missing
