import type {Context} from '@deepseek-ai/cordis'
import '@onefav/research-runtime'
export const name='dynamic-fixture-tools'
export const inject=['systemPrompt']
export function apply(ctx:Context){ctx.systemPrompt.section({name:'dynamic-fixture:contract',order:90,text:'This preset only runs the explicit deterministic dynamic-fixture acceptance protocol through /as-research. Results are simulated test fixtures. Do not start model research or candidate execution.'})}
