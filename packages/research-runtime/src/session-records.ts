import {join} from 'node:path'
import {atomic,read} from './storage.js'
export interface ExecutionRecord {attemptId:string;runId:string;parentNativeId:string;nativeId:string;sessionId:string|null;workspace:string|null;phase:string;createdAt:string;updatedAt:string;issue:string|null}
export interface ExecutionJournal {schemaVersion:2;runId:string;revision:number;methodRef:string;launches:ExecutionRecord[]}
export async function saveExecutionJournal(directory:string,value:ExecutionJournal){await atomic(join(directory,'controller.json'),value)}
export async function readExecutionJournal(directory:string){return read<ExecutionJournal>(join(directory,'controller.json'))}
