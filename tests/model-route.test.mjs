import test from 'node:test'
import assert from 'node:assert/strict'
import {normalizeResearchModel,classifyModelFailure} from '../lib/core/model-route.js'
import {launchOptions} from '../lib/core/session-controller.js'
test('original malformed route is normalized without editing the sealed input or choosing a different model',()=>{
 const original={spawn_options:{model:'deepseek-account/deepseek-flash/high',reasoning_effort:'high',fork_turns:'none'}}
 assert.deepEqual(launchOptions(original,'deepseek-account'),{provider:'deepseek-account',model:'deepseek-flash',reasoningEffort:'high'})
 assert.equal(original.spawn_options.model,'deepseek-account/deepseek-flash/high')
 assert.equal(normalizeResearchModel('deepseek-account','deepseek-flash','high'),'deepseek-flash')
 assert.throws(()=>normalizeResearchModel('deepseek-account','deepseek-account/deepseek-flash/low','high'),/Ambiguous/)
 assert.throws(()=>normalizeResearchModel('deepseek-account','other/deepseek-flash/high','high'),/API model ID/)
 assert.equal(normalizeResearchModel('other','org/model','high'),'org/model')
})
test('invalid model is not quota exhaustion even when request IDs contain 402 or 429',()=>{
 for(const id of ['request-402','request-429']){
  const result=classifyModelFailure({status:400,code:'INVALID_REQUEST',message:'unsupported model '+id})
  assert.equal(result.kind,'model_configuration');assert.equal(result.retryable,false)
 }
 assert.equal(classifyModelFailure({status:429,code:'RATE_LIMIT'}).kind,'provider_rate_limit')
 assert.equal(classifyModelFailure({status:402,code:'PAYMENT_REQUIRED'}).kind,'provider_quota')
 assert.equal(classifyModelFailure({status:500,message:'request-429'}).kind,'model_unavailable')
 assert.equal(classifyModelFailure({code:'NETWORK_ERROR'}).kind,'network')
})
