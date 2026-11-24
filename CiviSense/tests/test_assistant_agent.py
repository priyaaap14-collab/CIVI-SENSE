"""Gemini assistant state-machine tests using a scripted async client and in-memory MCP."""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import Client
from civisense_mcp.server import build_server
from core.assistant import Assistant, AgentConfig, new_state, trim_history
from tests.test_mcp_protocol import FakeBackend

SECRET = "s3"


def fc(name, **args):
    return NS(name=name, args=args)


def tx(text):
    return NS(text=text, function_call=None)


def call_part(name, **args):
    return NS(text=None, function_call=fc(name, **args))


def response(*parts):
    return NS(
        candidates=[
            NS(content=NS(parts=list(parts)))
        ]
    )


class ScriptedModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


class ScriptedLLM:
    def __init__(self, script):
        self.aio = NS(models=ScriptedModels(script))

    @property
    def calls(self):
        return self.aio.models.calls


def make(script, backend=None, voice=False):
    backend = backend or FakeBackend()
    cfg = AgentConfig("m", 100, 3, SECRET, 7, "cit", "CITIZEN", voice=voice)

    def factory(token):
        return Client(build_server(backend, SECRET, static_token=token))

    llm = ScriptedLLM(script)
    return Assistant(cfg, llm, factory), backend, llm


async def collect(generator):
    return [event async for event in generator]


SUB = {
    "title": "Garbage overflow",
    "description": "Garbage is overflowing near my apartment for days",
}


class T(unittest.TestCase):
    def setUp(self):
        import core.assistant as assistant_module
        assistant_module._tool_cache.update(at=0.0, tools=None)

    def test_read_tool_roundtrip_and_hidden_approval_token(self):
        a, backend, llm = make([
            response(call_part("classify_complaint", text="garbage near my house")),
            response(tx("It is garbage.")),
        ])

        state = new_state()
        events = asyncio.run(collect(a.turn(state, "hi")))

        self.assertEqual(
            [event["type"] for event in events],
            ["tool_start", "tool_done", "text", "done"],
        )
        self.assertEqual(events[0]["label"], "Analysing complaint…")

        config = llm.calls[0]["config"]
        declarations = config.tools[0].function_declarations
        names = {tool.name for tool in declarations}
        self.assertEqual(len(names), 10)

        for tool in declarations:
            schema = tool.parameters_json_schema
            self.assertNotIn("approval_token", schema.get("properties", {}))

        self.assertTrue(config.automatic_function_calling.disable)
        self.assertEqual(backend.calls[0][:2], ("classify_complaint", 7))

    def test_submit_pauses_then_confirm_executes(self):
        a, backend, llm = make([
            response(call_part("submit_complaint", **SUB, ward="X", approval_token="forged")),
            response(tx("Done")),
        ], voice=True)

        state = new_state()
        events = asyncio.run(collect(a.turn(state, "submit")))

        self.assertEqual(events[-1]["type"], "confirmation")
        self.assertEqual(backend.calls, [])
        self.assertEqual(state["pending"][0]["args"]["input_method"], "VOICE")
        self.assertNotIn("approval_token", state["pending"][0]["args"])

        pending_id = events[-1]["pending_id"]
        events2 = asyncio.run(collect(a.decide(state, pending_id, True)))

        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][2]["input_method"], "VOICE")
        self.assertEqual(
            [event["type"] for event in events2],
            ["tool_start", "tool_done", "text", "done"],
        )

        last_parts = llm.calls[-1]["contents"][-1]["parts"]
        self.assertEqual(last_parts[0]["function_response"]["name"], "submit_complaint")
        self.assertEqual(state["pending"], [])

    def test_decline_never_executes(self):
        a, backend, _ = make([
            response(call_part("submit_complaint", **SUB)),
            response(tx("Cancelled")),
        ])

        state = new_state()
        events = asyncio.run(collect(a.turn(state, "x")))
        events2 = asyncio.run(collect(a.decide(state, events[-1]["pending_id"], False)))

        self.assertEqual(backend.calls, [])
        self.assertEqual(events2[-1]["type"], "done")

    def test_ignoring_card_cancels_and_history_stays_valid(self):
        a, backend, llm = make([
            response(call_part("submit_complaint", **SUB)),
            response(tx("ok")),
        ])

        state = new_state()
        asyncio.run(collect(a.turn(state, "x")))
        asyncio.run(collect(a.turn(state, "never mind")))

        self.assertEqual(backend.calls, [])
        user_parts = llm.calls[-1]["contents"][-1]["parts"]
        self.assertEqual(user_parts[0]["function_response"]["name"], "submit_complaint")
        self.assertEqual(user_parts[1]["text"], "never mind")

    def test_stale_or_wrong_pending_id_rejected(self):
        a, backend, _ = make([
            response(call_part("submit_complaint", **SUB)),
        ])

        state = new_state()
        events = asyncio.run(collect(a.turn(state, "x")))
        result = asyncio.run(collect(a.decide(state, "nope", True)))

        self.assertEqual(result[0]["type"], "error")
        self.assertEqual(backend.calls, [])

    def test_parallel_tools_one_sensitive(self):
        a, backend, llm = make([
            response(
                call_part("classify_complaint", text="garbage near me"),
                call_part("submit_complaint", **SUB),
            ),
            response(tx("fin")),
        ])

        state = new_state()
        events = asyncio.run(collect(a.turn(state, "x")))

        self.assertEqual(len(backend.calls), 1)
        asyncio.run(collect(a.decide(state, events[-1]["pending_id"], True)))

        last_parts = llm.calls[-1]["contents"][-1]["parts"]
        names = [part["function_response"]["name"] for part in last_parts]
        self.assertEqual(names, ["classify_complaint", "submit_complaint"])

    def test_tool_error_is_passed_to_model(self):
        a, backend, llm = make([
            response(call_part("check_complaint_status", ref="CS-2026-000404")),
            response(tx("not found")),
        ])

        asyncio.run(collect(a.turn(new_state(), "x")))

        result_part = llm.calls[-1]["contents"][-1]["parts"][0]
        result = result_part["function_response"]["response"]
        self.assertTrue(result["is_error"])
        self.assertIn("not_found", result["content"])

    def test_round_limit(self):
        a, _, _ = make([
            response(call_part("classify_complaint", text="garbage near me"))
            for _ in range(5)
        ])

        events = asyncio.run(collect(a.turn(new_state(), "x")))
        self.assertEqual(events[-1]["type"], "done")

    def test_trim_history(self):
        messages = [
            {"role": "user", "parts": [{"text": "a"}]},
            {"role": "model", "parts": [{"function_call": {"name": "n", "args": {}}}]},
            {"role": "user", "parts": [{"function_response": {"name": "n", "response": {"content": "r"}}}]},
            {"role": "model", "parts": [{"text": "t"}]},
            {"role": "user", "parts": [{"text": "b"}]},
        ]

        trimmed = trim_history(messages, 3)
        self.assertEqual(len(trimmed), 3)
        self.assertEqual(trimmed[-1]["parts"][0]["text"], "b")


if __name__ == "__main__":
    unittest.main()
