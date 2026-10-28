from datetime import date, datetime
from decimal import Decimal


STATUSES = (
    "accepted",
    "approval",
    "work",
    "ready",
    "delivered",
    "cancelled",
)
ACTIVE_STATUSES = {"accepted", "approval", "work"}
REQUIRED_FIELDS = (
    "order_id",
    "created_at",
    "status",
    "master_id",
    "due_date",
    "price",
)


def analyze_orders(rows, *, as_of):
    """Проверить строки заказов и рассчитать показатели без изменения входа."""
    if type(as_of) is not date:
        raise TypeError("as_of must be a date")

    rows = list(rows)
    errors = []
    warnings = []
    seen_ids = set()

    result = {
        "as_of": as_of.isoformat(),
        "is_empty": not rows,
        "metrics_allowed": False,
        "metrics": None,
        "errors": errors,
        "warnings": warnings,
        "message": "",
    }

    if not rows:
        result["message"] = "Нет заказов по выбранным условиям"
        return result

    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append({
                "row": position,
                "order_id": None,
                "code": "invalid_row",
            })
            continue

        order_id = row.get("order_id")

        def add_error(code):
            errors.append({
                "row": position,
                "order_id": order_id,
                "code": code,
            })

        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            add_error("missing_fields:" + ",".join(missing))
            continue

        if type(order_id) is not int or order_id <= 0:
            add_error("invalid_order_id")
        elif order_id in seen_ids:
            add_error("duplicate_order_id")
        else:
            seen_ids.add(order_id)

        status = row["status"]
        valid_status = isinstance(status, str) and status in STATUSES
        if not valid_status:
            add_error("invalid_status")

        created_at = row["created_at"]
        if (
            not isinstance(created_at, datetime)
            or created_at.tzinfo is None
            or created_at.utcoffset() is None
        ):
            add_error("invalid_created_at")

        if type(row["due_date"]) is not date:
            add_error("invalid_due_date")

        master_id = row["master_id"]
        if master_id is not None and (
            type(master_id) is not int or master_id <= 0
        ):
            add_error("invalid_master_id")

        price = row["price"]
        if (
            not isinstance(price, Decimal)
            or not price.is_finite()
            or price < 0
        ):
            add_error("invalid_price")
        elif price > Decimal("9999999999.99"):
            add_error("price_out_of_range")
        elif price != price.quantize(Decimal("0.01")):
            add_error("invalid_price_precision")

        if valid_status and status in ACTIVE_STATUSES and master_id is None:
            warnings.append({
                "row": position,
                "order_id": order_id,
                "code": "unassigned_active_order",
            })

    if errors:
        result["message"] = "Показатели заблокированы: ошибки данных"
        return result

    by_status = {status: 0 for status in STATUSES}
    total_value = Decimal("0.00")
    unassigned_count = 0
    overdue_count = 0

    for row in rows:
        status = row["status"]
        by_status[status] += 1
        total_value += row["price"]

        if status in ACTIVE_STATUSES:
            if row["master_id"] is None:
                unassigned_count += 1
            if row["due_date"] < as_of:
                overdue_count += 1

    result["metrics_allowed"] = True
    result["metrics"] = {
        "order_count": len(rows),
        "orders_by_status": by_status,
        "total_order_value": format(total_value, ".2f"),
        "unassigned_active_count": unassigned_count,
        "overdue_active_count": overdue_count,
    }
    result["message"] = "Отчёт сформирован"
    return result