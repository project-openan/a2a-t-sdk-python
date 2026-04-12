import json
import sys
from xml import dom

sys.path.insert(0, "src")

from a2a_t.llm.client import LLMClient

client = LLMClient()

def do_complete():
    response_complete = client.complete(
        "请帮我查询广州今天天气怎么样."
    )
    print(response_complete)
    print()

def do_chat():
    response_first = client.chat(
        "请帮我查询广州今天天气怎么样."
    )
    response_second = client.chat(
        "今天去广州玩的话，需要注意些什么，可以穿短袖吗？",
        session_id=response_first.session_id,
    )
    print(response_second)
    print()

def do_structured():
    original_prompt="请帮我确认下IP为{{ip}}，MAC地址为{{mac}}的网元，当前状态工作状态怎么样。"
    processed_prompt="请帮我确认下IP为10.154.12.10，MAC地址为FA:16:3E:65:BE:DF的网元，当前状态工作状态怎么样。"
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个 Prompt 槽位提取器。"
                "请根据原始 prompt 模板和加工后的 prompt 提取槽位，并严格输出合法 json。"
                '输出 JSON 必须符合以下结构：{"slots":{"ip":"ip地址", "mac":"物理地址"}}。'
                "不要输出 markdown，不要输出解释文字，只输出 JSON 对象。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"原始 prompt 模板：\n{original_prompt}\n\n"
                f"加工后的 prompt：\n{processed_prompt}\n\n"
                "请提取槽位 ip、mac，并返回 json。"
            ),
        },
    ]

    json_schema = {
        "type": "object",
        "properties": {
            "ip": {"type": "string"},
            "mac": {"type": "string"},
        },
        "required": ["ip", "mac"],
    }

    response_structured = client.structured(messages=messages, json_schema=json_schema)
    print(response_structured)
    print()


if __name__ == "__main__":
    # do_complete()
    # do_chat()
    do_structured()