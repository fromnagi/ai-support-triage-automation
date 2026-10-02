"""Run actual localhost n8n E2E in an isolated data directory, using fake services."""
import argparse
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'demo')]
from demo.fake_business_system import Store, handler
from demo.fake_business_system import fake_llm
from local_e2e import request, ticket, run as basic
from extended_e2e import run as extended
from concurrent_e2e import run as concurrent


def fault_handler(store):
    class FaultHandler(handler(store)):
        delayed_started = threading.Event()
        delayed_release = threading.Event()
        delayed_finished = threading.Event()
        delayed_result = None

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            data = json.loads(raw)
            self.rfile = io.BytesIO(raw)
            suffix = data.get('ticket_id', '').rsplit('-', 1)[-1]
            if self.path == '/state/tickets' and suffix in {'REG400', 'REG503'}:
                return self.send(400 if suffix == 'REG400' else 503, {'error':'SIMULATED_STATE_FAILURE'})
            if self.path == '/state/tickets' and suffix in {'REGEMPTY','REG204'}:
                return self.send(204 if suffix == 'REG204' else 200, None)
            if self.path == '/state/triage' and suffix in {'TRI400','TRI503'}:
                return self.send(400 if suffix == 'TRI400' else 503, {'error':'SIMULATED_STATE_FAILURE'})
            if suffix == 'UNRESOLVED' and self.path in {'/crm/cases','/state/write-failed'}:
                return self.send(503, {'error':'SIMULATED_OUTAGE'})
            self.current_suffix = suffix
            if self.path == '/fake-llm/classify':
                value = json.loads(data['messages'][-1]['content'])
                if value['ticket_id'] == 'LIVE-ATTEMPT':
                    status, result = fake_llm(data)
                    content = json.loads(result['message']['content'])
                    if store.get('LIVE-ATTEMPT')['attempt_id'] == 1:
                        content.update(category='technical', confidence=.9, draft_reply='NEW DRAFT')
                    else:
                        content.update(draft_reply='OLD DRAFT')
                    return self.send(status, {'message':{'content':json.dumps(content)}})
            if self.path == '/crm/cases' and suffix == 'ATTEMPT' and data.get('attempt_id') == 0:
                type(self).delayed_started.set()
                if not type(self).delayed_release.wait(30):
                    raise RuntimeError('Regression did not release delayed request')
                try:
                    result = store.write('crm',data)
                    type(self).delayed_result = {'http':200,'response':result}
                    return self.send(200,result)
                except ValueError as error:
                    type(self).delayed_result = {'http':400,'error':str(error)}
                    return self.send(400,{'error':str(error)})
                finally:
                    type(self).delayed_finished.set()
            return super().do_POST()

        def send(self, status, value):
            suffix = getattr(self, 'current_suffix', '')
            if self.path == '/state/tickets' and suffix == 'REGSHAPE':
                value = dict(value, extra='invalid contract')
            if self.path == '/state/triage' and suffix == 'TRISHAPE':
                value = dict(value, status='WRONG')
            if self.path == '/crm/cases' and suffix == 'WRITELOST':
                return super().send(503, {'error':'RESPONSE_LOST_AFTER_COMMIT'})
            if self.path == '/crm/cases' and suffix == 'CRMSHAPE':
                value = dict(value, attempt_id=999)
            return super().send(status, value)
    return FaultHandler


