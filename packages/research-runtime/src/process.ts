import {spawn} from 'node:child_process'
export class ProcessError extends Error {constructor(readonly code:string,message:string){super(message);this.name='BridgeError'}}
export interface ProcessRequest {executable:string;args:string[];cwd:string;env:Record<string,string>;input:string;maxInputBytes:number;maxOutputBytes:number;signal?:AbortSignal;timeoutMs?:number}
/** Only installed adapters construct argv; no command registration is exposed to model tools. */
export class BoundedProcess {
 private children=new Set<ReturnType<typeof spawn>>();private closed=false
 async close(){this.closed=true;await Promise.all([...this.children].map(child=>new Promise<void>(done=>{child.once('close',()=>done());child.kill('SIGTERM')})))}
 async execute(request:ProcessRequest):Promise<{code:number|null;stdout:string}>{
  if(this.closed)throw new ProcessError('CLOSED','The execution bridge is stopping')
  request.signal?.throwIfAborted();if(Buffer.byteLength(request.input)>request.maxInputBytes)throw new ProcessError('INPUT_LIMIT','Request is too large')
  return new Promise((done,reject)=>{
   const child=spawn(request.executable,request.args,{cwd:request.cwd,env:request.env,shell:false,stdio:['pipe','pipe','pipe']});this.children.add(child)
   const chunks:Buffer[]=[];let bytes=0,failure:Error|undefined,killTimer:ReturnType<typeof setTimeout>|undefined
   const stop=(error:Error)=>{failure=error;child.kill('SIGTERM');killTimer??=setTimeout(()=>child.kill('SIGKILL'),5000)}
   const abort=()=>stop(new ProcessError('INTERRUPTED','Call interrupted; inspect its original request before continuing'))
   request.signal?.addEventListener('abort',abort,{once:true});if(request.signal?.aborted)abort()
   const timer=request.timeoutMs?setTimeout(()=>stop(new ProcessError('TIMEOUT','Execution bridge timed out; reconcile its original request')),request.timeoutMs):undefined
   child.stdout.on('data',(chunk:Buffer)=>{bytes+=chunk.length;if(bytes>request.maxOutputBytes)stop(new ProcessError('OUTPUT_LIMIT','Response exceeded its configured limit; inspect the original request'));else chunks.push(chunk)})
   child.stderr.on('data',()=>{});child.stdin.on('error',()=>{})
   child.once('error',()=>{failure=new ProcessError('START_FAILED','Could not start the registered execution adapter')})
   child.once('close',code=>{this.children.delete(child);request.signal?.removeEventListener('abort',abort);clearTimeout(timer);clearTimeout(killTimer);if(failure)reject(failure);else done({code,stdout:Buffer.concat(chunks).toString('utf8')})})
   child.stdin.end(request.input)
  })
 }
}
