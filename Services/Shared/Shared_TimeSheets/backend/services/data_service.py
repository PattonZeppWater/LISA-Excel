import requests

BASE_URL  = "https://vp-api.lylesgroup.com"
PAGE_SIZE = 1000


def fetch_all_units(token):
    """Paginate through all remaining-units records and return a flat list of dicts."""
    headers = {"Authorization": f"Bearer {token}"}
    rows    = []
    page    = 1

    while True:
        resp = requests.get(
            f"{BASE_URL}/api/v1/remaining-units",
            headers=headers,
            params={"page": page, "pageSize": PAGE_SIZE},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()

        for item in payload.get("data", []):
            rows.append({
                "Job":            item.get("Job", ""),
                "Phase":          item.get("Phase", ""),
                "RemainingUnits": item.get("RemainingUnits", 0),
            })

        if page >= payload.get("totalPages", 1):
            break
        page += 1

    return rows
