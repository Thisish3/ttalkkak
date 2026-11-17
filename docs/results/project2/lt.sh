#!/bin/bash
# usage: lt.sh <replicas> <label> [probe_seconds|""] [max_users]
export PATH=~/.local/bin:/usr/lib/wsl/lib:$PATH
cd ~/ttalkkak
N=$1; LABEL=$2; PS=${3:-}; MU=${4:-20}
U=http://100.68.102.113:8787; T=$(cat ~/.ttalkkak/console-token); H="Authorization: Bearer $T"
D=ttalkkak-20261117-214304-c1
L=docs/results/project2/loadtest-$LABEL.log
{
echo "# 부하 시험 $LABEL (복제본 $N, probe_seconds=${PS:-기본 30}, max_users=$MU)"; date -Is
echo '$ POST /api/autoscale {"enabled": false}'; curl -s -H "$H" -H 'content-type: application/json' -d '{"enabled": false}' $U/api/autoscale; echo
kubectl get configmap ttalkkak-autoscale -n ttalkkak -o jsonpath='{.data.enabled}'; echo
echo "\$ kubectl scale deployment/$D -n ttalkkak --replicas=$N"
kubectl scale deployment/$D -n ttalkkak --replicas=$N
kubectl rollout status deployment/$D -n ttalkkak --timeout=600s
# R24: Terminating 파드가 없고 N개가 모두 Ready일 때까지, 새 파드가 떴으면 120초 더
NEW=0
for i in $(seq 1 120); do
  L2=$(kubectl get pods -n ttalkkak -l ttalkkak.run=20261117-214304 --no-headers | grep -v epp)
  t=$(echo "$L2" | grep -c Terminating); r=$(echo "$L2" | grep -c "1/1 *Running")
  echo "$L2" | awk '{print $5}' | grep -qE "^[0-9]+s$|^1m[0-9]*s$" && NEW=1
  [ "$t" -eq 0 ] && [ "$r" -eq "$N" ] && break; sleep 5
done
echo "$L2"
[ "$NEW" -eq 1 ] && { echo "새 파드가 있어 120초 더 기다림"; sleep 120; }
for i in $(seq 1 36); do c=$(curl -s 'http://127.0.0.1:9090/api/v1/query?query=count(vllm:num_requests_running)' | python3 -c "import json,sys;r=json.load(sys.stdin)['data']['result'];print(r[0]['value'][1] if r else 0)"); [ "$c" -ge "$N" ] 2>/dev/null && break; sleep 5; done
echo "prometheus vllm series: $c"
B="{\"max_users\": $MU, \"autoscale_on\": false${PS:+, \"probe_seconds\": $PS}}"
SMP=docs/results/project2/loadtest-$LABEL-samples.log
( echo "time gpu_util_pct gpu_mem_mib per_pod_running"; while true; do g=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits | tr -d ' '); r=$(curl -s 'http://127.0.0.1:9090/api/v1/query?query=vllm:num_requests_running' | python3 -c "import json,sys;print(';'.join(m['metric'].get('pod','?')[-5:]+'='+m['value'][1] for m in json.load(sys.stdin)['data']['result']))" 2>/dev/null); echo "$(date +%T) $g $r"; sleep 5; done > $SMP ) &
SP=$!
echo "\$ POST /api/loadtest $B"
R=$(curl -s -H "$H" -H 'content-type: application/json' -d "$B" $U/api/loadtest); echo "$R"
ID=$(echo "$R" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('id') or d.get('tid') or '')")
echo "id=$ID"
[ -n "$ID" ] || exit 1
while true; do
  S=$(curl -s -H "$H" $U/api/loadtest/$ID)
  P=$(echo "$S" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('phase'), 'error' if d.get('error') else '')" 2>/dev/null)
  echo "$(date +%T) $P"
  case "$P" in done*|*error*) break;; esac
  sleep 20
done
date -Is
echo "\$ kubectl get configmap ttalkkak-capacity -n ttalkkak -o jsonpath='{.data.capacity\.json}'"
kubectl get configmap ttalkkak-capacity -n ttalkkak -o jsonpath='{.data.capacity\.json}'; echo
kill $SP 2>/dev/null
cp runs/loadtests/$ID.json docs/results/project2/loadtest-$LABEL.json 2>/dev/null && echo "copied runs/loadtests/$ID.json"
} > $L 2>&1
tail -6 $L
