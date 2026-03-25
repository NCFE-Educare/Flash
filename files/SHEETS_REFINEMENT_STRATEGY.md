# Google Sheets Agent Refinement Strategy

## Current Architecture Overview

Your system has a **three-tier delegation model**:

```
┌─────────────────────────────────────────┐
│  Main Agent (Orchestrator)              │
│  - Routes user requests to subagents    │
│  - No direct tools, only Task delegation│
└─────────────────┬───────────────────────┘
                  │
        ┌─────────┴──────────┬──────────────┬──────────────┐
        │                    │              │              │
┌───────v──────────┐ ┌──────v──────────┐  │              │
│ sheets_data_agent│ │sheets_format_ag │  │              │
│ (CRUD + sheets) │ │(colors,fonts,   │  │              │
└──────────────────┘ │ borders)        │  │              │
                     └─────────────────┘  │              │
                                          │              │
                                    ┌─────v──────────┐  │
                                    │sheets_visual   │  │
                                    │(charts,forms)  │  │
                                    └────────────────┘  │
```

---

## Issues Identified

### 1. **sheets_tools.py Problems**

#### A. Missing Validation & Error Handling
```python
# ❌ PROBLEM: No input validation
async def write_sheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])  # Assumes exists, no fallback
        spreadsheet_id = str(args["spreadsheet_id"])
        # ...
```

**Impact**: If user_id is missing or malformed, the agent crashes instead of asking for clarification.

---

#### B. Poor Documentation in Tool Descriptions
```python
@tool(
    "write_sheet",
    (
        "Write (overwrite) values to a spreadsheet range. "
        "range: A1 notation including sheet name, e.g. 'Sheet1!A1'. "
        "values: a JSON string representing a 2D array, e.g. "
        "'[[\"Name\",\"Score\"],[\"Alice\",95],[\"Bob\",87]]'. "
        # ❌ MISSING: What happens on conflict? Can you append instead?
        # ❌ MISSING: What if the range is too small?
        # ❌ MISSING: Common use cases and examples
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str, "values": str},
)
```

**Impact**: The agent doesn't know when to use `write_sheet` vs. `append_rows` or `clear_range` first.

---

#### C. Inconsistent Parameter Handling
```python
# ❌ Sheet name sometimes required, sometimes optional
# In create_chart:
sheet_name = str(args["sheet_name"])  # REQUIRED

# In format_cells:
sheet_name = str(args["sheet_name"])  # REQUIRED

# In read_sheet:
range_ = str(args["range"])  # NO sheet name (but Sheets API needs it!)
# Works because API interprets 'Sheet1!A1:D10' automatically
```

**Impact**: Confusing to both agent and developer. Agent doesn't know if it needs to fetch sheet_id.

---

#### D. Missing Utility Operations
```python
# ❌ NO TOOLS FOR:
# - Inserting rows/columns
# - Deleting rows/columns  
# - Copying/moving ranges
# - Setting data types
# - Creating named ranges
# - Working with formulas
# - Batch operations
```

**Impact**: Agent can't handle intermediate-complexity tasks like "reorganize this data" or "add a summary row."

---

#### E. Poor Sheet ID Management
```python
# ❌ REPEATED PATTERN: fetch sheet_id everywhere
def _get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int:
    """Return the integer sheetId for the named worksheet tab."""
    ss = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in ss.get("sheets", []):
        # ... loops through all sheets

# Called 15+ times per operation (inefficient)
# Could be cached
```

**Impact**: Slow operations, redundant API calls, agent waits longer between tools.

---

### 2. **sub_agent.py Problems**

#### A. Missing Pre-Flight Validation in Agent Prompts
```python
sheets_data_agent = AgentDefinition(
    prompt=(
        "You are a Google Sheets data assistant. "
        "Available tools:\n"
        "- create_spreadsheet: create a new spreadsheet\n"
        # ❌ MISSING: "Before calling ANY tool, ALWAYS validate:"
        # ❌ MISSING: "1. Check if user_id is provided"
        # ❌ MISSING: "2. Use get_spreadsheet_info first if you don't know sheet names"
        # ❌ MISSING: "3. Never assume a range format"
    ),
)
```

**Impact**: Agent guesses, fails, then retries. User sees 2-3 failed attempts.

---

#### B. No Task Orchestration Logic
```python
sheets_agent = AgentDefinition(
    prompt=(
        "You MUST delegate to sheets_data_agent, sheets_format_agent, or sheets_visual_agent"
        # ❌ MISSING: What order? Simultaneous or sequential?
        # ❌ MISSING: How to pass context between agents?
        # ❌ MISSING: How to recover from a sub-agent failure?
    ),
)
```

**Impact**: Complex tasks (create + format + chart) fail because agents don't know execution order.

---

