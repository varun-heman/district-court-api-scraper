"""Convert discovery.json output to the district_courts config format expected by batch-scrape."""
import json
import sys
from pathlib import Path


def convert(discovery_path: Path, state_name: str) -> dict:
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
    state_code = str(discovery["state_code"])
    districts = {}
    for d in discovery["districts"]:
        dist_name = d["district"]["label"]
        dist_code = d["district"]["value"]
        court_complexes = {}
        for cc in d["court_complexes"]:
            cc_name = cc["complex"]["label"]
            crtvalue = cc["complex"]["value"]
            establishments = {}
            for est in cc.get("establishments", []):
                establishments[est["label"]] = {"estvalue": est["value"]}
            court_complexes[cc_name] = {
                "crtvalue": crtvalue,
                "establishments": establishments,
            }
        districts[dist_name] = {
            "dstvalue": dist_code,
            "court_complexes": court_complexes,
        }
    return {state_name: {"stvalue": state_code, "districts": districts}}


if __name__ == "__main__":
    discovery_path = Path(sys.argv[1])
    state_name = sys.argv[2]
    output_path = Path(sys.argv[3])
    result = convert(discovery_path, state_name)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Written to {output_path}")
