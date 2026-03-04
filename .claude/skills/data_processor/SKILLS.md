---
description: Standard team procedure for processing text data files.
---

When the user asks you to read, process, or summarize mock data files (e.g. report.txt, any .txt file with data), you MUST:
1. Delegate to the `data_processor` subagent via the Task tool. Do NOT use read_mock_data yourself.
2. Once the subagent returns the summary, output the final result to the user as a neat, bulleted list.