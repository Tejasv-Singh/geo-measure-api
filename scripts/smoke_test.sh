#!/usr/bin/env bash
# Upload the golden KML to a running API, wait for processing, and check the measured area.
# Usage: scripts/smoke_test.sh <golden_square.kml> [base_url]
set -euo pipefail

file="${1:?usage: smoke_test.sh <golden_square.kml> [base_url]}"
base="${2:-http://localhost:8000}"
python="${PYTHON:-python}"

json_field() {
  "$python" -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

upload=$(curl -fsS -F "file=@${file}" "${base}/api/files/")
id=$(json_field id <<<"$upload")
echo "uploaded ${id}"

status=""
for _ in $(seq 1 60); do
  detail=$(curl -fsS "${base}/api/files/${id}/")
  status=$(json_field status <<<"$detail")
  [[ "$status" == "COMPLETED" || "$status" == "FAILED" ]] && break
  sleep 1
done
echo "status ${status}"
if [[ "$status" != "COMPLETED" ]]; then
  echo "$detail"
  exit 1
fi

curl -fsS "${base}/api/files/${id}/measurements/" | "$python" -c '
import json, sys
area = json.load(sys.stdin)["items"][0]["value"]
error = abs(area - 1_000_000) / 1_000_000
print(f"area {area:.3f} m2, relative error {error:.2e}")
sys.exit(0 if error <= 1e-5 else 1)
'
