> ## Documentation Index
> Fetch the complete documentation index at: https://docs.mem0.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Add Memories

> Add memories.

Add new facts, messages, or metadata to a user’s memory store. The Add Memories endpoint accepts either raw text or conversational turns and commits them asynchronously so the memory is ready for later search, retrieval, and graph queries.

## Endpoint

* **Method**: `POST`
* **URL**: `/v1/memories/`
* **Content-Type**: `application/json`

Memories are processed asynchronously by default. The response contains queued events you can track while the platform finalizes enrichment.

## Required headers

| Header                                | Required | Description                       |
| ------------------------------------- | -------- | --------------------------------- |
| `Authorization: Token <MEM0_API_KEY>` | Yes      | API key scoped to your workspace. |
| `Accept: application/json`            | Yes      | Ensures a JSON response.          |

## Request body

Provide at least one message or direct memory string. Most callers supply `messages` so Mem0 can infer structured memories as part of ingestion.

<CodeGroup>
  ```json Basic request theme={null}
  {
    "user_id": "alice",
    "messages": [
      { "role": "user", "content": "I moved to Austin last month." }
    ],
    "metadata": {
      "source": "onboarding_form"
    }
  }
  ```
</CodeGroup>

### Common fields

| Field           | Type                     | Required | Description                                                                                          |
| --------------- | ------------------------ | -------- | ---------------------------------------------------------------------------------------------------- |
| `user_id`       | string                   | No\*     | Associates the memory with a user. Provide when you want the memory scoped to a specific identity.   |
| `messages`      | array                    | No\*     | Conversation turns for Mem0 to infer memories from. Each object should include `role` and `content`. |
| `metadata`      | object                   | Optional | Custom key/value metadata (e.g., `{"topic": "preferences"}`).                                        |
| `infer`         | boolean (default `true`) | Optional | Set to `false` to skip inference and store the provided text as-is.                                  |
| `async_mode`    | boolean (default `true`) | Optional | Controls asynchronous processing. Most clients leave this enabled.                                   |
| `output_format` | string (default `v1.1`)  | Optional | Response format. `v1.1` wraps results in a `results` array.                                          |

> \* Provide at least one `messages` entry to describe what you are storing. For scoped memories, include `user_id`. You can also attach `agent_id`, `app_id`, `run_id`, `project_id`, or `org_id` to refine ownership.

## Response

Successful requests return an array of events queued for processing. Each event includes the generated memory text and an identifier you can persist for auditing.

<CodeGroup>
  ```json 200 response theme={null}
  [
    {
      "id": "mem_01JF8ZS4Y0R0SPM13R5R6H32CJ",
      "event": "ADD",
      "data": {
        "memory": "The user moved to Austin in 2025."
      }
    }
  ]
  ```

  ```json 400 response theme={null}
  {
    "error": "400 Bad Request",
    "details": {
      "message": "Invalid input data. Please refer to the memory creation documentation at https://docs.mem0.ai/platform/quickstart#4-1-create-memories for correct formatting and required fields."
    }
  }
  ```
</CodeGroup>

## Graph relationships

Add Memories can enrich the knowledge graph on write. Set `enable_graph: true` to create entity nodes and relationships for the stored memory. Use this when you want downstream `get_all` or search calls to traverse connected entities.

<CodeGroup>
  ```json Graph-aware request theme={null}
  {
    "user_id": "alice",
    "messages": [
      { "role": "user", "content": "I met with Dr. Lee at General Hospital." }
    ],
    "enable_graph": true
  }
  ```
</CodeGroup>

The response follows the same format, and related entities become available in [Graph Memory](/platform/features/graph-memory) queries.


## OpenAPI

````yaml post /v1/memories/
openapi: 3.0.1
info:
  title: Mem0 API Docs
  description: mem0.ai API Docs
  contact:
    email: deshraj@mem0.ai
  license:
    name: Apache 2.0
  version: v1
servers:
  - url: https://api.mem0.ai/
security:
  - ApiKeyAuth: []
