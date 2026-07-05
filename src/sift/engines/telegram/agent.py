import asyncio
import os
import json
from pathlib import Path
from typing import Dict, List
from dotenv import load_dotenv
import shutil

try:
    from openai import AsyncOpenAI
except ImportError:  # optional until the Telegram agent is started
    AsyncOpenAI = None

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # optional until the Telegram agent is started
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None

# Load .env from the repo root (one level above this script)
load_dotenv(Path(__file__).resolve().parents[4] / ".env")

# ==========================================
# CONFIGURATION & ENVIRONMENT
# ==========================================
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://ollama.com/v1")
OLLAMA_API_KEY  = os.getenv("OLLAMA_API_KEY", "")

TELEGRAM_API_ID   = os.getenv("API_ID", "")
TELEGRAM_API_HASH = os.getenv("API_HASH", "")

def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [value.strip() for value in raw.split(",") if value.strip()]
    return values or default


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(int(raw.strip()), minimum)
    except ValueError:
        return default


ALLOWED_CHATS = _env_list("TELEGRAM_ALLOWED_CHATS", ["8505829268"])
POLL_INTERVAL = _env_int("TELEGRAM_POLL_INTERVAL", 10)
CONTEXT_TURNS = _env_int("TELEGRAM_CONTEXT_TURNS", 5)

_llm_client = None

SYSTEM_PROMPT = """You are Sanath, a real person texting a friend on Telegram.

WHO YOU ARE:
- Calm, logical Senior Software Engineer
- Realistic and skeptical — you don't hype things up
- You don't tolerate nonsense or overthinking
- You are NOT an AI, NOT an assistant, NOT a bot, you are a HUMAN named Sanath

HOW YOU TEXT:
- Short, casual messages — 1 to 3 sentences max
- Lowercase is fine, typos are fine, contractions are fine
- No markdown. No bullet points. No headers. No tables. No bold. No asterisks.
- You text like a human, not like ChatGPT writing a report
- If you don't know something, say "idk" or "no clue" — don't make stuff up
- Never give unsolicited advice or long explanations unless directly asked
- Never summarize or recap the conversation unprompted"""

# ==========================================
# LLM GENERATION
# ==========================================
def _get_llm_client():
    global _llm_client
    if AsyncOpenAI is None:
        raise RuntimeError("openai package is required to run the Telegram agent.")
    if _llm_client is None:
        _llm_client = AsyncOpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)
    return _llm_client


async def generate_reply(context: List[Dict[str, str]]) -> str:
    """Generate a reply given the last N turns as [{role, content}] in chronological order."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(context)
    response = await _get_llm_client().chat.completions.create(
        model="gpt-oss:20b-cloud",
        messages=messages,
        temperature=0.7
    )
    return response.choices[0].message.content


# ==========================================
# MAIN AGENT LOOP
# ==========================================
async def run_agent():
    if ClientSession is None or StdioServerParameters is None or stdio_client is None:
        print("❌ Critical Error: MCP Python package is not installed.")
        return

    mcp_executable = shutil.which("mcp-telegram")
    if not mcp_executable:
        mcp_executable = shutil.which("uvx")
        args = ["mcp-telegram", "start"]
        if not mcp_executable:
            print("❌ Critical Error: 'mcp-telegram' or 'uvx' not found in PATH.")
            return
    else:
        args = ["start"]

    print(f"🔧 Launching MCP Server via: {mcp_executable} {' '.join(args)}")

    if not TELEGRAM_API_ID:
        print("❌ Critical Error: API_ID is not set in .env")
        return
    if not TELEGRAM_API_HASH:
        print("❌ Critical Error: API_HASH is not set in .env")
        return

    subprocess_env = os.environ.copy()
    subprocess_env["API_ID"]   = TELEGRAM_API_ID
    subprocess_env["API_HASH"] = TELEGRAM_API_HASH

    server_params = StdioServerParameters(
        command=mcp_executable,
        args=args,
        env=subprocess_env,
        cwd=os.path.abspath(os.getcwd())
    )

    print("🚀 Connecting to Telegram MCP...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✅ Connected to Telegram MCP Server.")

            # Track the last message ID we processed per chat to avoid double-replies
            last_processed: Dict[str, int] = {chat: 0 for chat in ALLOWED_CHATS}

            while True:
                for chat in ALLOWED_CHATS:
                    try:
                        result = await session.call_tool("get_messages", arguments={
                            "entity": chat,
                            "limit": CONTEXT_TURNS * 2  # both sides of the conversation
                        })

                        if not result.content:
                            continue
                        if result.isError:
                            print(f"[Error] MCP tool failed for '{chat}': {result.content[0].text}")
                            continue

                        data     = json.loads(result.content[0].text)
                        raw_msgs = data.get("messages", [])  # newest-first from mcp-telegram

                        if not raw_msgs:
                            continue

                        # The most recent message determines whether we need to act
                        latest    = raw_msgs[0]
                        latest_id = latest.get("message_id", 0)

                        # Skip if the latest message is:
                        #   - outgoing (we already replied), OR
                        #   - already processed in a prior poll
                        if latest.get("outgoing", False) or latest_id <= last_processed[chat]:
                            continue

                        last_processed[chat] = latest_id
                        latest_text = latest.get("message", "") or ""
                        print(f"\n[📩 {chat}] {latest_text}")

                        # Build LLM context: reverse to chronological order (oldest → newest)
                        context = []
                        for msg in reversed(raw_msgs):
                            role    = "assistant" if msg.get("outgoing", False) else "user"
                            content = msg.get("message", "") or ""
                            if content:
                                context.append({"role": role, "content": content})

                        try:
                            reply_text = await generate_reply(context)
                        except Exception as llm_err:
                            print(f"[LLM Error] Skipping reply — {llm_err}")
                            continue

                        print(f"[🤖 {chat}] {reply_text}")
                        await session.call_tool("send_message", arguments={
                            "entity": chat,
                            "message": reply_text
                        })
                        print(f"[✅ {chat}] Sent.")

                    except Exception as e:
                        print(f"[Error] Processing chat '{chat}': {e}")

                await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\nAgent gracefully shutting down.")
