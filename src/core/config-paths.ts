import { accessSync, constants } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const implementationRoot = fileURLToPath(new URL('../../', import.meta.url))
export function directoryPath(value: string, base = implementationRoot): string {
  if (!value || value.includes('\0')) throw Error('A nonempty configured path is required')
  return resolve(base, value === '~' ? homedir() : value.startsWith('~/') ? join(homedir(), value.slice(2)) : value)
}
export function executablePath(value: string, base = implementationRoot): string {
  if (isAbsolute(value) || /[/\\]/.test(value) || value.startsWith('.')) return directoryPath(value, base)
  for (const part of (process.env.PATH ?? '').split(delimiter).filter(Boolean)) {
    const path = resolve(part, value)
    try { accessSync(path, constants.X_OK); return path } catch { /* Try the next PATH entry. */ }
  }
  throw Error('Configured executable is not on PATH: ' + value)
}
