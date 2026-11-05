import type { Agent } from '@deepseek-ai/dsh-agent'
export type Json = null | boolean | number | string | Json[] | { [key:string]:Json }
export type JsonObject = { [key:string]:Json }
export interface ImplementationIdentity { name:string; version:string; digest:string }
export interface RunIdentity {
  schemaVersion:1; projectId:string; root:string; runId:string; implementationId:string;
  createdAt:string; requestId:string; state:'starting'|'running'|'completed'|'reconciliation';
  methodRef:string|null; issue:string|null;
}
export interface Preflight { ready:boolean; revision:string; target:string; message:string; payload:JsonObject }
export interface Invocation { agent:Agent; root:string; target?:string; signal:AbortSignal }
export interface StartInvocation extends Invocation { requestId:string; checked:Preflight; allocate:(target:string)=>Promise<string> }
export interface StartResult { runId:string; message:string; prompt?:string; methodRef?:string }
/** The outer protocol contains no research-specific schema or completion rules. */
export interface ResearchImplementation {
  id:string; version:string; apiVersion:1; mainPreset:string; packageRoot:string;
  ui:{module:string; panel:string; route:string};
  preflight(input:Invocation):Promise<Preflight>;
  validateEnvironment?(input:Invocation):Promise<void>;
  start(input:StartInvocation):Promise<StartResult>;
  status(root:string, runId:string, signal?:AbortSignal):Promise<{complete:boolean; progress:JsonObject}>;
}
