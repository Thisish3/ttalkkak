#!/bin/bash
export PATH=$HOME/ttalkkak/.venv/bin:$HOME/.local/bin:/usr/lib/wsl/lib:$PATH SSL_CERT_FILE=$HOME/.ttalkkak/ca-bundle.pem
cd ~/ttalkkak; R=docs/results/project2
U=http://100.68.102.113:8787; T=$(cat ~/.ttalkkak/console-token); H="Authorization: Bearer $T"
D=ttalkkak-20261117-214304-c1
L=$R/autoscale.log; S=$R/autoscale-samples.jsonl; W=$R/autoscale-podwatch.log
spec() { kubectl get deploy/$D -n ttalkkak -o jsonpath='{.spec.replicas}'; }
{
echo "# 9절 자동 증감 (세 번째: 시험 한 번 더, 그래도 안 늘면 20명 고정 300초)"; date -Is
kubectl scale deployment/$D -n ttalkkak --replicas=1
for i in $(seq 1 120); do L2=$(kubectl get pods -n ttalkkak --no-headers | grep "$D-" | grep -v epp); [ "$(echo "$L2" | grep -c Terminating)" -eq 0 ] && [ "$(echo "$L2" | grep -c '1/1 *Running')" -eq 1 ] && break; sleep 5; done
echo "$L2"
curl -s -H "$H" -H 'content-type: application/json' -d '{"enabled": true}' $U/api/autoscale; echo
echo "start state: $(date -Is) spec=$(spec) gpu=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
( kubectl get pods -n ttalkkak -w --output-watch-events 2>&1 | grep --line-buffered "$D-" | grep --line-buffered -v epp | while read l; do echo "$(date +%T.%3N) $l"; done > $W ) &
WP=$!
( while true; do
    j=$(curl -s -m 8 -H "$H" $U/api/live); dd=$(kubectl get deploy/$D -n ttalkkak -o jsonpath='{.spec.replicas}/{.status.readyReplicas}' 2>/dev/null)
    echo "$j" | python3 -c "
import json,sys,datetime
d=json.load(sys.stdin);c=d['capacity'];h=d['headline'];s=c['signals']
print(json.dumps({'t':datetime.datetime.now().strftime('%H:%M:%S'),'deploy':'$dd','pct':c['pct'],'max_tpm':c['max_tpm'],'waiting':s['waiting'],'ready':s['ready_replicas'],'concurrent':h['concurrent'],'ttft_p99_ms':h['ttft_p99_ms'],'out_tok_s':h['output_tok_s']},ensure_ascii=False))" 2>/dev/null
    sleep 10; done >> $S ) &
SP=$!
sleep 30
echo '$ POST /api/loadtest {"max_users": 20, "autoscale_on": true}'
ID=$(curl -s -H "$H" -H 'content-type: application/json' -d '{"max_users": 20, "autoscale_on": true}' $U/api/loadtest | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))"); echo "id=$ID $(date -Is)"
while true; do P=$(curl -s -H "$H" $U/api/loadtest/$ID | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('phase'),'error' if d.get('error') else '')"); case "$P" in done*|*error*) break;; esac; sleep 10; done
echo "test end: $(date -Is) $P"; cp runs/loadtests/$ID.json $R/autoscale-loadtest.json
UP=""; for i in $(seq 1 15); do [ "$(spec)" = 2 ] && UP=1 && break; sleep 10; done
if [ -z "$UP" ]; then
  echo "시험 뒤 2분 30초 동안 늘지 않음 → 20명 고정 300초 (지시문 9절 2번)"
  umask 077
  ttalkkak ops tenants key-issue ttalkkak-loadtest > /tmp/fixed.key 2>&1
  python3 - <<'PY'
import json, re, subprocess, time, httpx
from pathlib import Path
from ttalkkak.gateway.manifests import tenants_json
from ttalkkak.tenants.store import TenantStore
st = TenantStore(Path.home()/".ttalkkak/tenants.db")
m = {"apiVersion":"v1","kind":"Secret","metadata":{"name":"ttalkkak-tenants","namespace":"ttalkkak"},"type":"Opaque","stringData":{"tenants.json":tenants_json(st)}}
print("secret apply rc", subprocess.run(["kubectl","apply","-f","-"],input=json.dumps(m),text=True,capture_output=True).returncode)
key = re.findall(r"tk_[A-Za-z0-9_-]+", Path("/tmp/fixed.key").read_text())[-1]
t0=time.time()
while time.time()-t0<180:
    if httpx.get("https://ttalkkak.local/v1/models",headers={"Authorization":f"Bearer {key}"},timeout=10).status_code==200: break
    time.sleep(5)
print(f"key accepted after {time.time()-t0:.0f}s")
from ttalkkak.cost.runner import goodput_cmd
from ttalkkak.live.loadtest import DATA
cmd = goodput_cmd("https://ttalkkak.local/v1","Qwen/Qwen2.5-1.5B-Instruct-AWQ",DATA,Path("docs/results/project2/autoscale-fixed20.guidellm.json"),
    ttft_ms=1000,itl_ms=None,attainment=0.95,max_streams=20,probe_seconds=300,warmup_seconds=5,tolerance=0.2,max_probes=1,api_key=key,validate=False)
i=cmd.index("--profile"); cmd[i+1]="kind=concurrent"; cmd[i+2:i+2]=["--override","profile.streams","20"]
print("start fixed load", time.strftime("%H:%M:%S"), flush=True)
r=subprocess.run(cmd,capture_output=True,text=True)
print("fixed load rc", r.returncode, time.strftime("%H:%M:%S"))
print(re.sub(r"tk_[A-Za-z0-9_-]+","<KEY>",(r.stdout+r.stderr)[-1500:]))
PY
  K=$(grep -oE "tk_[A-Za-z0-9_-]+" /tmp/fixed.key | tail -1)
  ttalkkak ops tenants key-revoke "$(echo $K | sed -E 's/^(tk_ttalkkak-loadtest_.{8}).*/\1/')" 2>&1 | sed -E 's/tk_[A-Za-z0-9_-]+/<접두어>/g'
  rm -f /tmp/fixed.key
  python3 -c "
import json,subprocess
from pathlib import Path
from ttalkkak.gateway.manifests import tenants_json
from ttalkkak.tenants.store import TenantStore
m={'apiVersion':'v1','kind':'Secret','metadata':{'name':'ttalkkak-tenants','namespace':'ttalkkak'},'type':'Opaque','stringData':{'tenants.json':tenants_json(TenantStore(Path.home()/'.ttalkkak/tenants.db'))}}
print('secret re-apply rc', subprocess.run(['kubectl','apply','-f','-'],input=json.dumps(m),text=True,capture_output=True).returncode)"
fi
for i in $(seq 1 180); do r=$(spec); [ "$r" = 2 ] && UP=1; [ "$r" = 1 ] && [ -n "$UP" ] && [ -z "$DOWN" ] && DOWN=$(date +%s) && echo "back to 1: $(date -Is)"; [ -n "$DOWN" ] && [ $(( $(date +%s) - DOWN )) -ge 120 ] && break; sleep 10; done
echo "wait end: $(date -Is) spec=$(spec) up_seen=${UP:-no}"
kill $SP $WP 2>/dev/null
curl -s -H "$H" $U/api/autoscale/decisions > $R/autoscale-decisions.json
kubectl logs -n ttalkkak deploy/ttalkkak-ops --since=90m | grep AUTOSCALE > $R/autoscale-ops-log.txt
} > $L 2>&1
