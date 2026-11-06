import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type {} from '@deepseek-ai/dsh-commands'
import type { JsonObject, Scope } from '../services.js'

export type LaunchAction = 'start' | 'status' | 'final'
export interface LaunchRequest { action: LaunchAction | 'new'; target?: string; config?: string }
export const LAUNCH_HELP = '/anysolvers [STUDY_ID] 启动已封存研究；多个study时先选择。可用 --config configs/NAME.yaml 指定配置。/anysolvers status STUDY_ID、/anysolvers final STUDY_ID；也可指定STUDY_ID/001。已有批次默认查看状态，不重放研究。界面保存参数后，可用 /anysolvers new STUDY_ID --config PATH 显式启动新批次。'

declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    'anysolvers-command': { kind: 'anysolvers-command'; studyId: string; action: LaunchAction }
  }
}

function object(value: unknown, field: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid launch ' + field)
  return value as JsonObject
}
function string(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value) throw new Error('Invalid launch ' + field)
  return value
}

/** Parses only command data; no shell interpolation or benchmark aliases. */
export function parseLaunchInput(raw: string): LaunchRequest | { action: 'help' } {
  const words = raw.trim().split(/\s+/).filter(Boolean)
  if (words[0] === 'help') {
    if (words.length !== 1) throw new Error(LAUNCH_HELP)
    return { action: 'help' }
  }
  const first = words[0]
  const action = first && ['start', 'new', 'status', 'final'].includes(first) ? words.shift() as LaunchRequest['action'] : 'start'
  const result: LaunchRequest = { action }
  while (words.length) {
    const word = words.shift()!
    if (word === '--config') {
      const config = words.shift()
      if (result.config || !config || config.startsWith('-')) throw new Error(LAUNCH_HELP)
      result.config = config
    } else {
      if (result.target || word.startsWith('-') || !/^[a-zA-Z0-9][a-zA-Z0-9_.-]*(?:\/[0-9]{3,})?$/.test(word)) throw new Error(LAUNCH_HELP)
      result.target = word
    }
  }
  if (action === 'new' && (!result.target || result.target.includes('/') || !result.config)) throw new Error('新批次需要study ID和界面保存的 --config 配置。')
  return result
}

