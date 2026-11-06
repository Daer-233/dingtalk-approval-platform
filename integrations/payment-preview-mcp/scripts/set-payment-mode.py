"""Immediately enable or disable the independent payment capability."""
import argparse,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/payment-mcp'))
from payment_mcp.config import Settings,DEFAULT_CONFIG


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['off','on']);args=p.parse_args()
    values=json.loads(DEFAULT_CONFIG.read_text(encoding='utf-8'));values['enabled']=args.mode=='on';values['generation']=int(values.get('generation',0))+1
    probe=DEFAULT_CONFIG.with_suffix('.probe.private.json');probe.write_text(json.dumps(values),encoding='utf-8')
    try:Settings.load(probe)
    finally:probe.unlink(missing_ok=True)
    host={k:values[k] for k in ('enabled','pilot_staff_id','corp_id','bot_id','processor_ids','host_secret')}
    host['generation']=values.get('generation',0)
    host['service_url']='http://host.docker.internal:8070'
    directory=ROOT/'data/payments/runtime';directory.mkdir(parents=True,exist_ok=True)
    staged=directory/'host-mode.private.json';staged.write_text(json.dumps(host),encoding='utf-8')
    pending=DEFAULT_CONFIG.with_suffix('.pending.private.json')
    pending.write_text(json.dumps(values,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if args.mode=='off':os.replace(pending,DEFAULT_CONFIG)
    subprocess.run(['docker','cp',str(staged),'langbot:/app/data/work-payment-tools.json'],check=True,capture_output=True)
    if args.mode=='on':os.replace(pending,DEFAULT_CONFIG)
    print('Payment capability '+args.mode+'; no knowledge configuration changed.')


if __name__=='__main__':main()