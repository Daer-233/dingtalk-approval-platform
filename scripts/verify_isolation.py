"""Local lifecycle acceptance check. Never starts or stops the source application."""
from pathlib import Path
import json
import socket
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.service_control import ROOT, STATE, metadata, start, stop, check_ports

def connected(port):
    with socket.socket() as s:
        s.settimeout(.3);return s.connect_ex(('127.0.0.1',port))==0

def run():
    results={}
    sentinels=[]
    try:
        # Original services are never launched here. Free original ports get independent socket sentinels.
        for port in [8011,5174]:
            if connected(port): continue
            listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            listener.bind(('127.0.0.1',port));listener.listen();sentinels.append((port,listener))
        stop()
        results['stop_did_not_touch_original_ports']=all(connected(p) for p in [8011,5174])
        assert results['stop_did_not_touch_original_ports']
        assert not connected(8012) and not connected(5175)
        # Use a foreign base-Python process, whose command line has no experiment directory.
        foreign=subprocess.Popen([sys.base_prefix+'/python.exe','-m','http.server','5175','--bind','127.0.0.1','--directory','G:/AI_PJ/outputs'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline=time.monotonic()+10
            while not connected(5175) and time.monotonic()<deadline:time.sleep(.1)
            assert connected(5175)
            try:start(no_browser=True)
            except RuntimeError as exc:assert '5175' in str(exc)
            else:raise AssertionError('occupied port must stop startup')
            results['occupied_port_rejected_before_api_start']=not connected(8012)
            assert results['occupied_port_rejected_before_api_start']
            record=metadata(foreign.pid);assert record
            STATE.write_text(json.dumps([record]),encoding='utf8')
            stop()
            results['foreign_pid_record_not_terminated']=foreign.poll() is None and connected(5175)
            assert results['foreign_pid_record_not_terminated']
        finally:
            # Popen owns this sentinel only; no enumeration or port-based termination.
            foreign.terminate();foreign.wait(timeout=5)
        start(no_browser=True)
        results['independent_start']=connected(8012) and connected(5175)
        stop();start(no_browser=True)
        results['independent_restart']=connected(8012) and connected(5175)
        results['original_ports_still_serving']=all(connected(p) for p in [8011,5174])
        assert all(results.values())
        results['original_services_started']=False
        results['original_ports_validation']='Existing listeners or independent socket sentinels; source application not launched.'
        (ROOT/'output/lifecycle-tests.json').write_text(json.dumps(results,indent=2),encoding='utf8')
        print(results)
    finally:
        for _,listener in sentinels:listener.close()

if __name__=='__main__':run()
