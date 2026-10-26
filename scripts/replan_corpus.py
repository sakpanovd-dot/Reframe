"""Fast planner iteration using saved detections; no additional inference."""
import json
import sys
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reframe.contracts import AnalysisResult, Sample, Detection
from reframe.planner import make_plan
from reframe.presets import load_presets

path = Path("work/samples/diagnostics.json")
data = json.loads(path.read_text())
a = data["analysis"]
a["samples"] = [Sample(s["time"], s["scene"], [Detection(**o) for o in s["objects"]]) for s in a["samples"]]
data["plan"] = asdict(make_plan(AnalysisResult(**a), load_presets()["9:16"]))
data["analysis"] = asdict(AnalysisResult(**a))
path.write_text(json.dumps(data), encoding="utf-8")
