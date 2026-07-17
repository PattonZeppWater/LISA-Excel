import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://vp-api.lylesgroup.com"


def get_token():
    resp = requests.post(
        f"{BASE_URL}/auth/token",
        json={
            "apiKey":    os.getenv("API_KEY"),
            "apiSecret": os.getenv("API_SECRET"),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["token"]
