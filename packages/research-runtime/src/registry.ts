import type {ResearchImplementation} from './types.js'
export class ImplementationRegistry {
  private implementations=new Map<string,ResearchImplementation>()
  register(value:ResearchImplementation):()=>void {
    if(!/^[a-z][a-z0-9-]{0,63}$/.test(value.id)||value.apiVersion!==1)throw new Error('Unsupported research implementation')
    if(this.implementations.has(value.id)||this.list().some(item=>item.mainPreset===value.mainPreset||item.ui.route===value.ui.route))throw new Error('Research implementation, preset or UI route already registered')
    this.implementations.set(value.id,value)
    return ()=>{if(this.implementations.get(value.id)===value)this.implementations.delete(value.id)}
  }
  get(id:string){const value=this.implementations.get(id);if(!value)throw new Error(`Research implementation ${id} is not installed; install the locked package version to resume`);return value}
  forPreset(preset:string|undefined){return this.list().find(value=>value.mainPreset===preset)}
  list(){return [...this.implementations.values()]}
}
