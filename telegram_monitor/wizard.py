#!/usr/bin/env python3
"""
Interactive configuration wizard for Telegram Monitor.
Uses Claude Agent SDK to ask questions and generate a new JSON config.

Usage: python configure.py
       python configure.py --config ukraine.json
"""

import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path

from telegram_monitor.utils import parse_config_arg

# Resolved separately from config.py because the wizard reads JSON directly
# without triggering config.py's module-level loading.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _pick_config_for_wizard() -> str:
    """Discover which config file to edit when none is specified."""
    configs_dir = _PROJECT_ROOT / "configs"
    available = sorted(configs_dir.glob("*.json")) if configs_dir.is_dir() else []

    # Legacy root config.json — backward compat
    legacy = _PROJECT_ROOT / "config.json"
    if legacy.exists() and legacy not in available:
        available.insert(0, legacy)

    if not available:
        # No configs exist yet — will create a new one
        return str(configs_dir / "default.json")

    if len(available) == 1:
        return str(available[0])

    # Multiple configs — ask which to edit
    print("Which config do you want to edit?\n")
    for i, path in enumerate(available, 1):
        label = path.name
        try:
            with open(path) as f:
                title = json.load(f).get("app_title", "")
            if title:
                label = f"{path.name}  ({title})"
        except (json.JSONDecodeError, OSError):
            pass
        print(f"  [{i}] {label}")

    print(f"  [{len(available) + 1}] Create new config")
    print()
    while True:
        try:
            choice = input(f"Select [1-{len(available) + 1}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(available):
                return str(available[n - 1])
            if n == len(available) + 1:
                try:
                    name = input("  Config filename (e.g. ukraine.json): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    sys.exit(0)
                if not name.endswith(".json"):
                    name += ".json"
                return str(configs_dir / name)
        print(f"  Enter a number between 1 and {len(available) + 1}.")


def main():
    # Early --config/-c parse
    _config_file = parse_config_arg()

    # Deferred import
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    # Resolve config path
    if _config_file:
        if os.path.isabs(_config_file):
            CONFIG_PATH = _config_file
        else:
            candidate = _PROJECT_ROOT / _config_file
            configs_candidate = _PROJECT_ROOT / "configs" / _config_file
            if candidate.exists():
                CONFIG_PATH = str(candidate)
            elif configs_candidate.exists():
                CONFIG_PATH = str(configs_candidate)
            else:
                # Default to configs/ directory for new files
                CONFIG_PATH = str(_PROJECT_ROOT / "configs" / _config_file)
    else:
        CONFIG_PATH = _pick_config_for_wizard()

    # ─── ANSI helpers ─────────────────────────────────────────────────────────────

    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    RESET = "\033[0m"

    DONE_SENTINEL = "<<<DONE>>>"

    CONVERSATION_SYSTEM_PROMPT = """\
You are a helpful configuration assistant for a Telegram channel monitoring tool.
Your job is to ask the user a series of questions to reconfigure the monitor for a new \
region, topic, or focus area.

Ask ONE question at a time. Be conversational and suggest examples where helpful.
You should ask 4-6 questions covering:
1. What region or topic the monitor should focus on (e.g. Ukraine conflict, China-Taiwan, etc.)
2. What types of sources to include (state media, opposition, OSINT, citizen journalism, etc.)
3. What specific topics/events to track (like the current TOPICS list)
4. Any preferences for analysis style, language focus, or timing (batch size, interval)
5. What the app title/branding should be
6. Any political stances or tones relevant to the new focus area

After you have enough information (4-6 questions answered), output the exact marker: <<<DONE>>>
Do NOT output <<<DONE>>> until you have gathered enough information.
If the user says "done" or "skip", accept what you have and output <<<DONE>>>.

Do NOT generate any configuration JSON — just gather requirements conversationally."""

    GENERATION_SYSTEM_PROMPT = """\
You are a configuration generator for a Telegram channel monitoring tool.
Given a conversation transcript where a user described what they want to monitor, \
generate a JSON configuration object.

You must output ONLY valid JSON (no markdown fences, no commentary) with this exact schema:

{
  "channels": {
    "Category Name": [
      {"username": "channel_username", "description": "Brief description (subscriber count if known)"}
    ]
  },
  "topics": ["Topic 1 to track", "Topic 2 to track"],
  "stances": ["stance1", "stance2"],
  "tones": ["tone1", "tone2"],
  "stance_color_rules": [
    {"keywords": ["keyword1", "keyword2"], "color": "red"},
    {"keywords": ["keyword3"], "color": "green"}
  ],
  "analysis_prompt_intro": "You are a senior intelligence analyst specializing in <region>. You are monitoring {n_messages} new messages from {n_sources} <region> Telegram channels.",
  "analysis_system_prompt": "You are a senior ...-focused intelligence analyst...",
  "caption_system_prompt": "You are a translator fluent in ... and English...",
  "caption_language_line": "Most are in ... or ...",
  "app_title": "... Telegram Intelligence Monitor",
  "summary_batch_size": 20,
  "summary_interval_seconds": 300
}

Guidelines:
- channels: Include 30-100 real Telegram channel usernames. Research and include actual \
channels relevant to the region/topic. Organize by category. Omit the @ prefix.
- topics: 1-5 specific intelligence topics to track.
- stances: Political stances relevant to the region (e.g. "pro-government", "opposition", etc.)
- tones: 4-6 tone descriptors (e.g. "urgent", "measured", "inflammatory", "factual", "emotional")
- stance_color_rules: Map stance keywords to terminal colors (red, green, yellow, blue, magenta, cyan)
- analysis_prompt_intro: Region-specific intro for the analysis prompt. Must include \
{n_messages} and {n_sources} placeholders. Example: "You are a senior intelligence analyst \
specializing in Ukraine. You are monitoring {n_messages} new messages from {n_sources} \
Ukrainian/Russian Telegram channels."
- analysis_system_prompt: Tailor the analyst persona to the region. Mention relevant languages.
- caption_system_prompt: Mention the relevant languages for translation.
- caption_language_line: Describe the primary languages (e.g. "Most are in Ukrainian or Russian")
- app_title: Match the new focus area.
- summary_batch_size: Default 20 unless user requested differently.
- summary_interval_seconds: Default 300 unless user requested differently.

Output ONLY the JSON object. No markdown fences. No explanation."""

    # ─── Claude SDK helper ────────────────────────────────────────────────────────

    async def _call_claude(prompt: str, system: str, model: str = "sonnet") -> str:
        """Single Claude call via Agent SDK. Returns the text response."""
        parts = []
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(max_turns=1, model=model, system_prompt=system),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        return "\n".join(parts)

    # ─── Phase 1: Read current JSON config ──────────────────────────────────────

    def load_current_config() -> dict:
        """Load the current JSON config file."""
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def _count_channels(cfg: dict) -> int:
        """Count total channels from the categorized dict."""
        return sum(len(chs) for chs in cfg.get("channels", {}).values())

    def show_current_config(cfg: dict):
        """Display a summary of the current configuration."""
        interval = cfg.get("summary_interval_seconds", 300)
        interval_min = interval // 60
        topics = cfg.get("topics", [])

        print(f"\n  {DIM}Current setup:{RESET}")
        print(f"    Channels : {CYAN}{_count_channels(cfg)}{RESET}")
        print(f"    Topics   : {CYAN}{', '.join(topics) or '(none)'}{RESET}")
        print(f"    Analysis : every {CYAN}{cfg.get('summary_batch_size', 20)}{RESET} messages "
              f"or {CYAN}{interval_min}{RESET} min")
        print(f"    Title    : {CYAN}{cfg.get('app_title', '(none)')}{RESET}")
        print()

    # ─── Phase 2: Conversational Q&A ─────────────────────────────────────────────

    async def run_conversation() -> str:
        """Interactive Q&A loop with Claude. Returns the full transcript."""
        transcript = ""

        print("  Claude will ask you a few questions to reconfigure the monitor.")
        print(f"  {DIM}Type \"done\" to skip ahead · \"quit\" to exit{RESET}\n")

        # Get Claude's first question
        opening = (
            "The user wants to reconfigure their Telegram monitoring setup. "
            "Ask your first question to understand what they want to monitor."
        )
        response = await _call_claude(opening, CONVERSATION_SYSTEM_PROMPT)

        for turn in range(10):  # safety limit
            # Check if Claude signaled completion
            if DONE_SENTINEL in response:
                response = response.replace(DONE_SENTINEL, "").strip()
                if response:
                    print(f"  {response}\n")
                break

            print(f"  {response}\n")
            transcript += f"Assistant: {response}\n\n"

            # Get user input
            try:
                user_input = input(f"  {GREEN}>{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n")
                sys.exit(0)

            if user_input.lower() == "quit":
                print("\n  Exiting without changes.")
                sys.exit(0)

            transcript += f"User: {user_input}\n\n"

            if user_input.lower() == "done":
                break

            # Continue conversation with full transcript
            continuation = (
                f"Conversation so far:\n{transcript}\n"
                f"Continue the configuration interview. Ask your next question, "
                f"or if you have enough information, output {DONE_SENTINEL}."
            )
            response = await _call_claude(continuation, CONVERSATION_SYSTEM_PROMPT)

        return transcript

    # ─── Phase 3: JSON generation ─────────────────────────────────────────────────

    async def generate_config(transcript: str) -> dict:
        """Ask Claude to generate structured config JSON from the conversation."""
        print(f"  {DIM}Generating new configuration...{RESET}\n")

        prompt = (
            f"Based on this conversation, generate the configuration JSON:\n\n"
            f"{transcript}"
        )

        for attempt in range(2):
            response = await _call_claude(prompt, GENERATION_SYSTEM_PROMPT)

            # Strip markdown fences if present
            response = response.strip()
            if response.startswith("```"):
                response = re.sub(r"^```(?:json)?\s*\n?", "", response)
                response = re.sub(r"\n?```\s*$", "", response)

            try:
                return json.loads(response)
            except json.JSONDecodeError as e:
                if attempt == 0:
                    print(f"  {YELLOW}JSON parse error, retrying...{RESET}")
                    prompt = (
                        f"Your previous response was not valid JSON. Error: {e}\n\n"
                        f"Based on this conversation, generate the configuration JSON. "
                        f"Output ONLY valid JSON, no markdown fences:\n\n{transcript}"
                    )
                else:
                    print(f"  {RED}Failed to generate valid JSON after 2 attempts.{RESET}")
                    print(f"  {DIM}Raw response:{RESET}\n{response[:500]}")
                    sys.exit(1)

    # ─── Phase 4: Validate and write JSON ───────────────────────────────────────

    def validate_config(cfg: dict) -> bool:
        """Check that the generated config has required keys."""
        required = [
            "channels", "topics", "stances", "tones", "stance_color_rules",
            "analysis_prompt_intro", "analysis_system_prompt",
            "caption_system_prompt", "caption_language_line",
            "app_title", "summary_batch_size", "summary_interval_seconds",
        ]
        missing = [k for k in required if k not in cfg]
        if missing:
            print(f"  {RED}Missing required keys: {', '.join(missing)}{RESET}")
            return False
        return True

    # ─── Phase 5: Diff summary & write ──────────────────────────────────────────

    def show_diff(old_cfg: dict, new_cfg: dict):
        """Show a summary of what will change."""
        print(f"  {'─' * 50}")
        print(f"  {BOLD}Changes:{RESET}")

        # Channels
        old_count = _count_channels(old_cfg)
        new_count = _count_channels(new_cfg)
        color = YELLOW if old_count != new_count else DIM
        print(f"  * Channels     {old_count}  →  {color}{new_count}{RESET}")

        # Topics
        old_topics = old_cfg.get("topics", [])
        new_topics = new_cfg.get("topics", old_topics)
        print(f"  * Topics       {len(old_topics)}   →  {CYAN}{len(new_topics)}{RESET}")
        for t in new_topics:
            print(f"      • {t}")

        # Batch size
        old_batch = old_cfg.get("summary_batch_size", 20)
        new_batch = new_cfg.get("summary_batch_size", old_batch)
        color = YELLOW if old_batch != new_batch else DIM
        print(f"  * Batch size   {old_batch}  →  {color}{new_batch}{RESET}")

        # Interval
        old_int = old_cfg.get("summary_interval_seconds", 300)
        new_int = new_cfg.get("summary_interval_seconds", old_int)
        color = YELLOW if old_int != new_int else DIM
        print(f"  * Interval     {old_int}s →  {color}{new_int}s{RESET}")

        # Title
        old_title = old_cfg.get("app_title", "")
        new_title = new_cfg.get("app_title", old_title)
        if old_title != new_title:
            print(f"  * Title        {old_title}")
            print(f"                 →  {CYAN}{new_title}{RESET}")
        else:
            print(f"  * Title        {DIM}{old_title} (unchanged){RESET}")

        # Stances
        old_stances = old_cfg.get("stances", [])
        new_stances = new_cfg.get("stances", old_stances)
        if old_stances != new_stances:
            print(f"  * Stances      {len(old_stances)}  →  {CYAN}{len(new_stances)}{RESET}")
            for s in new_stances:
                print(f"      • {s}")

        print()

    def write_config(new_cfg: dict):
        """Backup existing config and write new JSON."""
        if os.path.exists(CONFIG_PATH):
            backup_path = CONFIG_PATH + ".bak"
            shutil.copy2(CONFIG_PATH, backup_path)
            print(f"  {DIM}Backup saved to {os.path.basename(backup_path)}{RESET}")

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(new_cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"  {GREEN}{os.path.basename(CONFIG_PATH)} updated successfully.{RESET}")

    def write_alternate(new_cfg: dict):
        """Write to a _new.json file instead."""
        base, ext = os.path.splitext(CONFIG_PATH)
        alt_path = base + "_new" + ext
        os.makedirs(os.path.dirname(alt_path), exist_ok=True)
        with open(alt_path, "w") as f:
            json.dump(new_cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"  {GREEN}Saved to {os.path.basename(alt_path)}{RESET}")
        print(f"  {DIM}To use: mv {os.path.basename(alt_path)} {os.path.basename(CONFIG_PATH)}{RESET}")

    # ─── Main ─────────────────────────────────────────────────────────────────────

    async def _main():
        # Banner
        print(f"\n{BOLD}{'═' * 50}{RESET}")
        print(f"{BOLD}  Telegram Monitor — Configuration Wizard{RESET}")
        print(f"{DIM}  Powered by Claude{RESET}")
        print(f"{BOLD}{'═' * 50}{RESET}")

        # Phase 1: Read current config
        if os.path.exists(CONFIG_PATH):
            old_cfg = load_current_config()
            show_current_config(old_cfg)
        else:
            print(f"  {YELLOW}No existing config at {CONFIG_PATH} — creating new.{RESET}\n")
            old_cfg = {}

        # Phase 2: Conversational Q&A
        transcript = await run_conversation()

        if not transcript.strip():
            print(f"  {YELLOW}No input gathered. Exiting.{RESET}")
            sys.exit(0)

        # Phase 3: Generate new config JSON
        new_cfg = await generate_config(transcript)

        # Phase 4: Validate
        if not validate_config(new_cfg):
            print(f"  {RED}Generated config is incomplete. Aborting.{RESET}")
            sys.exit(1)

        # Phase 5: Show diff and confirm
        show_diff(old_cfg, new_cfg)

        try:
            answer = input(f"  Apply changes to {os.path.basename(CONFIG_PATH)}? [{GREEN}y{RESET}/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            sys.exit(0)

        if answer == "y":
            write_config(new_cfg)
            print(f"\n  {BOLD}Next steps:{RESET}")
            print(f"    1. Run {CYAN}python join_channels.py{RESET} to join new channels")
            print(f"    2. Run {CYAN}python monitor.py{RESET} to start monitoring")
            print()
        else:
            try:
                alt_answer = input(
                    f"  Save as alternate file instead? [{GREEN}y{RESET}/N] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Cancelled.")
                sys.exit(0)

            if alt_answer == "y":
                write_alternate(new_cfg)
            else:
                print(f"  {DIM}No changes written.{RESET}")
            print()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