paths:
  /v1/memories/:
    post:
      tags:
        - memories
      description: Add memories.
      operationId: memories_create
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/MemoryInput'
        required: true
      responses:
        '200':
          description: Successful memory creation.
          content:
            application/json:
              schema:
                type: array
                items:
                  type: object
                  properties:
                    id:
                      type: string
                    data:
                      type: object
                      properties:
                        memory:
                          type: string
                      required:
                        - memory
                    event:
                      type: string
                      enum:
                        - ADD
                        - UPDATE
                        - DELETE
                  required:
                    - id
                    - data
                    - event
        '400':
          description: >-
            Bad Request. Invalid input data. Please refer to the memory creation
            documentation at
            https://docs.mem0.ai/platform/quickstart#4-1-create-memories for
            correct formatting and required fields.
          content:
            application/json:
              schema:
                type: object
                required:
                  - error
                  - details
                example:
                  error: 400 Bad Request
                  details:
                    message: >-
                      Invalid input data. Please refer to the memory creation
                      documentation at
                      https://docs.mem0.ai/platform/quickstart#4-1-create-memories
                      for correct formatting and required fields.
      x-code-samples:
        - lang: Python
          source: >-
            # To use the Python SDK, install the package:

            # pip install mem0ai


            from mem0 import MemoryClient


            client = MemoryClient(api_key="your_api_key", org_id="your_org_id",
            project_id="your_project_id")


            messages = [
                {"role": "user", "content": "<user-message>"},
                {"role": "assistant", "content": "<assistant-response>"}
            ]


            client.add(messages, user_id="<user-id>", version="v2")
        - lang: JavaScript
          source: |-
            // To use the JavaScript SDK, install the package:
            // npm i mem0ai

            import MemoryClient from 'mem0ai';
            const client = new MemoryClient({ apiKey: "your-api-key" });

            const messages = [
              { role: "user", content: "Hi, I'm Alex. I'm a vegetarian and I'm allergic to nuts." },
              { role: "assistant", content: "Hello Alex! I've noted that you're a vegetarian and have a nut allergy. I'll keep this in mind for any food-related recommendations or discussions." }
            ];

            client.add(messages, { user_id: "<user_id>", version: "v2" })
              .then(result => console.log(result))
              .catch(error => console.error(error));
        - lang: cURL
          source: |-
            curl --request POST \
              --url https://api.mem0.ai/v1/memories/ \
              --header 'Authorization: Token <api-key>' \
              --header 'Content-Type: application/json' \
              --data '{
              "messages": [
                {}
              ],
              "agent_id": "<string>",
              "user_id": "<string>",
              "app_id": "<string>",
              "run_id": "<string>",
              "metadata": {},
              "includes": "<string>",
              "excludes": "<string>",
              "infer": true,
              "custom_categories": {}, 
              "org_id": "<string>",
              "project_id": "<string>",
              "version": "v2"
            }'
        - lang: Go
          source: "package main\n\nimport (\n\t\"fmt\"\n\t\"strings\"\n\t\"net/http\"\n\t\"io/ioutil\"\n)\n\nfunc main() {\n\n\turl := \"https://api.mem0.ai/v1/memories/\"\n\n\tpayload := strings.NewReader(\"{\n  \\\"messages\\\": [\n    {}\n  ],\n  \\\"agent_id\\\": \\\"<string>\\\",\n  \\\"user_id\\\": \\\"<string>\\\",\n  \\\"app_id\\\": \\\"<string>\\\",\n  \\\"run_id\\\": \\\"<string>\\\",\n  \\\"metadata\\\": {},\n  \\\"includes\\\": \\\"<string>\\\",\n  \\\"excludes\\\": \\\"<string>\\\",\n  \\\"infer\\\": true,\n  \\\"custom_categories\\\": {},\n  \\\"org_id\\\": \\\"<string>\\\",\n  \\\"project_id\\\": \\\"<string>\",\n  \\\"version\\\": \"v2\"\n}\")\n\n\treq, _ := http.NewRequest(\"POST\", url, payload)\n\n\treq.Header.Add(\"Authorization\", \"Token <api-key>\")\n\treq.Header.Add(\"Content-Type\", \"application/json\")\n\n\tres, _ := http.DefaultClient.Do(req)\n\n\tdefer res.Body.Close()\n\tbody, _ := ioutil.ReadAll(res.Body)\n\n\tfmt.Println(res)\n\tfmt.Println(string(body))\n\n}"
        - lang: PHP
          source: |-
            <?php

            $curl = curl_init();

            curl_setopt_array($curl, [
              CURLOPT_URL => "https://api.mem0.ai/v1/memories/",
              CURLOPT_RETURNTRANSFER => true,
              CURLOPT_ENCODING => "",
              CURLOPT_MAXREDIRS => 10,
              CURLOPT_TIMEOUT => 30,
              CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,
              CURLOPT_CUSTOMREQUEST => "POST",
              CURLOPT_POSTFIELDS => "{
              \"messages\": [
                {}
              ],
              \"agent_id\": \"<string>\",
              \"user_id\": \"<string>\",
              \"app_id\": \"<string>\",
              \"run_id\": \"<string>\",
              \"metadata\": {},
              \"includes\": \"<string>\",
              \"excludes\": \"<string>\",
              \"infer\": true,
              \"custom_categories\": {}, 
              \"org_id\": \"<string>\",
              \"project_id\": \"<string>",
              \"version\": "v2"
            }",
              CURLOPT_HTTPHEADER => [
                "Authorization: Token <api-key>",
                "Content-Type: application/json"
              ],
            ]);

            $response = curl_exec($curl);
            $err = curl_error($curl);

            curl_close($curl);

            if ($err) {
              echo "cURL Error #:" . $err;
            } else {
              echo $response;
            }
        - lang: Java
          source: >-
            HttpResponse<String> response =
            Unirest.post("https://api.mem0.ai/v1/memories/")
              .header("Authorization", "Token <api-key>")
              .header("Content-Type", "application/json")
              .body("{
              \"messages\": [
                {}
              ],
              \"agent_id\": \"<string>\",
              \"user_id\": \"<string>\",
              \"app_id\": \"<string>\",
              \"run_id\": \"<string>\",
              \"metadata\": {},
              \"includes\": \"<string>\",
              \"excludes\": \"<string>\",
              \"infer\": true,
              \"custom_categories\": {}, 
              \"org_id\": \"<string>\",
              \"project_id\": \"<string>",
              \"version\": "v2"
            }")
              .asString();
