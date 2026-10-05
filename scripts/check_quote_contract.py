#!/usr/bin/env python3
"""Read-only V1 quote and Solana RPC contract probe in a temporary cache.

Outputs schema/status metadata, never credentials, public keys of users,
transaction payloads or signed messages. No building/submission endpoint.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
import requests


def main():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    with tempfile.TemporaryDirectory(prefix='neo-contract-') as directory:
        os.environ['NEO_JUPITER_LOCK_PATH']=str(Path(directory)/'quote.lock')
        os.environ['NEO_JUPITER_STAMP_PATH']=str(Path(directory)/'quote-stamp.txt')
        import honest_quote_transport as transport
        mint='So11111111111111111111111111111111111111112'
        usdc='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
        quote=transport.quote(usdc,mint,1_000_000,purpose='contract_probe')
        result={'quote_adapter':transport.ADAPTER_VERSION,
                'quote':{'status':'PASS','context_slot':quote.get('contextSlot'),
                         'http_ms':quote.get('_http_ms'),'schema_checked':True} if quote else
                        {'status':'UNAVAILABLE','diagnostic':transport.last_error()}}
        rpc=os.getenv('NEO_STOP_RPC_URL','https://api.mainnet-beta.solana.com')
        try:
            response=requests.post(rpc,json=[{'jsonrpc':'2.0','id':1,'method':'getVersion','params':[]},
                                            {'jsonrpc':'2.0','id':2,'method':'getSlot','params':[{'commitment':'confirmed'}]}],timeout=(3,6))
            response.raise_for_status()
            data=response.json()
            by_id={answer.get('id'):answer for answer in data} if isinstance(data,list) else {}
            result['solana_rpc']={'status':'PASS' if isinstance(by_id.get(2,{}).get('result'),int) else 'UNAVAILABLE',
                                  'version':(by_id.get(1,{}).get('result') or {}).get('solana-core'),
                                  'confirmed_slot':by_id.get(2,{}).get('result'),
                                  'http_status':response.status_code}
        except (requests.RequestException,ValueError,TypeError):
            result['solana_rpc']={'status':'UNAVAILABLE','error':'RPC_TRANSPORT_ERROR'}
        print(json.dumps(result,indent=2))
        return 0 if result['quote']['status']=='PASS' and result['solana_rpc']['status']=='PASS' else 2


if __name__=='__main__':
    raise SystemExit(main())
