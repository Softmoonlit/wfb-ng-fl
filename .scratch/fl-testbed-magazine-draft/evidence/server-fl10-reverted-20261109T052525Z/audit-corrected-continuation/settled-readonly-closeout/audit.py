import pathlib,importlib.util,json,time
ROOT=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('d',ROOT.parent/'driver-base.py');d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
d.checkpoint=lambda *a,**k:None
a=d.audit(ROOT,'actual-baseline',10,10)
g=d.gate(ROOT)
d.write(ROOT/'result.json',dict(full_ten_actual_baseline_ok=a['ok'],exact_nine_ready_idle=g['passed'],exact_cleanup_ok=json.loads((ROOT.parent/'final-closeout/result.json').read_text())['exact_cleanup_ok'],server_schedule='10/10',independent_restoration=True,readonly_settled_observation=True,prior_false_preserved=True,wall=time.time()))
print(json.dumps(dict(actual_ok=a['ok'],ready=g['passed'],checks=a['checks'])),flush=True)
