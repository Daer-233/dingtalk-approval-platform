// Launch only the existing local platform; no approval API is called.
const fs=require('node:fs'); const path=require('node:path'); const cp=require('node:child_process');
const root=path.resolve(__dirname,'..');
const platform = process.env.PAYMENT_PLATFORM_ROOT || 'I:/AI_Develop/py_AI/补贴付款拆分平台_局域网版';
const front=path.join(platform,'frontend'); const runtime=path.join(root,'data/payments/runtime');
const record=path.join(runtime,'platform-processes.json');
const parseEnv=require('node:util').parseEnv;
async function probe(port,route){
 try { const response=await fetch(`http://127.0.0.1:${port}${route}`,{signal:AbortSignal.timeout(3000)}); if(response.ok)return response; }
 catch{} return null;
}
function launch(name,file,args,cwd,env){
 const out=fs.openSync(path.join(runtime,name+'.out.log'),'a'); const err=fs.openSync(path.join(runtime,name+'.err.log'),'a');
 const child=cp.spawn(file,args,{cwd,env,detached:true,windowsHide:true,stdio:['ignore',out,err]});
 child.unref(); fs.closeSync(out);fs.closeSync(err);
 return {name,pid:child.pid,file,args,started_at:new Date().toISOString()};
}
async function main(){
 fs.mkdirSync(runtime,{recursive:true}); let rows=fs.existsSync(record)?JSON.parse(fs.readFileSync(record,'utf8')):[];
 const api=await probe(8000,'/openapi.json');
 if(api){const schema=await api.json();if(schema.info?.title!=='企业业务平台 API')throw Error('Port 8000 is not the expected platform');console.log('Reusing payment platform backend.');}
 else{
   const env=Object.assign({},process.env,parseEnv(fs.readFileSync(path.join(platform,'.env'),'utf8')),{PYTHONUNBUFFERED:'1',PYTHONDONTWRITEBYTECODE:'1'});
   rows=rows.filter(r=>r.name!=='platform-backend');
   rows.push(launch('platform-backend',path.join(platform,'.venv/Scripts/python.exe'),['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],platform,env));
   console.log('Existing payment backend starting on 127.0.0.1:8000.');
 }
 if(await probe(5173,'/'))console.log('Reusing platform webpage.');
 else{
   rows=rows.filter(r=>r.name!=='platform-web');
   rows.push(launch('platform-web',process.execPath,[path.join(front,'node_modules/vite/bin/vite.js'),'--host','0.0.0.0','--port','5173','--strictPort'],front,process.env));
   console.log('Existing payment webpage starting on port 5173.');
 }
 fs.writeFileSync(record,JSON.stringify(rows,null,2));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