/** Fixed research procedure over the selected standard initialization. */
export function launchPlan(profile: JsonObject, requested: LaunchAction): { action: LaunchAction; instructions: string } {
  if (profile.schema_version !== 1) throw new Error('Unsupported launch profile')
  const study = string(profile.study_id, 'study'), config = string(profile.config, 'config')
  const budgets = object(profile.budgets, 'budgets')
  if (!Array.isArray(profile.existing_runs) || profile.existing_runs.some(id => typeof id !== 'string')) throw new Error('Invalid launch run list')
  const latest = profile.selected_run ?? profile.existing_runs.at(-1)
  const action = requested === 'start' && latest ? 'status' : requested
  if (action !== 'start' && !latest) throw new Error('这个study还没有研究批次，请先运行 /anysolvers ' + study + '。')
  const datasets = Array.isArray(profile.datasets) ? profile.datasets.map(value => object(value, 'dataset')) : []
  const anchors = datasets.filter(value => value.purpose === 'anchor').map(value => string(value.dataset_id, 'anchor id'))
  const finals = datasets.filter(value => value.purpose === 'final').map(value => string(value.dataset_id, 'final id'))
  const intro = `AnySolvers ${action}；标准初始化已经由可信 launch-profile 接口校验，不需要再次调用 study_launch。\n研究与范围：${JSON.stringify(profile)}\n`
  if (action === 'status') return { action, instructions: intro + `仅查看现有批次 ${latest}：调用study_status、study_check、study_audit和study_evidence，汇总进度、可信成绩、冻结候选和未决工作。不要创建批次、研究员或新测量。未知执行保留现场。` }
  if (action === 'final') {
    if (!profile.final_permitted || !finals.length) return { action, instructions: intro + `调用final_status查看终局状态；本批未允许最终评测或没有封存final数据，不测final，不修改封存配置。读取final/visualization契约，用final_evidence整理已有开发证据，经final_write/final_deliver交付开发报告；然后visualization_render和visualization_deliver产出开发图表，最后final_status明确未完成独立最终测试。没有已结束证据时交付失败说明，不制造图表或分数。` }
    return { action, instructions: intro + `继续批次 ${latest} 的最终阶段。读取final/visualization契约，核对所有研究员已结束、pending为空且候选已冻结；说明所有封存final数据集${JSON.stringify(finals)}与剩余预算，再调用final_measure让App请求范围审批。每个final数据集仅测baseline和冻结候选，评测ID分别固定为final-baseline-<dataset_id>、final-selected-<dataset_id>；已有结果先读取，不换ID重试。未获批准就停止。按契约保存并final_deliver报告，分别报告开发/最终成绩、合法率、失败、会话与预算，随后必须用visualization_render从报告引用的评测生成图像与绘图数据，并visualization_deliver登记图表；最后final_status核对final覆盖、报告和图表均完成。最终反馈不回流研究，不能更换候选或启动新研究。` }
  }
  if (!anchors.length) throw new Error('所选研究缺少封存的anchor开发数据。')
  const reserve = profile.final_permitted ? finals.length * 2 : 0
  const allowance = budgets.evaluation_calls === null ? '评测次数不限；不人为分割每轮额度。' : `总评测额度为${budgets.evaluation_calls}次，包含基线与最终测量；为最终比较规划保留${reserve}次，此为调度约定，不能声称有专用硬保留。`
  return { action, instructions: intro + `用户发起了所选已初始化研究。只使用该study的问题、数据、初始想法和配置，不替换成内置样例。先简短说明有效预算、研究模型和算力，然后按配置执行。\n1. study_check(study_id=${study})、study_doctor；不匹配时停止。读取research/evaluation/selections契约。\n2. batch_init(study_id=${study},config=${config},researchers=${budgets.researchers},rounds=${budgets.rounds_per_researcher})，后续使用实际返回run_id，不猜编号。study_config核对预算和模型。\n3. 对全部anchor数据集${JSON.stringify(anchors)}调用baseline_measure测baseline，evaluation固定baseline-<dataset_id>。基线失败先停止处理。\n4. research_start使用从封存ideas确定的assignments=${JSON.stringify(profile.assignments)}且wait=true，各actor独立推进配置中的轮次；后续轮由主会话执行selections方法：study_selection_context读取证据，study_selection_report_context/study_selection_report_submit只为每轮最后checkpoint指定的最终封存候选写独立方法与可信成绩报告（含最后一轮），再study_selection_submit封存下一轮idea；研究员只写checkpoint，不拷贝旧对话、不伪造native ID。${allowance}\n5. 全部会话结束后检查status/audit/evidence；任何pending、未知执行或清理不明都停止，不换ID绕过。存在符合final契约的合法anchor候选才final_select冻结最佳候选。\n6. 汇总开发比较、会话交接、剩余额度及冻结候选，然后读取final/visualization契约并继续终局。配置允许final时，调用final_measure让App请求范围审批，按每个final数据集测baseline和冻结候选（固定ID final-baseline-<dataset_id>、final-selected-<dataset_id>），已有结果先读，不换ID重试；配置未允许时不测final并明确开发报告的限制。使用final_evidence整理证据，以final_write/final_deliver交付报告，然后visualization_render和visualization_deliver产出图表，最后final_status核对完成情况。失败则如实交付失败报告，不虚构分数或获胜候选。` }
}

