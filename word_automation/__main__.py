"""
CLI entry point for word_automation.

Usage:
  # Run a custom prompt directly:
  python -m word_automation "Create a Word doc with title 'Sales Report' and a 3x3 table"

  # Interactive mode:
  python -m word_automation
"""
from __future__ import annotations

import argparse
import json
import sys
from .config import AutomationConfig
from .pipeline import WordAutomationPipeline


def main():
    parser = argparse.ArgumentParser(
        description="3-Layer Word Automation System (Nemotron + python-docx + Osaurus AppleScript)"
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="The automation instruction or prompt to execute",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress verbose progress logs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON results",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override Nemotron model",
    )

    args = parser.parse_args()

    config = AutomationConfig.from_env()
    if args.model:
        config.nemotron_model = args.model

    pipeline = WordAutomationPipeline(config=config)

    prompt_text = " ".join(args.prompt).strip()

    # Interactive mode if no prompt provided
    if not prompt_text:
        print("=" * 60)
        print("🤖 Word Automation Interactive Shell")
        print("Enter your custom task prompt below (or 'exit' / 'quit'):")
        print("=" * 60)
        while True:
            try:
                user_input = input("\n📝 Prompt > ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    print("Exiting.")
                    break

                result = pipeline.execute(user_input, verbose=not args.quiet)
                if args.json:
                    print("\nJSON Result:")
                    print(json.dumps(result, indent=2, default=str))

            except (KeyboardInterrupt, EOFError):
                print("\nExiting.")
                break
        return

    # Single prompt execution
    result = pipeline.execute(prompt_text, verbose=not args.quiet)
    if args.json:
        print("\nJSON Result:")
        print(json.dumps(result, indent=2, default=str))

    if result["status"] == "failure":
        sys.exit(1)


if __name__ == "__main__":
    main()
