import base64
import json
import os
import time

import requests
from flask import Flask, jsonify

app = Flask(__name__)

OWNER = "synthesz-svg"
REPO = "kis-prices-public"
BRANCH = "main"
PATH = "prices.json"

MAX_AGE = 600
RETRIES = 3


def github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kis-price-relay",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    token = os.environ.get("SOURCE_GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def fetch_once():
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}"

    r = requests.get(
        url,
        headers=github_headers(),
        params={
            "ref": BRANCH,
            "cb": str(time.time_ns()),
        },
        timeout=12,
    )
    r.raise_for_status()

    meta = r.json()

    raw = base64.b64decode(
        meta["content"]
    ).decode("utf-8")

    data = json.loads(raw)
    data["relay_source_sha"] = meta.get("sha")

    return data


def age_seconds(data):
    ts = data.get("generated_at_epoch")

    if not isinstance(ts, (int, float)):
        return None

    return max(
        0.0,
        time.time() - float(ts)
    )


def fetch_fresh():
    last_data = None
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            data = fetch_once()
            last_data = data

            age = age_seconds(data)

            if age is not None and age <= MAX_AGE:
                return data, age, attempt, None

            if age is None:
                last_error = "generated_at_epoch 없음"
            else:
                last_error = f"stale: {age:.1f}s"

        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"

        if attempt < RETRIES:
            time.sleep(1)

    return (
        last_data,
        age_seconds(last_data or {}),
        RETRIES,
        last_error,
    )


def no_cache(response):
    response.headers["Cache-Control"] = \
        "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["CDN-Cache-Control"] = "no-store"

    return response


@app.get("/")
def home():
    return no_cache(jsonify({
        "service": "KIS Price Relay",
        "status": "ok",
        "endpoint": "/api/prices"
    }))


@app.get("/health")
def health():
    return no_cache(jsonify({
        "status": "ok",
        "time_epoch": int(time.time())
    }))


@app.get("/api/prices")
def prices():
    data, age, attempts, error = fetch_fresh()

    if data is None:
        response = jsonify({
            "relay_status": "error",
            "relay_error": error,
            "relay_attempts": attempts
        })
        response.status_code = 502
        return no_cache(response)

    if age is None or age > MAX_AGE:
        response = jsonify({
            "relay_status": "stale",
            "relay_error": error,
            "relay_attempts": attempts,
            "relay_age_seconds":
                None if age is None else round(age, 1),
            "freshness_max_age_seconds": MAX_AGE,
            "source_updated_at":
                data.get("updated_at"),
            "source_generated_at_epoch":
                data.get("generated_at_epoch")
        })
        response.status_code = 503
        return no_cache(response)

    data["relay_status"] = "fresh"
    data["relay_age_seconds"] = round(age, 1)
    data["relay_attempts"] = attempts
    data["relay_checked_at_epoch"] = int(time.time())

    return no_cache(jsonify(data))
