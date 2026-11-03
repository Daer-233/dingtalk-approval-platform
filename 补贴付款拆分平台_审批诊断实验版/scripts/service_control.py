"""Windows service lifecycle with PID reuse and project ownership checks; no WMI."""
from __future__ import annotations
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'output/experiment-processes.json'
K=c.WinDLL('kernel32',use_last_error=True)
N=c.WinDLL('ntdll')
K.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];K.OpenProcess.restype=w.HANDLE
K.CloseHandle.argtypes=[w.HANDLE]
K.GetProcessTimes.argtypes=[w.HANDLE,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p]
K.TerminateProcess.argtypes=[w.HANDLE,w.UINT]
N.NtQueryInformationProcess.argtypes=[w.HANDLE,w.ULONG,c.c_void_p,w.ULONG,c.POINTER(w.ULONG)]
class UnicodeString(c.Structure):
    _fields_=[('length',w.USHORT),('maximum',w.USHORT),('buffer',c.c_void_p)]

def metadata(pid, *, terminate=False):
    handle=K.OpenProcess(0x1000 | (1 if terminate else 0),False,int(pid))
    if not handle: return None
    try:
        created,exited,kernel,user=(c.c_ulonglong() for _ in range(4))
        if not K.GetProcessTimes(handle,c.byref(created),c.byref(exited),c.byref(kernel),c.byref(user)): return None
        size=w.ULONG();N.NtQueryInformationProcess(handle,60,None,0,c.byref(size))
        if not size.value: return None
        buffer=c.create_string_buffer(size.value)
        if N.NtQueryInformationProcess(handle,60,buffer,size,c.byref(size))!=0: return None
        data=UnicodeString.from_buffer(buffer)
        command=c.wstring_at(data.buffer,data.length//2)
        return {'pid':int(pid),'created':created.value,'command':command}
    finally:K.CloseHandle(handle)

class ProcessEntry(c.Structure):
    _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',c.c_size_t),
              ('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),('priority',w.LONG),
              ('flags',w.DWORD),('exe',w.WCHAR*260)]
K.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD];K.CreateToolhelp32Snapshot.restype=w.HANDLE
K.Process32FirstW.argtypes=[w.HANDLE,c.POINTER(ProcessEntry)]
K.Process32NextW.argtypes=[w.HANDLE,c.POINTER(ProcessEntry)]

def process_pairs():
    snapshot=K.CreateToolhelp32Snapshot(2,0)
    if snapshot==c.c_void_p(-1).value: raise OSError('无法读取进程列表，停止操作已取消。')
    try:
        entry=ProcessEntry();entry.size=c.sizeof(entry);ok=K.Process32FirstW(snapshot,c.byref(entry));pairs=[]
        while ok:
            pairs.append((entry.pid,entry.parent));ok=K.Process32NextW(snapshot,c.byref(entry))
        return pairs
    finally:K.CloseHandle(snapshot)

def owned_tree(records):
    own={}
    for record in records:
        current=metadata(record['pid'])
        if current and current==record and str(ROOT).casefold() in current['command'].casefold(): own[current['pid']]=current
    pairs=process_pairs()
    while True:
        added=False
        for pid,parent in pairs:
            if pid not in own and parent in own:
                child=metadata(pid)
                if child and child['created']>=own[parent]['created']:
                    own[pid]=child;added=True
        if not added:break
    return list(own.values())

def terminate_verified(record):
    handle=K.OpenProcess(0x1001,False,record['pid'])
    if not handle:return
    try:
        # Holding this process handle prevents a recycled PID from changing our termination target.
        current=metadata(record['pid'])
        if current==record and not K.TerminateProcess(handle,0): raise OSError(c.get_last_error(),'实验进程停止失败')
    finally:K.CloseHandle(handle)

def stop():
    if not STATE.exists():
        print('没有实验版进程记录，未操作其他服务。');return
    records=json.loads(STATE.read_text(encoding='utf-8-sig'))
    own=owned_tree(records)
    for record in sorted(own,key=lambda r:r['created'],reverse=True):terminate_verified(record)
    deadline=time.monotonic()+5
    while any(metadata(r['pid'])==r for r in own) and time.monotonic()<deadline:time.sleep(.1)
    if any(metadata(r['pid'])==r for r in own): raise RuntimeError('实验版进程仍在退出，请稍后再试。')
    STATE.unlink(missing_ok=True)
    print(f'已停止 {len(own)} 个经路径、PID、创建时间及父子关系核实的实验版进程。')

def check_ports():
    for port in (8012,5175):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            try:listener.bind(('0.0.0.0',port))
            except OSError as exc:raise RuntimeError(f'端口 {port} 已被占用，已停止启动；未关闭占用服务。') from exc

def start(no_browser=False):
    check_ports()
    if STATE.exists() and owned_tree(json.loads(STATE.read_text(encoding='utf-8-sig'))):
        raise RuntimeError('实验版已有进程，请使用实验版重启入口。')
    node=shutil.which('node.exe') or ('H:/node/node.exe' if Path('H:/node/node.exe').exists() else None)
    python=ROOT/'.venv/Scripts/python.exe';vite=ROOT/'frontend/node_modules/vite/bin/vite.js'
    if not node or not python.exists() or not vite.exists():raise RuntimeError('缺少独立依赖，请查看实验版使用说明。')
    logs=ROOT/'output/logs';logs.mkdir(parents=True,exist_ok=True)
    records=[];STATE.parent.mkdir(parents=True,exist_ok=True)
    commands=[([str(python),'-m','uvicorn','app.main:app','--app-dir',str(ROOT),'--host','127.0.0.1','--port','8012'],ROOT,'api'),
              ([node,str(vite),'--host','0.0.0.0','--port','5175','--strictPort'],ROOT/'frontend','frontend')]
    processes=[]
    try:
        for command,cwd,role in commands:
            with (logs/f'{role}.log').open('wb') as output,(logs/f'{role}.error.log').open('wb') as error:
                process=subprocess.Popen(command,cwd=cwd,stdout=output,stderr=error,creationflags=subprocess.CREATE_NO_WINDOW,
                                         env={**os.environ,'APP_DATA_DIR':str(ROOT/'data'),'PYTHONUNBUFFERED':'1'})
            processes.append(process)
            record=metadata(process.pid)
            if not record or str(ROOT).casefold() not in record['command'].casefold():
                # This is our own freshly returned Popen object; safe cleanup if metadata failed.
                process.terminate();raise RuntimeError('无法核验实验版启动进程，已取消启动。')
            records.append(record);STATE.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if any(p.poll() is not None for p in processes):raise RuntimeError('实验版进程启动失败，请查看 output/logs。')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8012/api/health',timeout=1) as r:health=json.load(r)
                with urllib.request.urlopen('http://127.0.0.1:5175',timeout=1) as r:html=r.read().decode('utf8')
                if health.get('edition')=='审批诊断实验版' and '审批诊断实验版' in html:break
            except (OSError,ValueError):pass
            time.sleep(.3)
        else:raise RuntimeError('实验版未在30秒内就绪，请查看 output/logs。')
    except Exception:
        if records:stop()
        raise
    print('审批诊断实验版已启动：http://localhost:5175 （后端 8012，钉钉只读）')
    if not no_browser:os.startfile('http://localhost:5175')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','restart']);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    try:
        if args.action in ('stop','restart'):stop()
        if args.action in ('start','restart'):start(args.no_browser)
    except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
