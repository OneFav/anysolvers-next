import type { Context } from '@deepseek-ai/cordis'
import { text } from '../core/tool.js'
import { terminalTool } from '../core/terminal-tools.js'

export const name = 'anysolvers-visualization-tools'
export const inject = ['tools', 'systemPrompt', 'studies', 'studyPolicy']
export function apply(ctx: Context): void {
  ctx.systemPrompt.section({ name:'anysolvers:visualization',order:120,
    text:`After registering the report, read study_module(slot=visualization) and produce its figure deliverable in this main session. Use visualization_evidence to select recorded evaluation IDs already cited by the report. Call visualization_render with chart=comparison for objective-score comparisons, or chart=by_metric with group_by=<a recorded metrics object key> and metric=<numeric field>. For example by_states/score works only when those keys really exist in the evaluator output. The fixed renderer loads scores itself; never supply numeric plot values, generated code, base64 images or new measurements. Different datasets remain separate panels; failed/missing results remain visible.
Use a stable figure_id; register the returned manifest with visualization_deliver. The tool saves PNG, plotting JSON and the module manifest with evaluation hashes. Finish with visualization_status/final_status. A report alone is not completion. If final was not permitted, deliver development-only figures and state that final evaluation remains unavailable. If no ended evidence exists, explain why no figure can be produced.` })
  terminalTool(ctx,'visualization_status','terminal-status','Inspect registered figures and terminal completion.',{})
  terminalTool(ctx,'visualization_evidence','terminal-evidence','Read verified evidence available for report-backed plotting.',
    {evaluation_ids:{type:'array',required:true,items:{type:'string'}}})
  terminalTool(ctx,'visualization_render','visualization-render','Render a PNG and plotting data from trusted reported evaluations; does not measure or register.',
    {figure_id:text('Stable figure identifier'),evaluation_ids:{type:'array',required:true,items:{type:'string'}},
     chart:{type:'string',enum:['comparison','by_metric']},group_by:text('For by_metric: recorded metrics object key, e.g. by_states',false),
     metric:text('Numeric field within each group; defaults to score',false),title:text('Optional descriptive chart title',false)})
  terminalTool(ctx,'visualization_deliver','visualization-deliver','Verify the renderer output and register the bound visualization module manifest.',
    {manifest:text('Manifest returned by visualization_render')})
}
