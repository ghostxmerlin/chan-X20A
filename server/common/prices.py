"""List prices of hello's paid DashScope (阿里云百炼) services that are not billed per model token in our code
(2026-11-16), and the aihubmix models of 深入研究 (2026-11-18, below). Used for the run log's cost estimates (docs/hello-log.md): cost = {amount, cur, est: true, basis}.
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

# aihubmix (OpenAI-compatible, https://aihubmix.com/v1), USD, for 深入研究 (server/mind/research.py, 2026-11-18).
# Per million tokens: (input, cached input, output); output includes reasoning tokens. Above 272k tokens of context
# the second tuple applies (research calls stay far below it). The web_search tool of the Responses API is billed per
# search request on top of the tokens.
AIHUBMIX_USD_PER_M = {"gpt-6.2-sol": (2.0, 0.1, 10.0), "gpt-6.1-sol": (2.0, 0.1, 10.0), "gpt-5.6-sol": (4.0, 0.4, 20.0),
                      "gemini-3-flash-preview-search": (0.5, 0.05, 3.0)}
AIHUBMIX_USD_PER_M_LONG = {"gpt-6.2-sol": (4.0, 0.2, 15.0), "gpt-6.1-sol": (4.0, 0.2, 15.0),
                           "gpt-5.6-sol": (8.0, 0.8, 30.0)}
AIHUBMIX_LONG_CONTEXT_TOKENS = 272_000
AIHUBMIX_SEARCH_USD_PER_CALL = {"gpt-6.2-sol": 0.01, "gpt-6.1-sol": 0.01, "gpt-5.6-sol": 0.01,
                                "gemini-3-flash-preview-search": 0.014}

BASIS = {
    "AIHUBMIX_USD_PER_M": "https://aihubmix.com/api/v1/models (public model list, pricing / pricing_lines: "
                          "prompt_tokens, cached_tokens, completion_tokens tier1), looked up 2026-11-18",
    "AIHUBMIX_USD_PER_M_LONG": "https://aihubmix.com/api/v1/models (pricing_lines tier2, > 272000 tokens), "
                               "looked up 2026-11-18",
    "AIHUBMIX_SEARCH_USD_PER_CALL": "https://aihubmix.com/api/v1/models (pricing_lines web_search_requests, "
                                    "USD per request), looked up 2026-11-18",
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
