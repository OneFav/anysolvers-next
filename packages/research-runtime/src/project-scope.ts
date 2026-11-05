import { AsyncLocalStorage } from 'node:async_hooks'
/** Scope follows each async chain, never a mutable process-wide current project. */
export const projectScope = new AsyncLocalStorage<string>()
