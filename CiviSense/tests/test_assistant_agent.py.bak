"""Agent state-machine tests: scripted LLM + in-memory MCP server + fake backend. No Django, no network, no API key.
These prove the pause/confirm/decline logic, NOT Claude's real behaviour (that needs an API key; see README)."""
import asyncio, os, sys, unittest
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp import Client
from civisense_mcp.server import build_server
from core.assistant import Assistant, AgentConfig, new_state, trim_history
from tests.test_mcp_protocol import FakeBackend

SECRET = "s3"
def tu(i, name, **inp): return NS(type="tool_use", id=i, name=name, input=inp)
def tx(t): return NS(type="text", text=t)

class ScriptedLLM:
    def __init__(self, script): self.script, self.calls = list(script), []
    @property
    def messages(self): return self
    async def create(self, **kw):
        self.calls.append(kw); return NS(content=self.script.pop(0))

def make(script, backend=None, voice=False):
    backend = backend or FakeBackend()
    cfg = AgentConfig("m", 100, 3, SECRET, 7, "cit", "CITIZEN", voice=voice)
    def factory(token): return Client(build_server(backend, SECRET, static_token=token))
    llm = ScriptedLLM(script)
    return Assistant(cfg, llm, factory), backend, llm

async def collect(gen): return [e async for e in gen]
SUB = dict(title="Garbage overflow", description="Garbage is overflowing near my apartment for days")

class T(unittest.TestCase):
    def setUp(self):
        import core.assistant as a; a._tool_cache.update(at=0.0, tools=None)

    def test_read_tool_roundtrip_and_hidden_approval_token(self):
        a, b, llm = make([[tu("1", "classify_complaint", text="garbage near my house")], [tx("It is garbage.")]])
        st = new_state(); ev = asyncio.run(collect(a.turn(st, "hi")))
        self.assertEqual([e["type"] for e in ev], ["tool_start", "tool_done", "text", "done"])
        self.assertEqual(ev[0]["label"], "Analysing complaint…")
        names = {t["name"] for t in llm.calls[0]["tools"]}
        self.assertEqual(len(names), 10)
        for t in llm.calls[0]["tools"]: self.assertNotIn("approval_token", t["input_schema"]["properties"])
        self.assertEqual(b.calls[0][:2], ("classify_complaint", 7))

    def test_submit_pauses_then_confirm_executes(self):
        a, b, llm = make([[tu("1", "submit_complaint", **SUB, ward="X", approval_token="forged")], [tx("Done")]], voice=True)
        st = new_state(); ev = asyncio.run(collect(a.turn(st, "submit")))
        self.assertEqual(ev[-1]["type"], "confirmation"); self.assertEqual(b.calls, [])
        self.assertEqual(st["pending"][0]["args"]["input_method"], "VOICE")
        self.assertNotIn("approval_token", st["pending"][0]["args"])
        pid = ev[-1]["pending_id"]
        ev2 = asyncio.run(collect(a.decide(st, pid, True)))
        self.assertEqual(len(b.calls), 1)
        self.assertEqual(b.calls[0][2]["input_method"], "VOICE")
        self.assertEqual([e["type"] for e in ev2], ["tool_start", "tool_done", "text", "done"])
        last = llm.calls[-1]["messages"][-1]["content"][0]
        self.assertEqual(last["type"], "tool_result"); self.assertEqual(st["pending"], [])

    def test_decline_never_executes(self):
        a, b, _ = make([[tu("1", "submit_complaint", **SUB)], [tx("Cancelled")]])
        st = new_state(); ev = asyncio.run(collect(a.turn(st, "x")))
        ev2 = asyncio.run(collect(a.decide(st, ev[-1]["pending_id"], False)))
        self.assertEqual(b.calls, []); self.assertEqual(ev2[-1]["type"], "done")

    def test_ignoring_card_cancels_and_history_stays_valid(self):
        a, b, llm = make([[tu("1", "submit_complaint", **SUB)], [tx("ok")]])
        st = new_state(); asyncio.run(collect(a.turn(st, "x")))
        asyncio.run(collect(a.turn(st, "never mind, what is the weather")))
        self.assertEqual(b.calls, [])
        user_msg = llm.calls[-1]["messages"][-1]["content"]
        self.assertEqual([c["type"] for c in user_msg], ["tool_result", "text"])

    def test_stale_or_wrong_pending_id_rejected(self):
        a, b, _ = make([[tu("1", "submit_complaint", **SUB)]])
        st = new_state(); asyncio.run(collect(a.turn(st, "x")))
        ev = asyncio.run(collect(a.decide(st, "nope", True)))
        self.assertEqual(ev[0]["type"], "error"); self.assertEqual(b.calls, [])

    def test_parallel_tools_one_sensitive(self):
        a, b, llm = make([[tu("1", "classify_complaint", text="garbage near me"), tu("2", "submit_complaint", **SUB)], [tx("fin")]])
        st = new_state(); ev = asyncio.run(collect(a.turn(st, "x")))
        self.assertEqual(len(b.calls), 1)
        asyncio.run(collect(a.decide(st, ev[-1]["pending_id"], True)))
        ids = [c["tool_use_id"] for c in llm.calls[-1]["messages"][-1]["content"]]
        self.assertEqual(ids, ["1", "2"])

    def test_tool_error_is_passed_to_model(self):
        a, b, llm = make([[tu("1", "check_complaint_status", ref="CS-2026-000404")], [tx("not found")]])
        asyncio.run(collect(a.turn(new_state(), "x")))
        r = llm.calls[-1]["messages"][-1]["content"][0]
        self.assertTrue(r["is_error"]); self.assertIn("not_found", r["content"])

    def test_round_limit(self):
        a, b, _ = make([[tu(str(i), "classify_complaint", text="garbage near me")] for i in range(5)])
        ev = asyncio.run(collect(a.turn(new_state(), "x")))
        self.assertEqual(ev[-1]["type"], "done")

    def test_trim_history(self):
        m = [{"role":"user","content":"a"},{"role":"assistant","content":[{"type":"tool_use","id":"1","name":"n","input":{}}]},
             {"role":"user","content":[{"type":"tool_result","tool_use_id":"1","content":"r"}]},{"role":"assistant","content":[{"type":"text","text":"t"}]},
             {"role":"user","content":"b"}]
        self.assertEqual(trim_history(m, 3)[0]["content"], "b")

if __name__ == "__main__": unittest.main()
