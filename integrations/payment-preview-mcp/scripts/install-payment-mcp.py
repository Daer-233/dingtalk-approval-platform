"""Install only payment hooks; preserve other local Host modifications."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PACKAGE='/app/src/langbot/pkg/platform/adapters/dingtalk/knowledge_card'
MARK='# work-payment-preview-mcp-v1 bootstrap'
HOOKS={
 '/app/src/langbot/pkg/agent/runner/orchestrator.py': '''
from langbot.pkg.platform.adapters.dingtalk.knowledge_card.payment_host import install_builder as _payment_builder, install_registry as _payment_registry, install_orchestrator as _payment_orchestrator
_payment_builder(AgentResourceBuilder)
_payment_registry(AgentRunSessionRegistry)
_payment_orchestrator(AgentRunOrchestrator)
''',
 '/app/src/langbot/pkg/plugin/handler.py': '''
from langbot.pkg.platform.adapters.dingtalk.knowledge_card.payment_host import install_handler as _payment_handler
_payment_handler(globals())
''',
}


def run(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,encoding='utf-8',timeout=120).stdout.strip()


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def card_patch(name,source):
    if name=='model.py' and "'payment_reading'" not in source:
        index=source.index('\ndef packets')
        source=source[:index]+"\nLABELS.update({'payment_reading':'正在读取表格','payment_preparing':'正在生成预览','payment_review':'需要补充信息','payment_ready':'待平台确认'})\n\n"+source[index:]
        source=source.replace("    stage: str = 'processing'","    business: bool = False\n    stage: str = 'processing'",1)
        source=source.replace("        if kind == 'retrieval.started':","        if kind == 'business.updated': self.business = True\n        elif kind == 'payment.stage': self.stage = event['stage']\n        elif kind == 'retrieval.started':",1)
        source=source.replace("('busy','empty','failed')","('busy','empty','failed','payment_review','payment_ready')")
    if name=='adapter.py' and "info.get('business')" not in source:
        source=source.replace("        stage = info.get('stage')","        stage = info.get('stage')\n        if info.get('business'): await delivery.push({'type':'business.updated'})\n        if stage in ('payment_reading','payment_preparing','payment_review','payment_ready'):\n            await delivery.push({'type':'payment.stage','stage':stage})",1)
        source=source.replace('if not delivery.state.sources:','if not delivery.state.sources and not delivery.state.business:')
    if name=='streaming.py' and "'payment_review'" not in source:
        source=source.replace("('busy','empty')","('busy','empty','payment_review','payment_ready')")
        source=source.replace("('failed','busy','empty')","('failed','busy','empty','payment_review','payment_ready')")
    compile(source,name,'exec')
    return source


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy',action='store_true')
    parser.add_argument('--rollback',action='store_true')
    args=parser.parse_args()
    record=ROOT/'data/payments/runtime/host-install.json'
    if args.rollback:
        saved=json.loads(record.read_text(encoding='utf-8'))
        for entry in saved['files']:
            current=record.parent/'rollback-inspect.py'
            run('docker','cp','langbot:'+entry['target'],str(current))
            if digest(current)!=entry['after_sha']:raise RuntimeError('Host changed after installation; review backup before rollback')
        for entry in saved['files']:run('docker','cp',entry['backup'],'langbot:'+entry['target'])
        print('Restored Host sources. Disable payment mode and restart only LangBot.');return
    folder=ROOT/'backups/payment-host'/datetime.now().strftime('%Y%m%d-%H%M%S')
    folder.mkdir(parents=True,exist_ok=False)
    files=[]
    for target,hook in HOOKS.items():
        backup=folder/Path(target).name
        run('docker','cp','langbot:'+target,str(backup))
        source=backup.read_text(encoding='utf-8')
        if MARK not in source:source+='\n'+MARK+'\n'+hook
        compile(source,target,'exec')
        patched=backup.with_suffix('.patched.py');patched.write_text(source,encoding='utf-8')
        files.append({'target':target,'backup':str(backup),'patched':str(patched),'after_sha':digest(patched)})
    for name in ('model.py','adapter.py','streaming.py'):
        target=PACKAGE+'/'+name; backup=folder/('card-'+name)
        run('docker','cp','langbot:'+target,str(backup))
        patched=backup.with_suffix('.patched.py');patched.write_text(card_patch(name,backup.read_text(encoding='utf-8')),encoding='utf-8')
        files.append({'target':target,'backup':str(backup),'patched':str(patched),'after_sha':digest(patched)})
    cfg=json.loads((ROOT/'instances/payments/config.private.json').read_text(encoding='utf-8'))
    host={k:cfg[k] for k in ('enabled','pilot_staff_id','corp_id','bot_id','processor_ids','host_secret')}
    host['generation']=cfg.get('generation',0)
    host['service_url']='http://host.docker.internal:8070'
    host_config=folder/'host-config.private.json';host_config.write_text(json.dumps(host,ensure_ascii=False,indent=2),encoding='utf-8')
    if not args.deploy:
        print('Prepared payment Host patch; deployment not requested.');return
    helper=ROOT/'scripts/knowledge_card/payment_host.py'
    compile(helper.read_text(encoding='utf-8'),str(helper),'exec')
    run('docker','cp',str(helper),'langbot:'+PACKAGE+'/payment_host.py')
    run('docker','cp',str(ROOT/'data/payments/runtime/catalog.json'),'langbot:/app/data/work-payment-catalog.json')
    run('docker','cp',str(host_config),'langbot:/app/data/work-payment-tools.json')
    for entry in files:run('docker','cp',entry['patched'],'langbot:'+entry['target'])
    record.parent.mkdir(parents=True,exist_ok=True)
    record.write_text(json.dumps({'files':files,'installed_at':datetime.now().isoformat()},ensure_ascii=False,indent=2),encoding='utf-8')
    run('docker','exec','langbot','/app/.venv/bin/python','-m','py_compile',*(entry['target'] for entry in files),PACKAGE+'/payment_host.py')
    print('Payment hooks deployed and compiled. Restart LangBot after tests. Configured mode preserved.')


if __name__=='__main__':main()