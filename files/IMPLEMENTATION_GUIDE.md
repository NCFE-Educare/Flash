# Implementation Guide: Refining Your Sheets Agent System

## Phase 1: Foundation (Days 1-2)

### Step 1: Update sheets_tools.py

**What to do:**
1. Add input validation function `_validate_required()` to all tools
2. Improve error messages (include "how to fix" in error text)
3. Add metadata caching to avoid repeated API calls
4. Add new utility tools: `insert_rows`, `delete_rows`, `insert_columns`, `delete_columns`

**Key changes:**
```python
# BEFORE (❌ no validation)
async def write_sheet(args: dict[str, Any]) -> dict[str, Any]:
    user_id = int(args["user_id"])  # Crashes if missing

# AFTER (✓ validates)
async def write_sheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "range", "values")
        user_id = int(args["user_id"])
        # ...
```

**Checklist:**
- [ ] Add `_validate_required()` function
- [ ] Add `_validate_a1_notation()` function
- [ ] Add `_validate_full_a1_notation()` function
- [ ] Replace `_get_sheet_id()` with `_get_sheet_id_cached()`
- [ ] Add `_clear_cache()` function
- [ ] Update all tool descriptions with examples
- [ ] Add `insert_rows` tool
- [ ] Add `delete_rows` tool
- [ ] Add `insert_columns` tool
- [ ] Add `delete_columns` tool
- [ ] Test each tool independently with sample user_id/sheet

---

### Step 2: Update sub_agent.py

**What to do:**
1. Replace vague prompts with detailed pre-flight checklists
2. Add "tool selection guide" (when to use which tool)
3. Add workflow examples
4. Add error recovery instructions

**Key sections to add:**
```
═══════════════════════════════════════════════════════════════════════════
CRITICAL PRE-FLIGHT CHECKLIST (ALWAYS do this BEFORE calling tools)
═══════════════════════════════════════════════════════════════════════════

Before executing ANY data operation:
1. ✓ Check that user_id is provided and valid
2. ✓ If spreadsheet_id is needed, confirm it was provided
3. ✓ If working with a specific sheet, call get_spreadsheet_info FIRST
...
```

**Checklist:**
- [ ] Rewrite `sheets_data_agent` prompt with pre-flight checklist
- [ ] Add "TOOL SELECTION GUIDE" section
- [ ] Add "PARAMETER GOTCHAS" section
- [ ] Add "ERROR RECOVERY" section
- [ ] Add "WORKFLOW EXAMPLES" section
- [ ] Rewrite `sheets_format_agent` with same pattern
- [ ] Rewrite `sheets_visual_agent` with same pattern
- [ ] Test by simulating an agent thinking through a task

---

### Step 3: Update agent_config.py

**What to do:**
1. Add user input validation rules to system prompt
2. Add fallback instructions
3. Add rate limit handling

**Key addition:**
```python
system_prompt = (
    # ... existing ...
    "\n=== CRITICAL VALIDATION RULES ===\n"
    "1. ALWAYS ask for missing required parameters\n"
    "2. NEVER assume a parameter value\n"
    "3. ALWAYS validate before delegating\n"
    "4. If authentication fails, provide reconnection link\n"
)
```

**Checklist:**
- [ ] Add validation rules section to system prompt
- [ ] Add fallback strategies (what to do on API failure)
- [ ] Add rate limit instructions
- [ ] Clarify delegation routing for sheets
- [ ] Test system prompt readability

---

## Phase 2: Capabilities (Days 3-4)

### Step 4: Add Error Recovery to api.py

**What to do:**
1. Add try/except to `_run_agent()` with graceful fallback
2. Add error context to streaming responses
3. Add retry logic for transient failures

**Pattern:**
```python
async def _run_agent(...) -> tuple[str, str | None]:
    # PRIMARY: native SDK session resumption
    if claude_session_id:
        try:
            return await _execute(_make_options(claude_session_id), effective_message)
        except SpecificError as e:
            print(f"[Memory] SDK resume failed: {e}")
            # FALLBACK: rebuild from DB
    
    # FALLBACK: full DB history
    full_message = _build_context_from_db(history, effective_message)
    return await _execute(_make_options(None), full_message)
```

**Checklist:**
- [ ] Add try/except wrapper around primary session resumption
- [ ] Add detailed error logging
- [ ] Add fallback to DB history reconstruction
- [ ] Test with expired session IDs
- [ ] Test with missing transcript files

---

### Step 5: Improve Memory Reconstruction in api.py

**What to do:**
1. Make `_build_context_from_db()` smarter
2. Include success/failure indicators
3. Group related turns

**Checklist:**
- [ ] Preserve conversation structure (not just flat list)
- [ ] Mark tool successes vs failures
- [ ] Include image/document references
- [ ] Test with long conversations (50+ messages)

---

### Step 6: Add Main Agent Error Handling

**What to do:**
1. Add try/except to CLI main loop
2. Add graceful reconnection on failure

**Pattern:**
```python
async def main():
    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                user_input = await read_input("You: ")
                if not user_input.strip():
                    continue
                await client.query(user_input)
                async for message in client.receive_response():
                    process_message(message)
            except KeyboardInterrupt:
                print("Goodbye!")
                break
            except Exception as e:
                print(f"Error: {e}. Retrying...")
                continue
```

**Checklist:**
- [ ] Add try/except to main loop
- [ ] Handle KeyboardInterrupt gracefully
- [ ] Handle network errors with retry
- [ ] Test with simulated network failures

---

## Phase 3: Intelligence (Days 5-6)

### Step 7: Smart Task Decomposition

**What to do:**
1. Add a "task analyzer" step before delegating
2. Identify multi-step tasks and break them down
3. Plan execution order

