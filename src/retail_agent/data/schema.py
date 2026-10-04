"""Canonical schema of the four `thelook_ecommerce` tables the agent may use.

This is the single source of truth for the mock database and for what the agent is told about the
data. The BigQuery backend can fetch the live schema; a test compares the two in phase 9.
`user_geom` (GEOGRAPHY) is deliberately left out: it duplicates latitude/longitude, which are PII.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str  # BigQuery type name: INT64, FLOAT64, STRING, TIMESTAMP
    description: str = ""
    mode: str = "NULLABLE"


def _c(name: str, type_: str, description: str = "") -> ColumnInfo:
    return ColumnInfo(name=name, type=type_, description=description)


TABLES: dict[str, list[ColumnInfo]] = {
    "users": [
        _c("id", "INT64", "Unique customer id"),
        _c("first_name", "STRING", "Customer first name"),
        _c("last_name", "STRING", "Customer last name"),
        _c("email", "STRING", "Customer email address"),
        _c("age", "INT64", "Customer age in years"),
        _c("gender", "STRING", "M or F"),
        _c("state", "STRING", "State or region of residence"),
        _c("street_address", "STRING", "Street address"),
        _c("postal_code", "STRING", "Postal code"),
        _c("city", "STRING", "City of residence"),
        _c("country", "STRING", "Country of residence"),
        _c("latitude", "FLOAT64", "Latitude of the address"),
        _c("longitude", "FLOAT64", "Longitude of the address"),
        _c("traffic_source", "STRING", "Acquisition channel (Search, Organic, Facebook, ...)"),
        _c("created_at", "TIMESTAMP", "When the customer registered"),
    ],
    "products": [
        _c("id", "INT64", "Unique product id"),
        _c("cost", "FLOAT64", "Unit cost to the retailer"),
        _c("category", "STRING", "Product category"),
        _c("name", "STRING", "Product name"),
        _c("brand", "STRING", "Brand"),
        _c("retail_price", "FLOAT64", "List price"),
        _c("department", "STRING", "Men or Women"),
        _c("sku", "STRING", "Stock keeping unit"),
        _c("distribution_center_id", "INT64", "Distribution center that stocks the product"),
    ],
    "orders": [
        _c("order_id", "INT64", "Unique order id"),
        _c("user_id", "INT64", "Customer who placed the order (users.id)"),
        _c("status", "STRING", "Complete, Shipped, Processing, Cancelled or Returned"),
        _c("gender", "STRING", "Customer gender at order time"),
        _c("created_at", "TIMESTAMP", "When the order was placed"),
        _c("returned_at", "TIMESTAMP", "When the order was returned, if it was"),
        _c("shipped_at", "TIMESTAMP", "When the order shipped"),
        _c("delivered_at", "TIMESTAMP", "When the order was delivered"),
        _c("num_of_item", "INT64", "Number of items in the order"),
    ],
    "order_items": [
        _c("id", "INT64", "Unique order item id"),
        _c("order_id", "INT64", "Parent order (orders.order_id)"),
        _c("user_id", "INT64", "Customer (users.id)"),
        _c("product_id", "INT64", "Product (products.id)"),
        _c("inventory_item_id", "INT64", "Physical inventory item"),
        _c("status", "STRING", "Complete, Shipped, Processing, Cancelled or Returned"),
        _c("created_at", "TIMESTAMP", "When the item was ordered"),
        _c("shipped_at", "TIMESTAMP", "When the item shipped"),
        _c("delivered_at", "TIMESTAMP", "When the item was delivered"),
        _c("returned_at", "TIMESTAMP", "When the item was returned"),
        _c("sale_price", "FLOAT64", "Price the customer paid for the item (revenue)"),
    ],
}
