import {prepareWorkspace as prepare,workspacePath as locate} from '@onefav/research-runtime'
export {DockerWorkspaces,WORKSPACE_PROGRAM,runCommand} from '@onefav/research-runtime'
export type {DockerWorkspaceOptions,CommandRunner,CommandResult} from '@onefav/research-runtime'
// The legacy environment name belongs to the compatibility adapter, not the runtime.
export const workspacePath=(root:string,runId:string,sessionId:string,workspaceRoot?:string)=>locate(root,runId,sessionId,workspaceRoot??process.env.ANYSOLVERS_DSH_WORKSPACES)
export const prepareWorkspace=(root:string,runId:string,sessionId:string,workspaceRoot?:string)=>prepare(root,runId,sessionId,workspaceRoot??process.env.ANYSOLVERS_DSH_WORKSPACES)
