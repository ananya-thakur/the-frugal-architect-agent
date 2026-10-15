"""
Synthetic application service with known cost anti-pattern violations.

Anti-patterns planted for eval-harness test cases:
  - N+1 query: fetches each order's items individually in a loop
  - Missing pagination: loads all records in a single query
  - Full table scan: uses LIKE with a leading wildcard
  - Per-request initialization: creates a new DB connection on every call
  - Synchronous polling: busy-waits for an async result instead of using events

These are POSSIBLE-confidence findings (app-code, not IaC).
"""

import psycopg2


# Anti-pattern: per-request heavy initialization (no connection pool)
def get_orders_for_user(user_id: str) -> list[dict]:
    conn = psycopg2.connect("host=db user=app password=secret dbname=store")  # noqa: S106
    cursor = conn.cursor()

    # Anti-pattern: missing pagination — loads all rows unconditionally
    cursor.execute("SELECT id, status FROM orders WHERE user_id = %s", (user_id,))
    orders = cursor.fetchall()

    result = []
    for order_id, status in orders:
        # Anti-pattern: N+1 query — one SELECT per order instead of a JOIN
        cursor.execute("SELECT product_id, qty FROM order_items WHERE order_id = %s", (order_id,))
        items = cursor.fetchall()
        result.append({"order_id": order_id, "status": status, "items": items})

    conn.close()
    return result


def search_products(query: str) -> list[dict]:
    conn = psycopg2.connect("host=db user=app password=secret dbname=store")  # noqa: S106
    cursor = conn.cursor()

    # Anti-pattern: full table scan via leading-wildcard LIKE
    cursor.execute("SELECT * FROM products WHERE name LIKE %s", (f"%{query}%",))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1]} for r in rows]


def wait_for_job_result(job_id: str) -> dict:
    import time

    conn = psycopg2.connect("host=db user=app password=secret dbname=store")  # noqa: S106
    cursor = conn.cursor()

    # Anti-pattern: synchronous polling instead of SNS/SQS event
    while True:
        cursor.execute("SELECT status, result FROM jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
        if row and row[0] == "DONE":
            conn.close()
            return {"status": row[0], "result": row[1]}
        time.sleep(1)