**Pattern:**
```
User: "Create a sales spreadsheet with data, format it, and add a chart"

Agent analysis:
  1. Task type: COMPLEX (multi-service)
  2. Sub-tasks identified:
     - Create spreadsheet + write data → sheets_data_agent
     - Format header row → sheets_format_agent
     - Create chart → sheets_visual_agent
  3. Execution order: Data → Format → Visual
  4. Context to pass forward: spreadsheet_id, sheet_names
```

**Checklist:**
- [ ] Add task analysis to main agent prompt
- [ ] Identify compound tasks automatically
- [ ] Plan execution order explicitly
- [ ] Test with 3+ step tasks

---

### Step 8: Proactive Validation

**What to do:**
1. Before delegating, verify all required params exist
2. Fetch metadata if needed
3. Warn user of potential issues

**Pattern:**
```python
# Agent checks:
"I need to format cells in 'Sales!A1:D10'.\n"
"Let me first verify this sheet exists..."
# → calls get_spreadsheet_info
"✓ Sheet 'Sales' found. Proceeding with formatting."
```

**Checklist:**
- [ ] Add pre-delegation validation step
- [ ] Cache metadata to avoid redundant API calls
- [ ] Warn user of non-fatal issues
- [ ] Test with non-existent sheets

---

## Testing Checklist

### Unit Tests (For Each Tool)

```python
def test_write_sheet_missing_user_id():
    """Should return error, not crash"""
    result = asyncio.run(write_sheet({"spreadsheet_id": "X"}))
    assert "user_id" in result["content"][0]["type"]

def test_write_sheet_valid():
    """Should successfully write data"""
    result = asyncio.run(write_sheet({
        "user_id": 123,
        "spreadsheet_id": "abc123",
        "range": "Sheet1!A1",
        "values": '[[1, 2], [3, 4]]'
    }))
    assert "✓" in result["content"][0]["type"]
```

**Checklist:**
- [ ] Test all tools with missing required params
- [ ] Test all tools with invalid params
- [ ] Test all tools with valid params
- [ ] Test error messages are user-friendly

---

### Integration Tests (Agent Workflows)

```
Test 1: Create spreadsheet
  → Verify spreadsheet_id returned
  → Verify URL is valid
  → Verify sheet names included

Test 2: Write and read data
  → Create sheet
  → Write data
  → Read data back
  → Verify matches

Test 3: Format header row
  → Get spreadsheet info
  → Format cells
  → Verify colors applied

Test 4: Multi-step task (Create + Format + Chart)
  → Execute sheets_data_agent (create + write)
  → Pass spreadsheet_id to sheets_format_agent
  → Format header row
  → Pass spreadsheet_id to sheets_visual_agent
  → Create chart
  → Verify all succeed
```

**Checklist:**
- [ ] Test single-step workflows (5 tests)
- [ ] Test multi-step workflows (3 tests)
- [ ] Test error recovery (2 tests)
- [ ] Test with real Google Sheets account

---

## Rollout Plan

### Day 1-2: Foundation
- [ ] Deploy refined sheets_tools.py
- [ ] Update sub_agent.py prompts
- [ ] Test tools individually

### Day 3-4: Capabilities
- [ ] Deploy error recovery to api.py
- [ ] Test streaming with error scenarios
- [ ] Deploy main_agent.py improvements

### Day 5-6: Intelligence
- [ ] Deploy task decomposition
- [ ] Deploy proactive validation
- [ ] Full end-to-end testing

### Day 7: Monitoring
- [ ] Monitor error rates
- [ ] Collect user feedback
- [ ] Fix any issues found

---

## Success Metrics

| Metric | Before | Target | How to Measure |
|--------|--------|--------|-----------------|
| Tool Success Rate | 70% | 95% | % of tool calls that succeed on first try |
| Error Message Quality | Poor | Clear | User feedback on error clarity |
| Multi-Step Task Success | 40% | 85% | % of multi-step tasks that complete |
| Time to Success | 3-4 attempts | 1-2 attempts | Average retries per task |
| User Satisfaction | Medium | High | Survey after each task |

---

## Quick Reference: What Changed

### sheets_tools.py
- **Added**: Input validation functions
- **Added**: Metadata caching
- **Added**: New utility tools (insert/delete rows/cols)
- **Improved**: Error messages
- **Improved**: Tool descriptions with examples

### sub_agent.py
- **Added**: Pre-flight checklists
- **Added**: Tool selection guides
- **Added**: Workflow examples
- **Added**: Error recovery instructions
- **Improved**: Clarity of when to use which tool

### agent_config.py
- **Added**: Validation rules to system prompt
- **Added**: Fallback strategies
- **Improved**: Delegation routing clarity

### api.py
- **Added**: Error recovery for session resumption
- **Added**: Better error context in streaming
- **Improved**: Memory reconstruction

### main_agent.py
- **Added**: Try/except to main loop
- **Added**: Graceful error handling
- **Improved**: User experience on network failures

---

## Next Steps

1. **Review**: Share refined files with team for feedback
2. **Test**: Run unit tests on all tools
3. **Integrate**: Merge refined files into main codebase
4. **Deploy**: Roll out to staging environment first
5. **Monitor**: Watch error rates and user feedback
6. **Iterate**: Fix issues found during monitoring

---

## Questions & Troubleshooting

**Q: How long will this take?**
A: 5-7 days for full implementation + testing.

**Q: Will users see any downtime?**
A: No, if you deploy to staging first and test thoroughly.

**Q: What if something breaks?**
A: Revert to previous version. Fallback to DB history reconstruction is built-in.

**Q: How do I know if it's working?**
A: Monitor success rates. Target 95% on first try.

**Q: Can I do this incrementally?**
A: Yes, Phase 1 is self-contained and can be deployed alone.

