/** Separate model identity from provider and effort; preserve the intended model. */
export function normalizeResearchModel(provider:string,model:string,effort:string):string {
 if(model.startsWith(provider+'/')){
  const parts=model.slice(provider.length+1).split('/')
  if(parts.length!==2||!parts[0]||parts[1]!==effort)throw new Error('Ambiguous model route; provider/model/effort must agree')
  return parts[0]!
 }
 if(provider==='deepseek-account'&&model.includes('/'))throw new Error('DeepSeek model must be an API model ID, with provider and effort separate')
 return model
}

export function classifyModelFailure(failure:unknown){
 const f=(failure&&typeof failure==='object'?failure:{}) as {status?:unknown;code?:unknown;message?:unknown}
 const status=typeof f.status==='number'?f.status:undefined
 const code=typeof f.code==='string'?f.code:''
 // IDs can contain 402/429; never search serialized failures for HTTP status digits.
 if(status===400||status===404||status===422||code==='INVALID_REQUEST')return {kind:'model_configuration',status,code,retryable:false,message:'模型请求配置无效（模型名称或参数错误）；已保留原会话，需修正配置后续接，不会继续重复提交。'}
 if(status===401||status===403)return {kind:'model_auth',status,code,retryable:false,message:'模型认证或权限校验失败；原会话与成果保留，需恢复账号权限。'}
 if(status===402||/^(INSUFFICIENT_BALANCE|INSUFFICIENT_QUOTA|QUOTA_EXCEEDED)$/.test(code))return {kind:'provider_quota',status,code,retryable:true,message:'模型服务明确返回额度不足；原会话与成果保留，恢复后重试同一步。'}
 if(status===429)return {kind:'provider_rate_limit',status,code,retryable:true,message:'模型服务限流（429）；正在等待后重试，不能据此判断余额不足。'}
 if(/NETWORK|TIMEOUT|CONNECTION|ECONN|FETCH/.test(code.toUpperCase()))return {kind:'network',status,code,retryable:true,message:'模型连接暂不可用；原会话与成果保留，恢复后重试同一步。'}
 return {kind:'model_unavailable',status,code,retryable:true,message:'模型请求暂不可用；原会话与成果保留，恢复后重试同一步。'}
}