components:
  schemas:
    MemoryInput:
      type: object
      properties:
        messages:
          description: >-
            An array of message objects representing the content of the memory.
            Each message object typically contains 'role' and 'content' fields,
            where 'role' indicates the sender either 'user' or 'assistant' and
            'content' contains the actual message text. This structure allows
            for the representation of conversations or multi-part memories.
          type: array
          items:
            type: object
            additionalProperties:
              type: string
              nullable: true
        agent_id:
          description: The unique identifier of the agent associated with this memory.
          title: Agent id
          type: string
          nullable: true
        user_id:
          description: The unique identifier of the user associated with this memory.
          title: User id
          type: string
          nullable: true
        app_id:
          description: >-
            The unique identifier of the application associated with this
            memory.
          title: App id
          type: string
          nullable: true
        run_id:
          description: The unique identifier of the run associated with this memory.
          title: Run id
          type: string
          nullable: true
        metadata:
          description: >-
            Additional metadata associated with the memory, which can be used to
            store any additional information or context about the memory. Best
            practice for incorporating additional information is through
            metadata (e.g. location, time, ids, etc.). During retrieval, you can
            either use these metadata alongside the query to fetch relevant
            memories or retrieve memories based on the query first and then
            refine the results using metadata during post-processing.
          title: Metadata
          type: object
          properties: {}
          nullable: true
        includes:
          description: String to include the specific preferences in the memory.
          title: Includes
          minLength: 1
          type: string
          nullable: true
        excludes:
          description: String to exclude the specific preferences in the memory.
          title: Excludes
          minLength: 1
          type: string
          nullable: true
        infer:
          description: Whether to infer the memories or directly store the messages.
          title: Infer
          type: boolean
          default: true
        output_format:
          description: >-
            Controls the response format structure. `v1.0` (deprecated) returns
            a direct array of memory objects: `[{...}, {...}]`. `v1.1`
            (recommended) returns an object with a 'results' key containing the
            array: `{"results": [...]}`. The `v1.0` format will be removed in
            future versions.
          title: Output format
          type: string
          nullable: true
          default: v1.1
        custom_categories:
          description: A list of categories with category name and its description.
          title: Custom categories
          type: object
          properties: {}
          nullable: true
        custom_instructions:
          description: >-
            Defines project-specific guidelines for handling and organizing
            memories. When set at the project level, they apply to all new
            memories in that project.
          title: Custom instructions
          type: string
          nullable: true
        immutable:
          description: Whether the memory is immutable.
          title: Immutable
          type: boolean
          default: false
        async_mode:
          description: Whether to add the memory completely asynchronously.
          title: Async mode
          type: boolean
          default: true
        timestamp:
          description: 'The timestamp of the memory. Format: Unix timestamp'
          title: Timestamp
          type: integer
          nullable: true
        expiration_date:
          description: 'The date and time when the memory will expire. Format: YYYY-MM-DD'
          title: Expiration date
          type: string
          nullable: true
        org_id:
          description: >-
            The unique identifier of the organization associated with this
            memory.
          title: Organization id
          type: string
          nullable: true
        project_id:
          description: The unique identifier of the project associated with this memory.
          title: Project id
          type: string
          nullable: true
        version:
          description: >-
            The version of the memory to use. The default version is v1, which
            is deprecated. We recommend using v2 for new applications.
          title: Version
          type: string
          nullable: true
  securitySchemes:
    ApiKeyAuth:
      type: apiKey
      in: header
      name: Authorization
      description: >-
        API key authentication. Prefix your Mem0 API key with 'Token '. Example:
        'Token your_api_key'

````

Built with [Mintlify](https://mintlify.com).