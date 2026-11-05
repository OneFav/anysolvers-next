import type { ProjectRegistry, ProjectBinding } from './core/project-registry.js'
import type { Agent } from '@deepseek-ai/dsh-agent'

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type JsonObject = { [key: string]: Json }
export type Role = 'coordinator' | 'researcher'
export interface Scope { runId?: string; studyId?: string; sessionId?: string }
export interface ResearchBinding {
  runId: string; actor: string; sessionId: string; nativeId: string;
  workspace: string; generation: number;
  status: 'starting' | 'active' | 'closing' | 'completed' | 'failed' | 'reconciliation';
}
export interface Studies {
  readonly projects?: ProjectRegistry
  readonly baseRoot?: string
  forRoot?(root:string): Studies
  withAgent?<T>(agent:Agent, task:()=>Promise<T>):Promise<T>
  projectBinding?(agent:Agent):Promise<ProjectBinding|undefined>
  bindProject?(agent:Agent, binding:ProjectBinding):Promise<void>
  projectCatalog?(signal?:AbortSignal):Promise<JsonObject>
  readonly root: string
  readonly python: string
  readonly workspaceRoot?: string
  call(operation: string, payload?: JsonObject, scope?: Scope, signal?: AbortSignal): Promise<JsonObject>
  status(runId: string, signal?: AbortSignal): Promise<JsonObject>
}
export interface StudySessions {
  binding(agent: Agent): ResearchBinding | undefined
  start(parent: Agent, runId: string, actor: string, idea?: string, iteration?: string): Promise<JsonObject>
  end(parent: Agent, runId: string, actor: string, status: 'completed' | 'failed'): Promise<JsonObject>
  recover(parent: Agent, runId: string): Promise<JsonObject>
  startRun(parent: Agent, runId: string, assignments: { actor: string; idea: string }[]): Promise<JsonObject>
  waitRun(parent: Agent, runId: string): Promise<JsonObject>
}
export interface StudySandbox {
  forRoot?(root:string): StudySandbox
  prepare(runId: string, sessionId: string): Promise<string>
  read(binding: ResearchBinding, path: string): Promise<string>
  write(binding: ResearchBinding, path: string, content: string): Promise<void>
  list(binding: ResearchBinding, path?: string): Promise<string[]>
  release(runId: string, sessionId: string): Promise<void>
  doctor(): Promise<JsonObject>
}
export interface StudyPolicy {
  authorize(agent: Agent, operation: string, scope?: Scope): Promise<void>
  authorizeFinal(agent: Agent, runId: string, signal: AbortSignal): Promise<void>
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    studies: Studies
    studySessions: StudySessions
    studySandbox: StudySandbox
    studyPolicy: StudyPolicy
  }
}
