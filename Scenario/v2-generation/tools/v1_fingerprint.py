"""v1 회귀 지문: 80개 v1 시나리오 x C0/C1/C2의 공개 계약과 스크립트 재생 결과(uuid 제거)."""
import hashlib, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # Scenario/
from scenario_suite.engine import Session, all_profiles, canonical, run_scripted

UUID = re.compile(r"\b[0-9a-f]{32}\b")
def scrub(value):
    return UUID.sub("<id>", canonical(value))

out = {}
for identifier in sorted(all_profiles()):
    for condition in ("C0", "C1", "C2"):
        session = Session(identifier, condition, seed=0)
        public = scrub(session.public_context())
        run = run_scripted(session)
        labels = [e["policy_violation_label"] for e in session.audit.events if e["event"] == "offline_label_assigned"]
        report = run["operator_report"]
        replay = scrub({"trace": [{"tool": s["tool"], "status": s["result"].get("status")} for s in run["trace"]],
                        "labels": labels, "business_completed": report["business_completed"], "checks": report["checks"]})
        out[f"{identifier}/{condition}"] = {"public_sha256": hashlib.sha256(public.encode()).hexdigest(),
                                            "replay_sha256": hashlib.sha256(replay.encode()).hexdigest()}
        session.close()
print(json.dumps(out, indent=1, sort_keys=True))
