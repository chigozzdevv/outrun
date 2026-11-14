"""Reproduce NearStats' dated interface routing exhibit from its CC BY 4.0 response."""
from compute.src.paths import ROOT, CODE_ROOT
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path


DATE = "2026-11-02"
raw = gzip.decompress((ROOT / f"data/verified/nearstats-integrators-{DATE}.json.gz").read_bytes())
data = json.loads(raw)
full, late = (data["windows"][k] for k in ("30d", "7d"))
assert data["ready"] and not data["warning"] and data["license"] == "CC-BY-4.0"
assert full["available"] and late["available"] and not full["warning"] and not late["warning"]
assert full["startsAt"] < late["startsAt"] < full["endsAt"] == late["endsAt"] == data["generatedAt"]
def check_window(w):
    indexed = {r["slug"]: r for r in w["rows"]}
    assert len(indexed) == len(w["rows"])
    assert sum(r["swaps"] for r in indexed.values()) == w["totalSwaps"]
    for key, predicate in [("totalVolumeUsd", lambda r: True), ("thirdPartyVolumeUsd", lambda r: not r["untagged"] and not r["firstParty"]), ("firstPartyVolumeUsd", lambda r: r["firstParty"]), ("untaggedVolumeUsd", lambda r: r["untagged"])]:
        assert math.isclose(sum(r["volumeUsd"] for r in indexed.values() if predicate(r)), w[key], abs_tol=.01, rel_tol=1e-12)
    return indexed
rows, recent = check_window(full), check_window(late)
assert recent.keys() <= rows.keys()
csv_rows = []
for slug, r in rows.items():
    last = recent.get(slug)
    if last:
        assert (r["firstParty"], r["untagged"]) == (last["firstParty"], last["untagged"])
    late_n, late_v = (last["swaps"], last["volumeUsd"]) if last else (0, 0)
    early_n, early_v = r["swaps"] - late_n, r["volumeUsd"] - late_v
    assert early_n >= 0 and early_v >= -.000001
    if early_n == 0:
        assert abs(early_v) < .000001
        early_v = 0
    group = "untagged" if r["untagged"] else "first-party" if r["firstParty"] else "third-party"
    csv_rows.append(dict(interface_id=slug, interface_name=r["name"], allocation=group, provider_verified=r["verified"], early_swaps=early_n, late_swaps=late_n, early_usd=early_v, late_usd=late_v, full_swaps=r["swaps"], full_usd=r["volumeUsd"], window_start_utc=full["startsAt"], split_utc=late["startsAt"], end_utc=full["endsAt"], attribution="NearStats.org", license="CC-BY-4.0"))
third = [r for r in csv_rows if r["allocation"] == "third-party"]
leaders = sorted(third, key=lambda r: -r["full_usd"])[:5]
top = sum(r["full_usd"] for r in leaders)
early = [r for r in third if r["early_swaps"] > 0]
final = [r for r in third if r["late_swaps"] > 0]
both = [r for r in early if r["late_swaps"] > 0]
sum_return = sum(r["late_usd"] for r in both)
assert math.isclose(top/full["thirdPartyVolumeUsd"], full["top5Share"], rel_tol=1e-12)
summary = dict(
    window=dict(startUtc=full["startsAt"], splitUtc=late["startsAt"], endUtc=full["endsAt"], finalDayPartial=True),
    volumes=dict(allUsd=full["totalVolumeUsd"], thirdPartyUsd=full["thirdPartyVolumeUsd"], firstPartyUsd=full["firstPartyVolumeUsd"], untaggedUsd=full["untaggedVolumeUsd"], topFiveUsd=top, topFiveShareOfThirdParty=top/full["thirdPartyVolumeUsd"], topFiveShareOfAll=top/full["totalVolumeUsd"], untaggedShareOfAll=full["untaggedVolumeUsd"]/full["totalVolumeUsd"], totalSwaps=full["totalSwaps"], thirdPartyLabels=len(third), topFive=[dict(name=r["interface_name"], usd=r["full_usd"]) for r in leaders]),
    cohort=dict(earlyLabels=len(early), lateLabels=len(final), returningLabels=len(both), returningShareOfEarly=len(both)/len(early), lateVolumeUsd=late["thirdPartyVolumeUsd"], returningLateVolumeUsd=sum_return, returningShareOfLateVolume=sum_return/late["thirdPartyVolumeUsd"]),
    source=dict(url="https://nearstats.org/api/integrators.json", methodology="https://nearstats.org/integrators", attribution="NearStats.org", license=data["license"], licenseUrl="https://creativecommons.org/licenses/by/4.0/", capturedAt="2026-11-02T13:36:24.856Z", rawSha256=hashlib.sha256(raw).hexdigest(), scope="NearStats allocates successful 1Click Explorer swaps to one interface from fee recipients, referral codes and its registry. It values swaps at the larger USD leg. These figures describe the mix of this dataset; they do not cover all Intents settlement or establish earned fees or unique paying customers.", construction="Early counts and volumes subtract the seven-day observations from the thirty-day observations for each interface under the identical cutoff; a repeat label has at least one successful swap in both disjoint periods. The first period is longer and the final day is partial. Unlabeled but attributable tags remain in the third-party denominator. Untagged and first-party swaps are explicitly separated."))
out = ROOT / f"public/data/interface-routing-{DATE}.csv"
with out.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(csv_rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(csv_rows)
(ROOT / f"data/verified/interface-routing-{DATE}.json").write_text(json.dumps(summary, indent=2) + "\n")