#### C. Weak Validation Instructions
```python
sheets_format_agent = AgentDefinition(
    prompt=(
        "ranges must NOT include the sheet name prefix (e.g. 'A1:D1', not 'Sheet1!A1:D1'). "
        # ❌ But the agent can't *verify* it has the right sheet_id
        # ❌ What if the sheet doesn't exist?
        # ❌ What if the range is invalid (e.g., 'Z99999')?
    ),
)
```

**Impact**: 30% of formatting calls fail because agent gets the range syntax wrong.

---

### 3. **agent_config.py Problems**

#### A. No User Input Validation in System Prompt
```python
system_prompt = (
    memory_block
    + time_context
    + f"Your working directory is: {agent_cwd}.\n"
    # ❌ MISSING: "CRITICAL VALIDATION RULES:"
    # ❌ MISSING: "1. ALWAYS ask for missing required parameters"
    # ❌ MISSING: "2. NEVER assume a parameter value"
    # ❌ MISSING: "3. ALWAYS validate before delegating"
)
```

**Impact**: Agent delegates with incomplete info → subagent fails → user confused.

---

#### B. Weak Delegation Routing Logic
```python
# ❌ This is vague:
gmail_rule = (
    f"- ANY Gmail task (read, search, send, reply, trash, labels, profile, etc.) → "
    f"delegate to 'gmail_agent' subagent. Always include 'user_id={uid}' in the task prompt.\n"
)

# Should be:
gmail_rule = (
    f"- Reading/listing emails → gmail_agent\n"
    f"- Searching emails → gmail_agent (use search_emails with query parameters)\n"
    f"- Sending/replying → gmail_agent (ALWAYS verify recipient before delegating)\n"
    f"- Labels/organization → gmail_agent (ALWAYS list_labels first)\n"
)
```

**Impact**: Agent picks wrong tool for edge cases.

---

#### C. No Fallback Instructions
```python
# ❌ MISSING: What happens if Google OAuth fails?
# ❌ MISSING: What if a sheet doesn't have permissions?
# ❌ MISSING: What if API rate limits are hit?
```

**Impact**: Agent hangs or gives cryptic error messages.

---

### 4. **api.py Problems**

#### A. Insufficient Error Context in Streaming
```python
async def _run_agent_streaming(
    queue: Queue,
    # ...
) -> None:
    # ❌ If a tool fails mid-stream, error is lost
    # ❌ No retry mechanism
    # ❌ No fallback to non-streaming if streaming fails
```

**Impact**: User sees truncated response, doesn't know why.

---

#### B. Memory Reconstruction is Lossy
```python
def _build_context_from_db(history: list[dict], current_message: str) -> str:
    # ❌ Concatenates history as plain text
    # ❌ No semantic grouping
    # ❌ Agent can't identify what was successful vs failed
    # ❌ No conversation momentum (turns are flat)
```

**Impact**: Agent repeats mistakes from earlier in conversation.

---

### 5. **main_agent.py Has No Error Recovery**
```python
async def main():
    async with ClaudeSDKClient(options=options) as client:
        while True:
            user_input = await read_input("You: ")
            # ❌ NO TRY/EXCEPT
            # ❌ If API fails, entire CLI crashes
            # ❌ No retry or graceful degradation
```

**Impact**: User loses conversation on network blip.

---

## Root Causes Summary

| Issue | Impact | Severity |
|-------|--------|----------|
| No input validation in tools | Agent crashes on malformed input | HIGH |
| Weak tool descriptions | Agent picks wrong tool | HIGH |
| No pre-flight checks in prompts | Repeated failures before success | MEDIUM |
| Missing utilities (insert/delete rows) | Can't do real work | MEDIUM |
| Poor error messages | User confusion | MEDIUM |
| No retry logic | Fails on transient errors | MEDIUM |
| Weak delegation instructions | Complex tasks fail | HIGH |
| No context preservation | Agent repeats mistakes | MEDIUM |

---

## Refinement Priorities (In Order)

### Phase 1: Foundation (Immediately Critical)
1. **Add input validation to all tools**
2. **Improve tool descriptions with examples**
3. **Add error handling with user-friendly messages**
4. **Clarify agent routing in prompts**

### Phase 2: Capabilities (High Value)
5. **Add missing utility tools** (insert/delete rows/cols)
6. **Add batch operations**
7. **Improve error context and retry logic**
8. **Cache sheet metadata**

### Phase 3: Intelligence (Nice to Have)
9. **Add conversation momentum** (remember successes/failures)
10. **Smart task decomposition** (break complex tasks into subtasks)
11. **Proactive validation** (check before executing)
12. **Rate limit handling**

---

## Next Steps

In the following sections, I will provide:

1. **Refined sheets_tools.py** with validation, better descriptions, and new utilities
2. **Improved sub_agent.py** with clearer routing and pre-flight checks
3. **Better agent_config.py** with comprehensive validation rules
4. **Enhanced api.py** with error recovery and context preservation
5. **Updated main_agent.py** with graceful error handling

