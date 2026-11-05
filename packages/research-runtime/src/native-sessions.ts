import type {Agent,AgentHandle,AgentOptions,CreateAgentOptions} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-subagent'
import {SessionId} from '@deepseek-ai/dsh-session'
import {createUserMessage} from '@deepseek-ai/dsh-llm'
export interface NativeDependencies {
 create(options:CreateAgentOptions):Promise<AgentHandle>;
 validateRoute(options:AgentOptions,signal:AbortSignal):Promise<void>;
}
export interface NativeRequest {
 nativeId:string;parent:Agent;preset:string;role:string;workspace:string;options:AgentOptions;signal:AbortSignal;
 setup:NonNullable<CreateAgentOptions['setup']>;
}
export class NativeCreationError extends Error {readonly cleanupConfirmed=true}
/** Lifecycle only. Method code supplies role, materials, activation and completion policy. */
export class NativeSessions {
 constructor(private readonly deps:NativeDependencies){}
 async validate(options:AgentOptions,signal:AbortSignal){await this.deps.validateRoute(options,signal);signal.throwIfAborted()}
 async create(request:NativeRequest):Promise<AgentHandle>{
  const {nativeId,parent,preset,workspace,options,signal,setup}=request
  const handle=await this.deps.create({sessionId:SessionId(nativeId),parentAgent:parent,meta:{cwd:workspace,parentSession:parent.id,origin:'subagent',delegationDepth:(parent.session.header.delegationDepth??0)+1,agentPreset:preset},agentOptions:options,signal,setup})
  try{
   if(handle.agent.id!==nativeId)throw new Error('Agent factory returned a different identity')
   if(handle.agent.options.provider!==options.provider||handle.agent.options.model!==options.model||handle.agent.options.reasoningEffort!==options.reasoningEffort)throw new Error('Created agent changed the explicit model settings')
   return handle
  }catch(error){await handle.dispose();throw new NativeCreationError(error instanceof Error?error.message:String(error))}
 }
 deliver(handle:AgentHandle,parent:Agent,prompt:string){handle.agent.followup(createUserMessage({content:[{type:'text',text:prompt}],source:{kind:'agent-message',form:'relay',senderSessionId:parent.id}}))}
 idle(handle:AgentHandle){return handle.agent.status==='idle'&&!handle.agent.inbox.nextTurn.length&&!handle.agent.inbox.nextStep.length}
 async close(handle:AgentHandle){await handle.dispose()}
}
