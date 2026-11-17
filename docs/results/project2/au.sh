#!/bin/bash
export PATH=~/.local/bin:/usr/lib/wsl/lib:$PATH
cd ~/ttalkkak; R=docs/results/project2
U=http://100.68.102.113:8787; T=$(cat ~/.ttalkkak/console-token); H="Authorization: Bearer $T"
D=ttalkkak-20261117-214304-c1
L=$R/autoscale.log; S=$R/autoscale-samples.jsonl; W=$R/autoscale-podwatch.log
{
echo "# 9절 자동 증감 1 -> 2 -> 1"; date -Is
echo "\$ kubectl scale deployment/$D -n ttalkkak --replicas=1"
kubectl scale deployment/$D -n ttalkkak --replicas=1
for i in $(seq 1 120); do L2=$(kubectl get pods -n ttalkkak --no-headers | grep "$D-" | grep -v epp); [ "$(echo "$L2" | grep -c Terminating)" -eq 0 ] && [ "$(echo "$L2" | grep -c '1/1 *Running')" -eq 1 ] && break; sleep 5; done
echo "$L2"
echo '$ POST /api/autoscale {"enabled": true}'; curl -s -H "$H" -H 'content-type: application/json' -d '{"enabled": true}' $U/api/autoscale; echo
kubectl get configmap ttalkkak-autoscale -n ttalkkak -o jsonpath='{.data}'; echo
echo "start state: $(date -Is) replicas=1 gpu=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
( kubectl get pods -n ttalkkak -w --output-watch-events 2>&1 | grep --line-buffered "$D-" | grep --line-buffered -v epp | while read l; do echo "$(date +%T.%3N) $l"; done > $W ) &
WP=$!
( while true; do
    j=$(curl -s -m 8 -H "$H" $U/api/live)
    desired=$(kubectl get deploy/$D -n ttalkkak -o jsonpath='{.spec.replicas}/{.status.readyReplicas}' 2>/dev/null)
    echo "$j" | python3 -c "
import json,sys,datetime
d=json.load(sys.stdin);c=d['capacity'];h=d['headline'];s=c['signals']
print(json.dumps({'t':datetime.datetime.now().strftime('%H:%M:%S'),'deploy':'$desired','pct':c['pct'],'now_tpm':c['now_tpm'],'max_tpm':c['max_tpm'],'state':c['state'],'waiting':s['waiting'],'ready':s['ready_replicas'],'idle':s['idle_replicas'],'concurrent':h['concurrent'],'ttft_p99_ms':h['ttft_p99_ms'],'itl_p99_ms':h['itl_p99_ms'],'out_tok_s':h['output_tok_s'],'errors':d.get('errors')},ensure_ascii=False))" 2>/dev/null
    sleep 10; done >> $S ) &
SP=$!
sleep 60
echo '$ POST /api/loadtest {"max_users": 20, "autoscale_on": true}'
ID=$(curl -s -H "$H" -H 'content-type: application/json' -d '{"max_users": 20, "autoscale_on": true}' $U/api/loadtest | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))"); echo "id=$ID $(date -Is)"
while true; do P=$(curl -s -H "$H" $U/api/loadtest/$ID | python3 -c "import json,sys;d=json.load(sys.stdin);pn=d.get('probe_now') or {};print(d.get('phase'),pn.get('users',''),'error' if d.get('error') else '')"); echo "$(date +%T) $P"; case "$P" in done*|*error*) break;; esac; sleep 10; done
echo "test end: $(date -Is)"; cp runs/loadtests/$ID.json $R/autoscale-loadtest.json
# 감소를 기다림: 1개로 돌아온 뒤 2분 더, 최대 30분
for i in $(seq 1 180); do r=$(kubectl get deploy/$D -n ttalkkak -o jsonpath='{.spec.replicas}'); [ "$r" = 1 ] && [ -n "$DOWN" ] && [ $(( $(date +%s) - DOWN )) -ge 120 ] && break; [ "$r" = 1 ] && [ -z "$DOWN" ] && [ -n "$UPSEEN" ] && DOWN=$(date +%s) && echo "back to 1: $(date -Is)"; [ "$r" = 2 ] && UPSEEN=1; sleep 10; done
echo "wait end: $(date -Is) replicas=$(kubectl get deploy/$D -n ttalkkak -o jsonpath='{.spec.replicas}')"
kill $SP $WP 2>/dev/null
curl -s -H "$H" $U/api/autoscale/decisions > $R/autoscale-decisions.json
python3 -c "
import json;d=json.load(open('$R/autoscale-decisions.json'))
for x in d:
  if x['action']!='hold' or x['t']>'2026-11-17T15:4': print(x)" | tail -60
} > $L 2>&1