/** No target: one sealed study is unambiguous; otherwise return choices without launching. */
export async function prepareLaunch(ctx: Context, request: LaunchRequest, signal: AbortSignal): Promise<JsonObject> {
  if (request.action === 'new') {
    if (!request.target || request.target.includes('/') || !request.config) throw new Error('新批次需要study ID和保存的配置。')
    const profile = await ctx.studies.call('ui-launch-profile', { config: request.config }, { studyId: request.target }, signal)
    return { profile, ...launchPlan(profile, 'start') }
  }
  let target = request.target
  if (!target) {
    const catalog = await ctx.studies.call('launch-list', {}, {}, signal)
    if (!Array.isArray(catalog.studies)) throw new Error('Invalid initialized study catalog')
    if (catalog.studies.length !== 1) return { action: 'choose', catalog,
      instructions: catalog.studies.length ? '有多个已封存study。请明确选择study_id，再发送 /anysolvers STUDY_ID；不要自动选择样例。' : '没有符合格式的已封存研究。请先完成初始化并封存task、datasets、ideas；不启动研究或替换成样例。' }
    target = string(object(catalog.studies[0], 'study').study_id, 'study id')
  }
  const scope: Scope = target.includes('/') ? { runId: target } : { studyId: target }
  const profile = await ctx.studies.call('launch-profile', request.config ? { config: request.config } : {}, scope, signal)
  return { profile, ...launchPlan(profile, request.action) }
}

/** Scoped native entry; never runs research at plugin load. */
export function registerLaunchCommand(ctx: Context): void {
  const pending = new WeakSet<Agent>(), started = new WeakMap<Agent, Set<string>>()
  ctx.commands.register({ name: 'anysolvers', description: '启动已初始化并封存的研究 · 选择study · 使用其配置、数据与预算',
    input: { hint: '[start|new|status|final] [STUDY_ID|STUDY_ID/001] [--config PATH]' },
    handler: async ({ agent, rawInput, signal }) => {
      const request = parseLaunchInput(rawInput)
      await ctx.studyPolicy.authorize(agent, 'study_launch')
      if (request.action === 'help') return { kind: 'success', text: LAUNCH_HELP }
      if (pending.has(agent) || agent.status !== 'idle' || agent.inbox.nextTurn.length || agent.inbox.nextStep.length) return { kind: 'error', text: '当前会话正在运行或排队，请等待当前操作结束。' }
      pending.add(agent)
      let queued = false
      try {
        const prepared = await prepareLaunch(ctx, request, signal)
        if (prepared.action === 'choose') {
          const catalog = object(prepared.catalog, 'catalog')
          const choices = (catalog.studies as JsonObject[]).map(item => `${item.study_id}（${item.task_id}）`).join('\n')
          return { kind: 'success', text: string(prepared.instructions, 'selection instructions') + (choices ? '\n' + choices : '') }
        }
        const profile = object(prepared.profile, 'profile'), studyId = string(profile.study_id, 'study id')
        if (prepared.action === 'start' && (agent.session.firstLiveSeq > 0 || started.get(agent)?.has(studyId))) return { kind: 'error', text: '此研究已在本会话提交，或这是恢复的旧会话。先查看记录，不自动重放启动。' }
        signal.throwIfAborted()
        if (agent.status !== 'idle' || agent.inbox.nextTurn.length || agent.inbox.nextStep.length) return { kind: 'error', text: '会话已开始其他工作，本次未追加请求。' }
        if (prepared.action === 'start') {
          const studies = started.get(agent) ?? new Set<string>()
          studies.add(studyId); started.set(agent, studies)
        }
        if (request.action === 'new') {
          await ctx.studies.call('ui-claim', { config: request.config!, session: agent.id }, { studyId }, signal)
        }
        queued = true
        agent.followup(createUserMessage({ content: [{ type: 'text', text: string(prepared.instructions, 'instructions') }],
          source: { kind: 'anysolvers-command', studyId, action: prepared.action as LaunchAction } }))
        return { kind: 'success', text: prepared.action === 'start' ? `已提交研究 ${studyId}，按其配置推进；最终测量保留App审批。` : prepared.action === 'status' ? `已请求查看 ${studyId} 的现有批次，不会新建研究。` : `已请求 ${studyId} 的最终阶段，实际测量仍需App范围审批。` }
      } catch (error) {
        if (queued) throw new Error('请求投递状态不明，请核对本会话与已有批次，不要重复提交。')
        throw error
      } finally { pending.delete(agent) }
    },
  })
}
