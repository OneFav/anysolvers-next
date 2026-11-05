import type { Context } from '@deepseek-ai/cordis'
import { register, text } from '../core/tool.js'
import { terminalTool } from '../core/terminal-tools.js'

export const name = 'anysolvers-final-tools'
export const inject = ['tools', 'systemPrompt', 'studies', 'studyPolicy']
export function apply(ctx: Context): void {
  ctx.systemPrompt.section({ name: 'anysolvers:final', order: 110,
    text: `After research settles, read the bound final contract with study_module(slot=final). Use final_status to check terminal completion independently of study_status.phase=reported. Reconcile all pending work and end researchers before final_select. Freeze only the eligible development winner. If the run permits final evaluation, proceed to final_measure for baseline and the frozen candidate on every sealed final dataset, within the existing authorization and budget. Reuse fixed evaluation IDs and read existing results; never replay uncertain work with new IDs. If final is not permitted, report that limitation without editing sealed configuration or claiming final completion.
Use final_evidence with evaluation IDs for verified, sanitized results. Write a report and manifest using final_write, then final_deliver(kind=report). Keep development and final conclusions separate. A null score means unavailable, never zero or an average over only successful cases. Preserve failure and incomplete-session records. After report delivery continue to the visualization plugin. All work stays in this main session; final feedback never returns to researchers.` })
  terminalTool(ctx,'final_status','terminal-status','Read verified final coverage, report/figure delivery and overall completion.',{})
  terminalTool(ctx,'final_evidence','terminal-evidence','Read settled trusted evidence; incomplete/invalid overall scores are null.',
    { evaluation_ids: { type:'array', required:true, items:{type:'string'} } })
  terminalTool(ctx,'final_select','select','Freeze the best eligible anchor candidate after research settles.',{candidate:text('Candidate ID')})
  terminalTool(ctx,'final_write','terminal-report-write','Write a UTF-8 report or report manifest under final/.',
    { path:text('Run-relative report draft path'),content:text('Complete UTF-8 content'),encoding:{type:'string',enum:['utf8']} })
  terminalTool(ctx,'final_deliver','terminal-report-deliver','Validate and register a report, then continue to visualization.',
    { kind:{type:'string',enum:['report']},manifest:text('Run-relative report manifest') })
  register(ctx,'final_measure','Evaluate baseline or the frozen candidate on sealed final data using scoped authorization.',
    {run_id:text('Study/batch identifier'),candidate:text('baseline or frozen candidate ID'),dataset:text('Sealed final dataset ID'),evaluation:text('Stable evaluation ID')},
    async(a,e)=>{
      await ctx.studyPolicy.authorize(e.agent,'final_measure',{runId:a.run_id})
      await ctx.studyPolicy.authorizeFinal(e.agent,a.run_id,e.signal)
      return ctx.studies.call('measure',{candidate:a.candidate,dataset:a.dataset,evaluation:a.evaluation,scope:'final'},{runId:a.run_id},e.signal)
    })
}
