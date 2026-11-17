#!/bin/bash
export PATH=~/.local/bin:$PATH
cd ~/ttalkkak
U=http://100.68.102.113:8787; T=$(cat ~/.ttalkkak/console-token); H="Authorization: Bearer $T"
D=ttalkkak-20261117-214304-c1; R=docs/results/project2
L=$R/kill-replica.log; W=$R/kill-replica-podwatch.log
{
echo "# 8절: 시험 중 복제본 하나 끄기"; date -Is
curl -s -H "$H" -H 'content-type: application/json' -d '{"enabled": false}' $U/api/autoscale; echo
kubectl scale deployment/$D -n ttalkkak --replicas=2; kubectl rollout status deployment/$D -n ttalkkak --timeout=600s
kubectl get pods -n ttalkkak --no-headers | grep "$D-" | grep -v epp
( kubectl get pods -n ttalkkak -w --output-watch-events 2>&1 | grep --line-buffered "$D-" | grep --line-buffered -v epp | while read l; do echo "$(date +%T.%3N) $l"; done > $W ) &
WP=$!
for i in $(seq 1 36); do c=$(curl -s 'http://127.0.0.1:9090/api/v1/query?query=count(vllm:num_requests_running)' | python3 -c "import json,sys;r=json.load(sys.stdin)['data']['result'];print(r[0]['value'][1] if r else 0)"); [ "$c" -ge 2 ] 2>/dev/null && break; sleep 5; done
echo '$ POST /api/loadtest {"max_users": 20, "autoscale_on": false}'
ID=$(curl -s -H "$H" -H 'content-type: application/json' -d '{"max_users": 20, "autoscale_on": false}' $U/api/loadtest | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))"); echo "id=$ID"
[ -n "$ID" ] || exit 1
KILLED=""
while true; do
  S=$(curl -s -H "$H" $U/api/loadtest/$ID)
  P=$(echo "$S" | python3 -c "import json,sys;d=json.load(sys.stdin);pn=d.get('probe_now') or {};print(d.get('phase'), pn.get('index',0), pn.get('users',''), 'error' if d.get('error') else '')")
  echo "$(date +%T) $P"
  set -- $P
  if [ -z "$KILLED" ] && [ "$1" = search ] && [ "${2:-0}" -ge 3 ]; then
    POD=$(kubectl get pods -n ttalkkak --no-headers | grep "$D-" | grep -v epp | grep Running | head -1 | awk '{print $1}')
    echo "$(date +%T.%3N) KILL $POD via POST /api/replicas/$POD/delete (탐색 ${2}번째 단계, ${3}명)"
    curl -s -H "$H" -X POST $U/api/replicas/$POD/delete; echo
    KILLED=$POD
  fi
  case "$P" in done*|*error*) break;; esac
  sleep 5
done
date -Is
cp runs/loadtests/$ID.json $R/kill-replica.json && echo "copied runs/loadtests/$ID.json"
kubectl rollout status deployment/$D -n ttalkkak --timeout=600s
sleep 5; kill $WP 2>/dev/null
} > $L 2>&1