def regressions(webhook, demo, store, faults):
    results = {}
    for suffix in ['REG400','REG503','REGEMPTY','REG204','REGSHAPE','TRI400','TRI503','TRISHAPE']:
        value = ticket('REGRESSION-' + suffix, 'Order status')
        http, result = request('POST', webhook, value)
        assert http == 503 and result['status'] == 'PROCESSING_UNRESOLVED', (suffix,http,result)
        assert result['needs_attention'] is True
        assert not any(c['ticket_id'] == value['ticket_id'] for c in store.list_records('crm'))
        assert not any(c['ticket_id'] == value['ticket_id'] for c in store.list_records('review'))
        results[suffix] = {'http':http,'response':result,'state':store.get(value['ticket_id'])}

    for suffix in ['WRITELOST','CRMSHAPE']:
        value = ticket('REGRESSION-' + suffix, 'Order status')
        http, result = request('POST', webhook, value)
        assert http == 200 and result['status'] == 'AUTO_ROUTED' and result['reason'] == 'WRITE_CONFIRMED_BY_STATE', (http,result)
        results[suffix] = {'http':http,'response':result,'state':store.get(value['ticket_id'])}

    value = ticket('REGRESSION-UNRESOLVED', 'Order status')
    http, result = request('POST', webhook, value)
    assert http == 503 and result['status'] == 'WRITE_UNRESOLVED', (http,result)
    http2, duplicate = request('POST', webhook, value)
    assert http2 == 202 and duplicate['status'] == 'AI_CLASSIFIED' and duplicate['needs_attention'] is True
    results['UNRESOLVED'] = {'http':http,'response':result,'resend_http':http2,'resend':duplicate}

    # Seed the exact durable crash boundaries; no unrelated running process is killed.
    for state in ['RECEIVED','AI_CLASSIFIED','AI_FAILED']:
        value = ticket('REGRESSION-' + state, 'Order status')
        store.register(value)
        if state != 'RECEIVED':
            store.triage(dict(ticket_id=value['ticket_id'],attempt_id=0,route='REVIEW',
                              reason='AI_TIMEOUT' if state == 'AI_FAILED' else 'CATEGORY_REVIEW'))
        http, result = request('POST', webhook, value)
        assert http == 202 and result['disposition'] == 'duplicate' and result['status'] == state
        assert result['record_id'] is None and result['needs_attention'] is True
        results[state] = {'http':http,'response':result}

    for index, (field, text) in enumerate([('body','Where?\u001c'),('body','\ufeffWhere?\ufeff'),
                                         ('customer_email',' A\u0130@Example.Test '),('body','\U0001f600'*2500)]):
        value = dict(ticket('REGRESSION-PARITY'+str(index),'Order status'), **{field:text})
        http, result = request('POST',webhook,value)
        assert http == 200 and result['status'] == 'AUTO_ROUTED', (http,result)
        http, dup = request('POST',webhook,value)
        assert http == 200 and dup['status'] == 'AUTO_ROUTED' and dup['needs_attention'] is False
        results['PARITY'+str(index)] = {'response':result,'resend':dup}

    value=ticket('LIVE-ATTEMPT','Order status')
    http,old=request('POST',webhook,value)
    assert http == 502 and old['status']=='WRITE_FAILED' and old['attempt_id']==0, (http,old)
    assert faults.delayed_started.is_set()
    http,new=request('POST',webhook,value)
    assert http==200 and new['status']=='AUTO_ROUTED' and new['attempt_id']==1 and new['category']=='technical', (http,new)
    faults.delayed_release.set()
    assert faults.delayed_finished.wait(5)
    assert faults.delayed_result=={'http':400,'error':'INVALID_STATE'}
    record=next(c for c in store.list_records('crm') if c['ticket_id']=='LIVE-ATTEMPT')
    assert record['draft_reply']=='NEW DRAFT' and record['attempt_id']==1
    assert store.get('LIVE-ATTEMPT')['category']=='technical'
    results['DELAYED_ATTEMPT']={'old_response':old,'new_response':new,'delayed_write':faults.delayed_result,
                                'ticket':store.get('LIVE-ATTEMPT'),'record':record}
    for suffix, expected in [('NETREG','PROCESSING_UNRESOLVED'),('NETTRI','PROCESSING_UNRESOLVED'),
                             ('NETWRITE','WRITE_FAILED'),('NETCONFIRM','WRITE_UNRESOLVED')]:
        http,result=request('POST',webhook,ticket('REGRESSION-'+suffix,'Order status'))
        assert http==(502 if expected=='WRITE_FAILED' else 503) and result['status']==expected, (suffix,http,result)
        results[suffix]={'http':http,'response':result,'state':store.get('REGRESSION-'+suffix)}
    http,result=request('POST',webhook,ticket('REGRESSION-NETLLM','Order status'))
    assert http==200 and result['reason']=='AI_SERVICE_ERROR' and result['status']=='HUMAN_REVIEW'
    results['NETLLM']={'http':http,'response':result}
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True, help='New ignored local directory for logs and databases')
    parser.add_argument('--port', type=int, default=26678)
    parser.add_argument('--demo-port', type=int, default=29765)
    parser.add_argument('--keep-running', action='store_true', help='Keep own services available for UI inspection until Ctrl+C')
    args = parser.parse_args()
    directory = (ROOT / args.directory).resolve()
    local = (ROOT / '.local').resolve()
    if local not in directory.parents or directory.exists():
        parser.error('Use a new directory inside this repository .local directory')
    for port in [args.port,args.port+1,args.demo_port]:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',port))
    directory.mkdir(parents=True)
    store = Store(directory/'business.db')
    faults=fault_handler(store)
    server = ThreadingHTTPServer(('127.0.0.1',args.demo_port),faults)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    demo = f'http://127.0.0.1:{args.demo_port}'
    base = f'http://127.0.0.1:{args.port}'
    webhook = base + '/webhook/support-triage'
    subprocess.run([sys.executable,str(ROOT/'scripts/build_workflow.py'),'--demo-base-url',demo,
                    '--llm-url',demo+'/fake-llm/classify','--llm-timeout-ms','500',
                    '--output',str(directory/'workflow.json')],cwd=ROOT,check=True)
    # Force transport refusal only in this test export; shipped destinations stay fixed.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        closed_port=probe.getsockname()[1]
    workflow=json.loads((directory/'workflow.json').read_text(encoding='utf-8'))
    cases={'Register Ticket':['NETREG'],'Record Triage':['NETTRI'],
           'Write Business Record':['NETWRITE','NETCONFIRM'],'Record Write Failure':['NETCONFIRM'],
           'Call LLM':['NETLLM']}
    for node in workflow['nodes']:
        if node['name'] in cases:
            url=node['parameters']['url']
            check=' || '.join('($json.ticket_id ?? $json.ticket?.ticket_id) === '+json.dumps('REGRESSION-'+suffix) for suffix in cases[node['name']])
            node['parameters']['url']='={{ ('+check+') ? "http://127.0.0.1:'+str(closed_port)+'" : ('+url[3:-3].strip()+') }}'
    (directory/'workflow.json').write_text(json.dumps(workflow,indent=2),encoding='utf-8')
    env = dict(os.environ,N8N_USER_FOLDER=str(directory/'n8n'),N8N_LISTEN_ADDRESS='127.0.0.1',
               N8N_PORT=str(args.port),N8N_RUNNERS_BROKER_PORT=str(args.port+1),
               N8N_DIAGNOSTICS_ENABLED='false',N8N_VERSION_NOTIFICATIONS_ENABLED='false',
               N8N_TEMPLATES_ENABLED='false',N8N_PUBLIC_API_DISABLED='true')
    cli = ['node',str(ROOT/'node_modules/n8n/bin/n8n')]
    process = None
    with (directory/'n8n.log').open('w',encoding='utf-8') as log:
        try:
            for command in [['import:workflow','--input='+str(directory/'workflow.json')],
                            ['publish:workflow','--id=SupportTriageDemo']]:
                subprocess.run(cli+command,env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
            process = subprocess.Popen(cli+['start'],env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            for _ in range(180):
                if process.poll() is not None: raise RuntimeError('n8n exited; see n8n.log')
                try:
                    http,result = request('POST',webhook,{})
                    if http == 400 and result.get('error') == 'INVALID_INPUT': break
                except (URLError,TimeoutError): pass
                time.sleep(1)
            else: raise RuntimeError('Published webhook not ready')
            basic(webhook,demo,'LIVE-BASIC')
            extended(webhook,demo,'LIVE-EXT')
            concurrent(webhook,demo,'LIVE-CONCURRENT')
            results=regressions(webhook,demo,store,faults)
            http,result=request('POST',webhook,ticket('LIVE-TIMEOUT','[FAKE:timeout] Order status'))
            assert http == 200 and result['reason'] == 'AI_TIMEOUT' and result['status'] == 'HUMAN_REVIEW'
            results['TIMEOUT']={'http':http,'response':result}
            # Retry in extended E2E must propagate the registration's attempt through all real nodes.
            audit=store.get('LIVE-EXT-CRMFAIL')
            record=next(c for c in store.list_records('crm') if c['ticket_id']=='LIVE-EXT-CRMFAIL')
            assert audit['attempt_id']==1 and record['attempt_id']==1
            for action in [lambda:store.write('crm',dict(ticket_id=audit['ticket_id'],attempt_id=0,draft_reply='OLD')),
                           lambda:store.write_failed(audit['ticket_id'],0)]:
                try: action()
                except ValueError as error: assert str(error)=='INVALID_STATE'
                else: raise AssertionError('Old attempt accepted')
            assert store.get(audit['ticket_id'])==audit
            results['ATTEMPT']={'ticket':audit,'record':record,'stale_write':'INVALID_STATE','stale_confirmation':'INVALID_STATE'}
            (directory/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
            print('LIVE VALIDATION PASSED',flush=True)
            if args.keep_running:
                print('Own local services available at '+base,flush=True)
                while not (directory/'stop').exists(): time.sleep(1)
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=30)
            server.shutdown()


if __name__ == '__main__':
    main()
