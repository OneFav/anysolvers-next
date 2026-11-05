"""Human-facing text only. These checks never grant or change execution authority.

The DSH runtime already requires Node; Intl.Segmenter gives Chinese/mixed text
the same word boundary rules as the reference prototype. If Node is unavailable,
use a conservative character count for CJK so fault notices still work offline.
"""
from functools import lru_cache
import re
import subprocess

IM_WORDS = 100
PURPOSES = frozenset(('acknowledgement', 'answer', 'result', 'attention', 'incident', 'recovery', 'research_signal'))
STAGES = {'initialize': '初始化', 'research': '研究', 'terminal': '最终交付'}
_SEGMENT = """let s='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>s+=x);
process.stdin.on('end',()=>{let n=0;for(const x of new Intl.Segmenter('zh',{granularity:'word'}).segment(s))if(x.isWordLike)n++;process.stdout.write(String(n))});"""
_FALLBACK = re.compile(r'[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0003134f\u3040-\u30ff\uac00-\ud7af]|[^\W_]+', re.UNICODE)


@lru_cache(maxsize=512)
def word_count(text):
    try:
        result = subprocess.run(['node', '-e', _SEGMENT], input=text, text=True,
                                capture_output=True, timeout=3, check=True)
        return int(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        # Split CJK before counting other words; \w also includes CJK characters.
        separated = re.sub(r'([\u3400-\u9fff\uf900-\ufaff\U00020000-\U0003134f\u3040-\u30ff\uac00-\ud7af])', r' \1 ', text)
        return len(_FALLBACK.findall(separated))


def checked_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('A nonempty outward message is required')
    # Bound segmentation work as well as final output. Never truncate a message.
    if len(text) > 4000 or word_count(text) > IM_WORDS:
        raise ValueError('Outward message exceeds 100 words (Chinese word segmentation). Rewrite as one short answer and only a necessary next action; keep evidence internal. Do not split into multiple messages.')
    return text


def notification(text, purpose, reference):
    if purpose not in PURPOSES or not isinstance(reference, str) or not reference:
        raise ValueError('Notification needs a supported purpose and source reference; progress is internal')
    return dict(text=checked_text(text), purpose=purpose, reference=reference, communication_version=1)


def subject(job):
    title = job.get('title', '')
    # Long machine-generated titles are stored intact; do not turn them into chat reports.
    if title and len(title) <= 60 and word_count(title) <= 20:
        return '「' + title + '」'
    return '当前' + STAGES.get(job.get('stage'), '任务') + '任务'


def incident_text(job, kind):
    message = {
        'host_lost': '执行端失联，暂时无法确认研究是否仍在运行。',
        'network': '模型服务连接中断，正在等待恢复。',
        'provider_quota': '模型服务额度不足，需要负责人处理后才能继续。',
        'provider_rate_limit': '模型服务暂时限流，正在等待恢复。',
        'model_unavailable': '模型服务不可用，正在等待恢复。',
        'worker_exit': '执行进程已退出，正在核对恢复条件。',
        'negative_results': '连续开发评测出现无效结果，需要检查失败原因；这不代表最终成绩。',
        'stagnation': '近期开发评测未刷新最佳成绩，可据此评估下一轮实验方向。',
    }.get(kind, '执行状态需要核对，暂时无法继续；请维护方检查执行记录。')
    return subject(job) + message
