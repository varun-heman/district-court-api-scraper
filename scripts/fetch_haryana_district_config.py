"""
Fetches Haryana court complex codes directly from eCourts and builds haryana_district_courts.json.
Run from repo root: python scripts/fetch_haryana_district_config.py
"""
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STATE_CODE = "14"
BASE_URL = "https://services.ecourts.gov.in/ecourtindia_v6/"

DISTRICTS = {
    "Ambala": "3",
    "Bhiwani": "4",
    "Charkhi Dadri": "88",
    "Faridabad": "5",
    "Fatehabad": "15",
    "Gurugram": "6",
    "Hisar": "7",
    "Jhajjar": "8",
    "Jind": "9",
    "Kaithal": "16",
    "Karnal": "1",
    "Kurukshetra": "10",
    "Mahendragarh": "11",
    "Nuh": "19",
    "Palwal": "23",
    "Panchkula": "14",
    "Panipat": "18",
    "Rewari": "17",
    "Rohtak": "12",
    "Sirsa": "2",
    "Sonipat": "13",
    "Yamunanagar": "20",
}

def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
    })
    # Hit main page to get cookies
    s.get(BASE_URL + "?p=casestatus/index", timeout=15)
    return s

def fetch_complexes(session, dist_code):
    resp = session.post(
        BASE_URL,
        params={"p": "casestatus/fillcomplex"},
        data={"state_code": STATE_CODE, "dist_code": dist_code, "ajax_req": "true", "app_token": ""},
        timeout=15,
    )
    data = resp.json()
    html = data.get("court_complex_list") or data.get("complex_list") or ""
    soup = BeautifulSoup(html, "html.parser")
    complexes = {}
    for opt in soup.find_all("option"):
        val = opt.get("value", "").strip()
        name = opt.get_text(strip=True)
        if val and name and name.lower() != "select court complex":
            complexes[name] = {"crtvalue": val}
    return complexes

def main():
    print("Fetching Haryana court complexes from eCourts...")
    session = get_session()
    
    districts_out = {}
    for dist_name, dist_code in DISTRICTS.items():
        print(f"  {dist_name} (code={dist_code})...", end=" ", flush=True)
        try:
            complexes = fetch_complexes(session, dist_code)
            print(f"{len(complexes)} complexes")
            districts_out[dist_name] = {"dstvalue": dist_code, "court_complexes": complexes}
        except Exception as e:
            print(f"ERROR: {e}")
            districts_out[dist_name] = {"dstvalue": dist_code, "court_complexes": {}}
        time.sleep(0.5)
    
    output = {"Haryana": {"stvalue": STATE_CODE, "districts": districts_out}}
    out_path = Path("configs/haryana_district_courts.json")
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    
    total = sum(len(d["court_complexes"]) for d in districts_out.values())
    print(f"\nWritten: {out_path}")
    print(f"  {len(districts_out)} districts, {total} court complexes")

if __name__ == "__main__":
    main()
