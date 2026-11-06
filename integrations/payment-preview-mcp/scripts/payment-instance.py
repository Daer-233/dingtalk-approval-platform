"""Provision or serve the separate payment-preview MCP instance."""
import argparse
import asyncio
import json
from pathlib import Path
import secrets
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/payment-mcp'))


def initialize():
    from payment_mcp.config import DEFAULT_CONFIG,Settings
    path=DEFAULT_CONFIG
    if not path.exists():
        example=json.loads((ROOT/'instances/payments/config.example.json').read_text(encoding='utf-8'))
        example['host_secret']=secrets.token_urlsafe(48)
        path.write_text(json.dumps(example,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    Settings.load()
    from payment_mcp.app import create_app
    app=create_app()
    tools=asyncio.run(app.state.mcp.list_tools())
    target=ROOT/'data/payments/runtime'
    target.mkdir(parents=True,exist_ok=True)
    (target/'catalog.json').write_text(json.dumps({'tools':[x.model_dump(mode='json',exclude_none=True) for x in tools]},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Payment configuration and tool catalog ready; existing enabled flag retained.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['init','serve'])
    args=parser.parse_args()
    if args.command=='init':initialize()
    else:
        import logging
        logging.getLogger('httpx').setLevel(logging.WARNING)
        import uvicorn
        uvicorn.run('payment_mcp.app:create_app',factory=True,host='127.0.0.1',port=8070,
                    workers=1,access_log=False)


if __name__=='__main__':main()
