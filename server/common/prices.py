"""List prices of hello's paid DashScope (阿里云百炼) services that are not billed per model token in our code
(2026-11-16). Used for the run log's cost estimates (docs/hello-log.md): cost = {amount, cur, est: true, basis}.
A price we could not confirm on an official page is None: the log then records amount None and keeps the usage.
Standard (list) prices of 华北2（北京）, without free quotas, trial periods or promotions.
"""

# 听悟 realtime (tingwu-meeting, type realtime, one audio input, no LLM summary features in our createTask):
# 实时会议转写（送说话人分离）0.6 元/小时, billed on the audio duration sent (silence included with one input).
TINGWU_CNY_PER_HOUR = 0.6

# Web search of the 围观 realtime session (enable_search; Omni / realtime models use the agent strategy):
# 4 元 per 1000 calls.
REALTIME_SEARCH_CNY_PER_CALL = 0.004

# soul-tts speaks with qwen-audio-3.1-tts-flash, which is billed per token, not per 10k characters:
# there is no per-character list price for it, so this stays unknown (kept so nobody adds a guessed one).
TTS_CNY_PER_10K_CHARS = None
# Its token prices, 元 per million tokens (ruling R15): soul-tts's call.tts cost = input_tokens × IN + output_tokens
# × OUT from the usage DashScope reports at task-finished; amount None when there is no usage.
TTS_CNY_PER_M_IN = 1.5
TTS_CNY_PER_M_OUT = 12.0

BASIS = {
    "TINGWU_CNY_PER_HOUR": "https://help.aliyun.com/zh/model-studio/tingwu-meeting-summary-overview and "
                           "https://help.aliyun.com/zh/tingwu/pricing-and-billing-rules/ (实时会议转写 0.6 元/小时), "
                           "looked up 2026-11-16",
    "REALTIME_SEARCH_CNY_PER_CALL": "https://help.aliyun.com/zh/model-studio/web-search 计费说明 "
                                    "(agent 策略 4 元/千次, 华北2 北京), looked up 2026-11-16",
    "TTS_CNY_PER_10K_CHARS": "unknown (looked up 2026-11-16): qwen-audio-3.1-tts-flash is billed per token, "
                             "see TTS_CNY_PER_M_IN / TTS_CNY_PER_M_OUT",
    "TTS_CNY_PER_M_IN": "https://help.aliyun.com/zh/model-studio/qwen-audio-3-1-tts-flash "
                        "(华北2 北京: 输入 1.5 元/百万 Token), looked up 2026-11-16",
    "TTS_CNY_PER_M_OUT": "https://help.aliyun.com/zh/model-studio/qwen-audio-3-1-tts-flash "
                         "(华北2 北京: 输出 12 元/百万 Token), looked up 2026-11-16",
}
