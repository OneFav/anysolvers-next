// Runs inside the native DSH host. Never returns credential values or account data.
import {writeFile} from 'node:fs/promises'
export const name='anysolvers-model-probe'
export const inject=['llm','credentials','deepseekAccount']
export function apply(ctx,config){
  const report={provider:config.provider,model:config.model,model_requests:0,available:false}
  void ctx.root.loader.await().then(async()=>{
    const models=await ctx.llm.listModels(config.provider)
    report.available=models.some(m=>m.id===config.model)
    report.offered_models=models.map(m=>({id:m.id,name:m.name}))
    if(config.inference && report.available){
      report.model_requests=1;let ended=false,text='',failure
      for await(const chunk of ctx.llm.stream({provider:config.provider,model:config.model,
        messages:[{role:'user',content:[{type:'text',text:'Connectivity check. Reply exactly READY.'}]}],
        tools:[],maxTokens:256,reasoningEffort:'off',signal:AbortSignal.timeout(45000)})){
        if(chunk.type==='text-delta')text+=chunk.text??chunk.delta??''
        if(chunk.type==='finish'){
          ended=true;report.finish_reason=chunk.reason?.kind??chunk.reason
          if(report.finish_reason!=='stop')failure=chunk.reason?.error?.code??chunk.error?.code??'MODEL_NOT_COMPLETED'
        }
        if(chunk.type==='error')failure=chunk.error?.code??'MODEL_ERROR'
      }
      report.inference_ok=ended&&!failure&&text.trim()==='READY';report.ended=ended;report.response_chars=text.length
      if(failure)report.error_code=failure
    }
  }).catch(error=>{report.error_code=typeof error?.code==='string'?error.code:'PROBE_FAILED'}).finally(async()=>{
    await writeFile(config.output,JSON.stringify(report,null,2),{mode:0o600})
    process.exit(report.available && (!config.inference || report.inference_ok)?0:1)
  })
}
