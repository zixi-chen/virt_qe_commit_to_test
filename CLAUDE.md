# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Virt QE Agent for Backport Analysis - A Python-based tool that analyzes patch files and generates test plans for RHEL Virt QE (Quality Engineering). The agent analyzes input patches, applies rules and expert knowledge, and generates comprehensive test plans including regression tests and new test case designs.

## Architecture

The project follows a modular Python package structure:

```
qe_agent/
├── cli.py          # Command-line interface and argument parsing
├── core.py         # Core analysis logic and LLM integration
├── tp_qemu_repo.py  # tp-qemu repository management (clone/pull)
├── config.py       # Configuration constants and rules
├── models.py       # Data models for commit records
└── __main__.py     # Package entry point
```

Key components:
- **Analysis Engine**: Uses OpenAI API to analyze patches and generate test plans
- **tp-qemu Integration**: Automatically manages test code repository synchronization
- **Rule-based System**: Applies retrieval rules and expert knowledge from `.cursorrules` and `.agent_context/`
- **Mapping Index**: Test-to-subsystem mapping in `.agent_context/mapping_index.json`

## Build System

- **Packaging**: setuptools with pyproject.toml
- **Dependencies**: openai, python-dotenv, tqdm, jsonlines
- **Entry Point**: `virt-qe-agent` command (via pyproject.toml scripts)

## Common Development Commands

```bash
# Setup development environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Run the agent (recommended)
python3 -m qe_agent --input ./patches/sample.patch --mode single --output test_plan_report.md

# Legacy entry point (still works)
python3 virt_qe_agent.py --input ./patches/sample.patch --mode single --output test_plan_report.md

# Regenerate mapping index
python3 generate_index.py

# Run with verbose output for debugging
python3 -m qe_agent --input ./patches/sample.patch --verbose

# Export Excel-friendly CSV
python3 -m qe_agent --input ./patches/sample.patch --excel-output test_plan_report.csv
```

## Configuration

- **Environment**: `.env` file for API keys and settings
- **Required Keys**: `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `MODEL_NAME`
- **tp-qemu Settings**: `TP_QEMU_GIT_URL`, `TP_QEMU_BRANCH`, `TP_QEMU_AUTO_SYNC`

## Key Files

- **`.cursorrules`**: Core analysis rules and constraints
- **`.agent_context/experience_base.md`**: Expert knowledge base
- **`.agent_context/mapping_index.json`**: Test-to-subsystem mapping
- **`tp-qemu/`**: Test code repository (auto-managed)

## Development Notes

- The agent requires internet access for DeepSeek API calls
- Test code repository (`tp-qemu`) is automatically synchronized
- Output format is Chinese markdown with specific section structure
- Supports both single-commit and cluster analysis modes
- CSV export available for Excel compatibility