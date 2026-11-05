import {existsSync} from 'node:fs'
import {join} from 'node:path'
export function runtimeDirectoryName(root:string,runId:string):'.runtime'|'dsh'{
  return (existsSync(join(root,'runs',runId,'.runtime','engine.json')) || existsSync(join(root,'runs',runId,'.runtime','run.json')))?'.runtime':'dsh'
}
