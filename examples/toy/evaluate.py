# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
import json
import sys

from omega_transform.harness import Candidate, CandidateError

try:
    v = Candidate(sys.argv[1]).call("value")
except CandidateError as e:
    print(json.dumps({"valid": False, "feedback": str(e)}))
    sys.exit(0)
ok = isinstance(v, (int, float)) and v <= 100
print(json.dumps({"score": v if ok else None, "valid": ok, "metrics": {"value": v},
                  "feedback": "" if ok else "value must be a number <= 100"}))
